"""Clause library: the wording drafts are assembled from.

Rows (clause_library)
  seed      canonical wording shipped with the code (rci/seed/clauses.yaml), data_class any, to_verify
  mined     one row per distinct delexicalised sentence found in precedent letters / orders (status candidate)
  curated   a mined (or new) row an officer made canonical, with Jinja slots, rule-set tags and conditions

Mining (`mine`) — precedents only (role precedent / unassigned, approved + locked card; never holdouts):
  1. split   each QUERY_LETTER / DEMAND_LETTER / COLLECTOR_ORDER / OFFICE_NOTE exhibit's redacted page text into
             clauses by the letter structure (header up to विषय / आदेश, numbered items, ज्याअर्थी / त्याअर्थी, अटी, प्रत …);
             each gets a clause key (query-letter items: query_item.<CODE> from the card's reviewed item → code mapping)
  2. delex   the case's own facts are replaced by slots (society → {{ society_name }}, dates → {{ x|mr_date }}, amounts
             → {{ premium|inr|mr_num }}, …). Digits left over mark the row needs_slots; PII tokens mark it has_pii —
             neither can become canonical until the curator fixes the template (validate_template).
  3. embed   the delexicalised wording (same embedding client and residency guard as the case facets)
  4. cluster greedy leader clustering per (data class, kind) at cosine ≥ clauses.cluster_threshold
Drafts pick, per clause key, the most specific canonical row whose conditions hold and whose rule-set tags intersect
the case's rule stack (`pick`).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable

from jinja2 import StrictUndefined, TemplateSyntaxError, meta, nodes
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from rci.drafting.mrtext import FILTERS, Blank, inr, mr_date, mr_num, to_date
from rci.pipeline.normalise import ascii_digits, devanagari_digits
from rci.pipeline.redact.vault import TOKEN_RE

KINDS = ("scrutiny_note", "query_letter", "demand_letter", "collector_order")
KIND_OF_EXHIBIT = {"QUERY_LETTER": "query_letter", "DEMAND_LETTER": "demand_letter", "COLLECTOR_ORDER": "collector_order",
                   "OFFICE_NOTE": "scrutiny_note"}
CONDITION_KEYS = ("tenure", "validator_failed", "premium_paid", "has_query_letter", "fee_status")

# slot -> (description, example value). Every canonical template may use only these (+ ITEM_SLOTS in query items).
SLOTS: dict[str, tuple[str, Any]] = {
    "case_ref": ("this case's file number", "SR-45/2024"),
    "society_name": ("applicant society", "सुयोग सहकारी गृहनिर्माण संस्था मर्यादित"),
    "registration_no": ("society registration no.", "PNA/HSG/(TC)/1234/1987"),
    "village": ("village (मौजे)", "कोथरूड"),
    "taluka": ("taluka", "हवेली"),
    "district": ("district", "पुणे"),
    "cts_no": ("CTS / survey no.", "1234/अ"),
    "area_sqm": ("area on record, sq m", "1250.50"),
    "tenure_mr": ("current tenure in Marathi", "भोगवटादार वर्ग-२"),
    "application_date": ("application date", "2024-02-05"),
    "property_card_date": ("issue date of the latest property card", "2023-06-10"),
    "sgm_date": ("special general meeting date", "2024-01-20"),
    "lease_date": ("lease deed date", "1975-04-01"),
    "query_date": ("date of this office's (first) query letter", "2024-03-11"),
    "reply_date": ("date of the society's reply", "2024-04-02"),
    "inspection_date": ("site inspection date", "2024-04-15"),
    "inspection_area": ("area measured at inspection, sq m", "1250.50"),
    "ctso_date": ("CTSO measurement date", "2024-04-25"),
    "ctso_area": ("CTSO-measured area, sq m", "1250.50"),
    "demand_date": ("demand letter date", "2024-05-20"),
    "premium": ("premium due (demand letter, else the fee engine's total)", "4564325"),
    "asr_year": ("ASR year (1 April start)", 2024),
    "asr_rate": ("ASR rate, ₹ per sq m", "73000"),
    "rate_pct": ("premium rate, %", "5"),
    "paid_amount": ("premium paid by challan", "4564325"),
    "payment_date": ("challan payment date", "2024-06-10"),
    "grn": ("challan GRN", "MH004512345678202425"),
    "order_no": ("order no.", "महसूल/कक्ष-२/आदेश/SR-45/2024"),
    "order_date": ("order date", "2024-06-28"),
    "reply_days": ("days allowed to comply with a query", 30),
    "payment_days": ("days allowed to pay", 30),
    "head_of_account": ("head of account for the premium", "0029-00-800-01"),
    "collector_title": ("the Collector's title", "जिल्हाधिकारी, पुणे"),
    "rule_name_mr": ("Marathi name of the base rules in force", "महाराष्ट्र जमीन महसूल (…) नियम, २०१९"),
    "office_name_mr": ("office name", "जिल्हाधिकारी कार्यालय (पायलट)"),
    "today": ("date of the draft", "2026-09-27"),
    "narrative": ("narrative recital (numbers / dates / IDs cross-checked against the facts)", "…"),
}
ITEM_SLOTS: dict[str, tuple[str, Any]] = {
    "requirement_name_mr": ("the requirement's Marathi name", "अद्ययावत मिळकत पत्रिका"),
    "requirement_code": ("the requirement code", "PROPERTY_CARD_RECENT"),
}
DIGITS = re.compile(r"[0-9०-९]")
ALLOWED_DIGIT_PHRASES = re.compile(r"(?:वर्ग|कक्ष)-[0-9०-९]")  # "भोगवटादार वर्ग-१" is a name, not a number
JINJA_BLOCK = re.compile(r"{{.*?}}|{%.*?%}|{#.*?#}", re.S)
MARKERS = ("<NUM>", "<PII>")


class ClauseError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ------------------------------------------------------------------------------------------------
# Rendering and validation
# ------------------------------------------------------------------------------------------------
@lru_cache
def env() -> SandboxedEnvironment:
    e = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    e.filters.update(FILTERS)
    return e


@lru_cache(maxsize=2048)
def _compiled(template: str):
    return env().from_string(template)


@lru_cache(maxsize=2048)
def variables(template: str) -> frozenset[str]:
    return frozenset(meta.find_undeclared_variables(env().parse(template)))


def _filters_used(template: str) -> set[str]:
    return {n.name for n in env().parse(template).find_all(nodes.Filter)}


def tidy(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def render(template: str, slots: dict) -> tuple[str, list[str]]:
    """(text, missing slots). A missing / empty slot renders as a blank line to fill (and is reported)."""
    ctx, missing = {}, []
    for name in variables(template):
        v = slots.get(name)
        if v is None or v == "" or isinstance(v, Blank):
            ctx[name], _ = Blank(name), missing.append(name)
        else:
            ctx[name] = v
    return tidy(_compiled(template).render(**ctx)), sorted(missing)


def allowed_slots(key: str) -> set[str]:
    return set(SLOTS) | (set(ITEM_SLOTS) if key.startswith("query_item.") else set())


def literal_digits(template: str) -> list[str]:
    text = ALLOWED_DIGIT_PHRASES.sub("", JINJA_BLOCK.sub(" ", template))
    return DIGITS.findall(text)


def validate_template(template: str, *, key: str) -> list[str]:
    """Why a template cannot be canonical (empty list = fine)."""
    errors = []
    try:
        env().parse(template)
    except TemplateSyntaxError as e:
        return [f"template syntax: {e.message} (line {e.lineno})"]
    unknown = set(variables(template)) - allowed_slots(key)
    if unknown:
        errors.append(f"unknown slots: {', '.join(sorted(unknown))}")
    bad_filters = _filters_used(template) - set(FILTERS)
    if bad_filters:
        errors.append(f"filters not allowed: {', '.join(sorted(bad_filters))}")
    if literal_digits(template):
        errors.append("literal digits in the wording — replace case-specific numbers / dates with slots")
    if TOKEN_RE.search(template):
        errors.append("contains a PII token from the source letter — rewrite that part")
    if any(m in template for m in MARKERS):
        errors.append("contains an unresolved <NUM> / <PII> marker")
    if not template.strip():
        errors.append("empty template")
    return errors


def example_slots(key: str = "") -> dict:
    return {k: v[1] for k, v in {**SLOTS, **(ITEM_SLOTS if key.startswith("query_item.") else {})}.items()}


# ------------------------------------------------------------------------------------------------
# Selection
# ------------------------------------------------------------------------------------------------
def to_row(c) -> dict:
    return {"id": str(c.id), "clause_key": c.clause_key, "kind": c.kind, "template": c.template, "text_mr": c.text_mr,
            "conditions": c.conditions or {}, "rule_set_codes": c.rule_set_codes or [], "data_class": c.data_class,
            "origin": c.origin, "verification": c.verification, "status": c.status,
            "source_case_ids": c.source_case_ids or [], "source_refs": c.source_refs or [],
            "updated_at": c.updated_at.isoformat() if c.updated_at else ""}


def matches(conditions: dict, facts: dict) -> bool:
    for k, want in (conditions or {}).items():
        if k == "validator_failed":
            if want not in (facts.get("validators_failed") or ()):
                return False
        elif k in CONDITION_KEYS:
            have = facts.get(k)
            if isinstance(want, list) and have not in want or not isinstance(want, list) and have != want:
                return False
        else:
            return False  # an unknown condition never matches (fail closed)
    return True


def specificity(row: dict, data_class: str) -> tuple:
    return (len(row["conditions"]) + (1 if row["rule_set_codes"] else 0), row["data_class"] == data_class,
            row["origin"] == "curated", row["verification"] == "verified", row["updated_at"])


def pick(rows: list[dict], key: str, facts: dict, regime: set[str] | tuple, data_class: str) -> dict | None:
    cands = [r for r in rows if r["clause_key"] == key and r["status"] == "canonical" and r["template"]
             and r["data_class"] in (data_class, "any") and matches(r["conditions"], facts)
             and (not r["rule_set_codes"] or set(r["rule_set_codes"]) & set(regime))]
    return max(cands, key=lambda r: specificity(r, data_class)) if cands else None


def canonical_rows(session: Session, data_class: str) -> list[dict]:
    from rci.db.models import Clause

    return [to_row(c) for c in session.scalars(select(Clause).where(
        Clause.status == "canonical", Clause.data_class.in_((data_class, "any"))))]


def fingerprint(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(sorted((r["id"], r["template"], r["updated_at"]) for r in rows),
                                     ensure_ascii=False).encode()).hexdigest()[:8]


# ------------------------------------------------------------------------------------------------
# Seed
# ------------------------------------------------------------------------------------------------
def _variant(conditions: dict, rule_sets: list) -> str:
    return hashlib.sha256(json.dumps([conditions or {}, sorted(rule_sets or [])], sort_keys=True).encode()).hexdigest()[:8]


def seed_clauses(session: Session) -> int:
    """Upsert rci/seed/clauses.yaml. A seed row an officer has edited (curated_by set) is left alone."""
    from rci.db.models import Clause
    from rci.seed import _load

    n = 0
    for spec in _load("clauses.yaml")["clauses"]:
        key, template = spec["key"], spec["template"].strip()
        errors = validate_template(template, key=key)
        if errors:
            raise ClauseError([f"seed {key}: {e}" for e in errors])
        conditions, rule_sets = spec.get("conditions") or {}, spec.get("rule_sets") or []
        seed_key = f"seed:{key}:{_variant(conditions, rule_sets)}"
        row = session.scalar(select(Clause).where(Clause.seed_key == seed_key))
        text, _ = render(template, example_slots(key))
        if row is None:
            row = Clause(seed_key=seed_key, origin="seed", status="canonical", canonical=True, data_class="any",
                         verification="to_verify")
            session.add(row)
        elif row.curated_by is not None:
            continue
        row.clause_key, row.kind, row.template, row.text_mr = key, spec["kind"], template, text
        row.conditions, row.rule_set_codes = conditions, rule_sets
        row.slots = sorted(variables(template))
        n += 1
    session.flush()
    return n


# ------------------------------------------------------------------------------------------------
# Mining: split + delexicalise (pure) …
# ------------------------------------------------------------------------------------------------
NUMBERED = re.compile(r"^\s*([0-9०-९]{1,3})\s*[.)]\s*(.+)$")
SIGNATURE = re.compile(r"^(?:उपजिल्हाधिकारी|करिता जिल्हाधिकारी|जिल्हाधिकारी,|तहसीलदार\b|नायब तहसीलदार|अव्वल कारकून|लिपिक)")
PAGE_MARK = re.compile(r"^पान क्र\.")


@dataclass
class Mined:
    kind: str
    key: str
    text: str  # source wording (redacted page text)
    template: str  # delexicalised
    page_seq: int | None
    flags: dict = field(default_factory=dict)


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", ascii_digits(s or "")).strip().rstrip(".;:").strip()


def _item_codes(card: dict, exhibit_id: str) -> list[tuple[str, list[str], str | None]]:
    out = []
    for q in card["facts"].get("queries") or []:
        if q["exhibit_id"] != exhibit_id:
            continue
        for it in q["items"]:
            out.append((_norm(it.get("raw") or it.get("value")), list(it.get("requirement_codes") or []), it.get("mapping")))
    return out


def _item_key(text: str, items: list[tuple[str, list[str], str | None]], flags: dict) -> str:
    t = _norm(text)
    for raw, codes, mapping in items:
        if raw and (raw == t or raw in t or t in raw):
            if codes:
                if len(codes) > 1:
                    flags["multi_code"] = codes
                return f"query_item.{codes[0]}"
            if mapping == "not_requirement":
                return "query_item.not_requirement"
    flags["unmapped_item"] = True
    return "query_item.unmapped"


READ_KEYS = [("त्रुटी पूर्तता", "query_reply"), ("पूर्तता", "query_reply"), ("त्रुटी पत्र", "query_letter"), ("स्थळ पाहणी", "field_report"),
             ("मोजणी", "ctso_report"), ("मागणी पत्र", "demand_letter"), ("चलन", "challan"), ("अर्ज", "application")]
CONDITION_KEYWORDS = [("मिळकत पत्रिके", "mutation"), ("वापर", "use"), ("भाडेपट्ट", "lease_rent"), ("भंग", "breach")]
COPY_KEYWORDS = [("नगर भूमापन", "ctso"), ("तहसीलदार", "tahsildar"), ("संस्था", "society")]


def _first(keywords: list[tuple[str, str]], text: str, default: str) -> str:
    return next((k for w, k in keywords if w in text), default)


def split_exhibit(kind: str, lines: list[tuple[str, int]], card: dict, exhibit_id: str) -> list[tuple[str, str, int, dict]]:
    """[(clause key, text, page_seq, flags)] for one letter / order / note."""
    items = _item_codes(card, exhibit_id) if kind == "query_letter" else []
    out: list[tuple[str, str, int, dict]] = []
    body = kind == "scrutiny_note"  # notes have no letter header
    section = None
    for raw, page in lines:
        line = raw.strip()
        if not line or PAGE_MARK.match(line):
            continue
        if not body:
            if re.match(r"^विषय\s*[:：]", line) or (kind == "collector_order" and re.match(r"^(आदेश|वाचले)\b", line)):
                body = True
            else:
                continue  # letterhead, number, date, title, addressee
        if SIGNATURE.match(line):
            section = "signature"
            continue
        flags: dict = {}
        m_head = re.match(r"^(विषय|संदर्भ)\s*[:：]\s*(.*)$", line)
        numbered = NUMBERED.match(line)
        prefix = {"query_letter": "query", "demand_letter": "demand", "collector_order": "order", "scrutiny_note": "note"}[kind]
        if m_head:
            key = f"{prefix}.{'subject' if m_head.group(1) == 'विषय' else 'reference'}"
            line = m_head.group(2)
        elif kind == "collector_order" and re.match(r"^वाचले", line):
            section = "read"
            continue
        elif kind == "collector_order" and re.match(r"^आदेश\s*$", line):
            continue
        elif kind == "collector_order" and re.match(r"^अटी", line):
            section = "conditions"
            continue
        elif kind == "collector_order" and re.match(r"^प्रत\s*[:：]", line):
            section, line = "copy", re.sub(r"^प्रत\s*[:：]\s*", "", line)
            key = f"order.copy_to.{_first(COPY_KEYWORDS, line, 'other')}"
        elif kind == "collector_order" and line.startswith("आणि ज्याअर्थी"):
            section, key = "whereas", ("order.whereas.payment" if "जमा" in line else "order.whereas.other")
        elif kind == "collector_order" and line.startswith("ज्याअर्थी"):
            section, key = "whereas", "order.whereas.application"
        elif kind == "collector_order" and line.startswith("त्याअर्थी"):
            section, key = "operative", "order.operative"
        elif numbered and kind == "query_letter":
            line = numbered.group(2)
            key = _item_key(line, items, flags)
        elif numbered and kind == "collector_order" and section == "read":
            line = numbered.group(2)
            key = f"order.read.{_first(READ_KEYS, line, 'other')}"
        elif numbered and kind == "collector_order" and section == "conditions":
            line = numbered.group(2)
            key = f"order.condition.{_first(CONDITION_KEYWORDS, line, 'other')}"
        elif kind == "query_letter" and line.startswith("तरी"):
            key = "query.closing"
        elif kind == "query_letter" and "छाननी" in line:
            key = "query.intro"
        elif kind == "demand_letter" and "नुसार दर" in line:
            key = "demand.line.asr_rate"
        elif kind == "demand_letter" and re.match(r"^क्षेत्र\s*[:：]", line):
            key = "demand.line.area"
        elif kind == "demand_letter" and "नजराणा दर" in line:
            key = "demand.line.rate"
        elif kind == "demand_letter" and "एकूण रक्कम" in line:
            key = "demand.line.total"
        elif kind == "demand_letter" and line.startswith("सदर रक्कम"):
            key = "demand.payment_instruction"
        elif kind == "demand_letter" and ("निश्चित करण्यात" in line or line.startswith("उपरोक्त")):
            key = "demand.intro"
        else:
            if numbered:
                line = numbered.group(2)
            key = f"{prefix}.body"
        if line.strip():
            out.append((key, line.strip(), page, flags))
    return out


def _v(card: dict, path: str):
    group, _, name = path.partition(".")
    f = (card["facts"].get(group) or {}).get(name)
    return f.get("value") if isinstance(f, dict) else None


def _date_forms(iso: str | None) -> list[str]:
    d = to_date(iso) if iso else None
    if d is None:
        return []
    base = [d.strftime("%d/%m/%Y"), d.strftime("%d.%m.%Y"), d.strftime("%d-%m-%Y")]
    return [devanagari_digits(b) for b in base] + base + [mr_date(d, "long")]


def _amount_forms(v) -> list[str]:
    try:
        base = [inr(v, "always"), inr(v), str(int(float(ascii_digits(str(v)).replace(",", ""))))]
    except (ValueError, TypeError):
        return []
    return [mr_num(b) for b in base] + base


def _number_forms(v) -> list[str]:
    if v in (None, ""):
        return []
    s = str(v)
    return [devanagari_digits(s), ascii_digits(s)]


def delexicaliser(card: dict) -> Callable[[str], str]:
    """Replace this case's own facts in a sentence by slots (longest surface form first)."""
    f = card["facts"]
    first = lambda xs, k: next((x.get(k, {}).get("value") for x in xs if isinstance(x.get(k), dict) and x[k].get("value")), None)  # noqa: E731
    inspections = f.get("inspections") or []
    field_rep = [i for i in inspections if i["kind"] == "FIELD_REPORT"]
    ctso = [i for i in inspections if i["kind"] == "CTSO_REPORT"]
    queries = sorted(f.get("queries") or [], key=lambda q: ((q.get("letter_date") or {}).get("value") or ""))
    reply = next((e.get("date") for e in card.get("events") or [] if e["code"] == "QUERY_REPLIED" and e.get("date")), None)
    subs: list[tuple[str, str, bool]] = []  # (surface, replacement, numeric)

    def add(forms, repl, numeric=True):
        subs.extend((x, repl, numeric) for x in forms if x and len(x) >= 2)

    for iso, slot in ((_v(card, "application.date"), "application_date"), (_v(card, "sgm.meeting_date"), "sgm_date"),
                      (_v(card, "property_card.issue_date"), "property_card_date"), (_v(card, "lease.lease_date"), "lease_date"),
                      (first(queries, "letter_date"), "query_date"), (reply, "reply_date"),
                      (first(field_rep, "inspection_date"), "inspection_date"), (first(ctso, "inspection_date"), "ctso_date"),
                      (_v(card, "demand.letter_date"), "demand_date"), (first(f.get("payments") or [], "payment_date"), "payment_date"),
                      (_v(card, "order.order_date"), "order_date")):
        add(_date_forms(iso), "{{ %s|mr_date }}" % slot)
    for v, slot in ((_v(card, "demand.amount"), "premium"), (_v(card, "order.premium_amount"), "premium"),
                    (first(f.get("payments") or [], "amount"), "paid_amount")):
        if v not in (None, ""):
            add(_amount_forms(v), "{{ %s|inr|mr_num }}" % slot)
    add(_number_forms(_v(card, "land.area_sqm")), "{{ area_sqm|mr_num }}")
    add(_number_forms(first(field_rep, "area_sqm")), "{{ inspection_area|mr_num }}")
    add(_number_forms(first(ctso, "area_sqm")), "{{ ctso_area|mr_num }}")
    add(_number_forms(_v(card, "land.cts_no")), "{{ cts_no|mr_num }}")
    for v, slot in ((first(f.get("payments") or [], "grn"), "grn"), (_v(card, "order.order_no"), "order_no"),
                    (_v(card, "society.registration_no"), "registration_no")):
        add(_number_forms(v), "{{ %s }}" % slot)
    for v, slot in ((_v(card, "society.name"), "society_name"), (_v(card, "land.village"), "village"),
                    (_v(card, "land.taluka"), "taluka"), (_v(card, "land.district"), "district")):
        if v:
            add([v], "{{ %s }}" % slot, numeric=False)
    subs.sort(key=lambda s: -len(s[0]))
    patterns = []
    for surface, repl, numeric in subs:
        esc = re.escape(surface)
        pat = rf"(?<![0-9०-९]){esc}(?![0-9०-९])" if numeric else rf"(?<![ऀ-ॿ\w]){esc}"
        patterns.append((re.compile(pat), repl))

    def apply(text: str) -> str:
        holes: list[str] = []
        for pat, repl in patterns:
            def hole(_m, repl=repl):
                holes.append(repl)
                return f"\x00{len(holes) - 1}\x00"
            text = pat.sub(hole, text)
        return re.sub(r"\x00(\d+)\x00", lambda m: holes[int(m.group(1))], text)

    return apply


def mine_exhibit(card: dict, exhibit: dict, page_text: dict[int, str]) -> list[Mined]:
    kind = KIND_OF_EXHIBIT.get(exhibit["type"])
    if not kind:
        return []
    lines = [(ln, seq) for seq in exhibit["pages"] for ln in (page_text.get(seq) or "").split("\n")]
    delex = delexicaliser(card)
    out = []
    for key, text, page, flags in split_exhibit(kind, lines, card, exhibit["exhibit_id"]):
        template = delex(text)
        rest = literal_digits(template)
        if rest:
            flags["needs_slots"] = True
        if TOKEN_RE.search(template):
            flags["has_pii"] = True
        out.append(Mined(kind, key, text, template, page, flags))
    return out


# ------------------------------------------------------------------------------------------------
# … DB side: mine, embed, cluster
# ------------------------------------------------------------------------------------------------
def _hash(kind: str, key: str, template: str) -> str:
    return hashlib.sha256(f"{kind}|{key}|{tidy(template)}".encode()).hexdigest()


def _embed_text(template: str) -> str:
    return re.sub(r"{{\s*(\w+)[^}]*}}", r"\1", template)


def cluster(vectors: list[list[float]], order: list[int], threshold: float) -> list[int]:
    """Greedy leader clustering: rows visited in `order` join the first leader with cosine ≥ threshold. → cluster ids."""
    import numpy as np

    if not vectors:
        return []
    m = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    m = m / np.where(norms == 0, 1, norms)
    ids = [0] * len(vectors)
    leaders: list[int] = []
    for i in order:
        if leaders:
            sims = m[leaders] @ m[i]
            j = int(np.argmax(sims))
            if sims[j] >= threshold:
                ids[i] = ids[leaders[j]]
                continue
        leaders.append(i)
        ids[i] = len(leaders)
    return ids


def mine(session: Session, *, data_class: str | None = None, threshold: float | None = None) -> dict:
    from rci.adapters.embeddings import get_embedding_client
    from rci.db.models import Case, CaseCard, Clause, Page
    from rci.drafting.config import DraftingConfig
    from rci.pipeline.runner import audit

    threshold = threshold if threshold is not None else float(DraftingConfig.load().value("clauses.cluster_threshold", 0.8))
    q = (select(Case, CaseCard).join(CaseCard, and_(CaseCard.case_id == Case.id, CaseCard.status == "approved", CaseCard.locked.is_(True)))
         .where(Case.role.in_(("precedent", "unassigned"))).order_by(Case.case_ref))
    if data_class:
        q = q.where(Case.data_class == data_class)
    groups: dict[tuple[str, str, str], dict] = {}
    n_cases = n_exhibits = 0
    for case, cc in session.execute(q).all():
        if case.role == "holdout":  # the query excludes them; belt and braces for the backtest's sake
            raise AssertionError("holdout reached clause mining")
        exs = [e for e in cc.card["exhibits"] if e["type"] in KIND_OF_EXHIBIT]
        if not exs:
            continue
        n_cases += 1
        seqs = sorted({s for e in exs for s in e["pages"]})
        pages = {p.seq: p.text_redacted or "" for p in session.scalars(select(Page).where(Page.case_id == case.id, Page.seq.in_(seqs)))}
        soc = _v(cc.card, "society.name")
        for ex in exs:
            n_exhibits += 1
            for m in mine_exhibit(cc.card, ex, pages):
                h = _hash(m.kind, m.key, m.template)
                g = groups.setdefault((case.data_class, m.kind, h), {"m": m, "refs": [], "flags": {}})
                g["refs"].append({"case_id": str(case.id), "case_ref": case.case_ref, "exhibit_id": ex["exhibit_id"],
                                  "page_seq": m.page_seq, "society_name": soc})
                g["flags"].update(m.flags)
    existing = {(c.data_class, c.kind, c.text_hash): c for c in session.scalars(select(Clause).where(Clause.text_hash.is_not(None)))}
    touched, created = set(), 0
    for (dc, kind, h), g in groups.items():
        m = g["m"]
        row = existing.get((dc, kind, h))
        if row is None:
            row = Clause(data_class=dc, kind=kind, clause_key=m.key, text_hash=h, origin="mined", status="candidate",
                         verification="to_verify", text_mr=m.text, template=m.template, slots=sorted(variables_safe(m.template)))
            session.add(row)
            created += 1
        if row.status == "candidate":
            row.text_mr, row.flags = m.text, g["flags"]
        row.source_refs = g["refs"]
        row.source_case_ids = sorted({r["case_id"] for r in g["refs"]})
        row.member_count = len(g["refs"])
        touched.add(id(row))
    stale = [c for c in existing.values() if id(c) not in touched and c.status == "candidate" and c.origin == "mined"
             and (data_class is None or c.data_class == data_class)]
    for c in stale:
        session.delete(c)
    session.flush()

    rows = [c for c in session.scalars(select(Clause).where(Clause.origin.in_(("mined", "curated")), Clause.status != "rejected"))
            if data_class is None or c.data_class == data_class]
    client = get_embedding_client()
    by_dc: dict[str, list] = defaultdict(list)
    for c in rows:
        by_dc[c.data_class].append(c)
    summary: dict[str, dict] = {}
    for dc, rs in by_dc.items():
        todo = [c for c in rs if c.embedding is None]
        if todo:
            vecs = client.embed([_embed_text(c.template or c.text_mr) for c in todo], data_class="real" if dc == "real" else "synthetic")
            for c, v in zip(todo, vecs):
                c.embedding = v
        for kind in KINDS:
            krows = [c for c in rs if c.kind == kind]
            order = sorted(range(len(krows)), key=lambda i: (-krows[i].member_count, krows[i].clause_key, krows[i].text_hash or ""))
            for c, cid in zip(krows, cluster([list(c.embedding) for c in krows], order, threshold)):
                c.cluster_id = cid
            if krows:
                summary.setdefault(dc, {})[kind] = {
                    "rows": len(krows), "clusters": len({c.cluster_id for c in krows}),
                    "needs_slots": sum(1 for c in krows if (c.flags or {}).get("needs_slots")),
                    "has_pii": sum(1 for c in krows if (c.flags or {}).get("has_pii"))}
    session.flush()
    out = {"cases": n_cases, "exhibits": n_exhibits, "distinct": len(groups), "created": created, "stale_removed": len(stale),
           "threshold": threshold, "by_data_class": summary}
    audit(session, "clauses.mine", actor_kind="system", **{k: v for k, v in out.items() if k != "by_data_class"})
    return out


def variables_safe(template: str) -> set[str]:
    try:
        return set(variables(template))
    except TemplateSyntaxError:
        return set()


# ------------------------------------------------------------------------------------------------
# Curation
# ------------------------------------------------------------------------------------------------
EDITABLE = ("clause_key", "template", "rule_set_codes", "conditions", "status", "notes", "verification", "kind")


def _check_conditions(conditions: dict) -> list[str]:
    return [f"unknown condition {k!r} (allowed: {', '.join(CONDITION_KEYS)})" for k in (conditions or {}) if k not in CONDITION_KEYS]


def update_clause(session: Session, clause, patch: dict, *, user_id: uuid.UUID | None, is_admin: bool) -> Any:
    from rci.db.models import Clause
    from rci.pipeline.runner import audit

    unknown = set(patch) - set(EDITABLE)
    if unknown:
        raise ClauseError([f"cannot edit {', '.join(sorted(unknown))}"])
    if "verification" in patch and not is_admin:
        raise PermissionError("only an admin can mark wording verified")
    if patch.get("kind") and patch["kind"] not in KINDS:
        raise ClauseError([f"kind must be one of {KINDS}"])
    if patch.get("status") and patch["status"] not in ("candidate", "canonical", "rejected"):
        raise ClauseError(["status must be candidate | canonical | rejected"])
    new = {k: patch.get(k, getattr(clause, k)) for k in EDITABLE}
    errors = _check_conditions(new["conditions"])
    if new["status"] == "canonical":
        errors += validate_template(new["template"] or "", key=new["clause_key"])
    if errors:
        raise ClauseError(errors)
    before = {k: getattr(clause, k) for k in EDITABLE}
    for k in EDITABLE:
        setattr(clause, k, new[k])
    clause.canonical = clause.status == "canonical"
    if clause.template:
        clause.slots = sorted(variables_safe(clause.template))
    if clause.template != before["template"]:
        clause.embedding = None  # re-embedded on the next mining run
    now = dt.datetime.now(dt.timezone.utc)
    if clause.origin == "mined" and clause.status == "canonical":
        clause.origin = "curated"
    clause.curated_by, clause.curated_at = user_id, now
    if clause.status == "canonical":  # one canonical row per (data class, key, conditions, rule sets)
        for other in session.scalars(select(Clause).where(Clause.id != clause.id, Clause.clause_key == clause.clause_key,
                                                          Clause.data_class == clause.data_class, Clause.status == "canonical")):
            if _variant(other.conditions, other.rule_set_codes) == _variant(clause.conditions, clause.rule_set_codes):
                other.status, other.canonical = "candidate", False
    session.flush()
    audit(session, "clause.update", actor_kind="user", entity_type="clause", entity_id=clause.id, actor=str(user_id) if user_id else None,
          changed=sorted(k for k in EDITABLE if before[k] != getattr(clause, k)), status=clause.status)
    return clause


def create_clause(session: Session, data: dict, *, user_id: uuid.UUID | None, is_admin: bool):
    from rci.db.models import Clause

    if data.get("data_class") not in ("real", "synthetic", "any"):
        raise ClauseError(["data_class must be real | synthetic | any"])
    row = Clause(clause_key=data.get("clause_key") or "", kind=data.get("kind") or "", text_mr=data.get("template") or "",
                 template=data.get("template"), data_class=data["data_class"], origin="curated", status="candidate",
                 source_case_ids=data.get("source_case_ids") or [], source_refs=data.get("source_refs") or [])
    session.add(row)
    session.flush()
    patch = {k: data[k] for k in EDITABLE if k in data}
    patch.setdefault("status", "candidate")
    return update_clause(session, row, patch, user_id=user_id, is_admin=is_admin)
