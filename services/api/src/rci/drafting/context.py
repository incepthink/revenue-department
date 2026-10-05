"""Draft content for the four templates, built from the case card, the stored analysis and the clause library.

Pure (no DB): `DraftInputs` carries everything; `build(template_code, inputs, mode)` returns the docxtpl context plus
the bookkeeping the service stores (clauses used, provenance notes, missing slots, warnings, narratives, and the
strings the issue render must never contain).

Render modes
  internal  numbered provenance markers [टीप n] after clauses, gap items, facts and figures, a closing list of notes (clause
            source cases, precedent citations, rule rows, fee trace status) and — added by render.py — the watermark
  issue     the same text, no markers, no notes, no other case's ID anywhere (enforced by render.leak_check)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from rci.drafting import clauses as cl
from rci.drafting.config import DraftingConfig
from rci.drafting.crosscheck import allowed_from, check
from rci.drafting.mrtext import Blank, fy_label, inr, mr_date, mr_num, mr_rupees
from rci.drafting.narrative import narrative
from rci.intel.rulecompat import LABEL_TEXT
from rci.review.summary import TENURE

TEMPLATES = {
    "SCRUTINY_NOTE": {"kind": "scrutiny_note", "name_mr": "छाननी टिप्पणी", "name_en": "Scrutiny note"},
    "QUERY_LETTER": {"kind": "query_letter", "name_mr": "त्रुटी पत्र", "name_en": "Query / deficiency letter"},
    "DEMAND_LETTER": {"kind": "demand_letter", "name_mr": "मागणी पत्र", "name_en": "Demand letter"},
    "COLLECTOR_ORDER": {"kind": "collector_order", "name_mr": "जिल्हाधिकारी आदेश", "name_en": "Collector's order"},
}
STAGE_MR = {"filing": "अर्जासोबत", "query_response": "त्रुटी पूर्तता", "internal": "कार्यालयीन", "final": "अंतिम"}
GAP_STATUS_MR = {"missing": "सादर नाही", "invalid": "सदोष", "queried": "मागवले आहे"}
CHECK_MR = {"pass": "योग्य", "fail": "त्रुटी", "not_evaluable": "तपासता आले नाही"}
MARK_OPEN, MARK_CLOSE = "[टीप ", "]"


@dataclass
class DraftInputs:
    case: dict  # {id, case_ref, data_class}
    office: dict  # {code, name_mr, district}
    card: dict
    analysis: dict
    catalogs: dict  # {"requirements": {code: {...}}, "exhibit_types": {code: {...}}, "events": {code: {...}}}
    rule_sets: dict  # code -> {name, kind, status}
    clauses: list[dict]
    cfg: DraftingConfig
    today: dt.date
    llm: Any = None


@dataclass
class DocModel:
    template_code: str
    mode: str
    context: dict
    clauses_used: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    narratives: list[dict] = field(default_factory=list)
    crosscheck: list[dict] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing_slots and not any(w.startswith("incomplete:") for w in self.warnings)


# ------------------------------------------------------------------------------------------------
# Facts → slots
# ------------------------------------------------------------------------------------------------
def _f(card: dict, path: str) -> dict | None:
    group, _, name = path.partition(".")
    f = (card["facts"].get(group) or {}).get(name)
    return f if isinstance(f, dict) and f.get("value") not in (None, "") else None


def _v(card: dict, path: str):
    f = _f(card, path)
    return f["value"] if f else None


def _val(d: dict | None, k: str):
    x = (d or {}).get(k)
    return x.get("value") if isinstance(x, dict) else None


def plain_number(v) -> str | None:
    """5.0000 → 5, 2.50 → 2.5 (rates as printed in letters)."""
    if v in (None, ""):
        return None
    d = Decimal(str(v)).normalize()
    return format(d, "f")


def id_text(v) -> str:
    """Identifiers keep their script: Devanagari digits only when the ID has no Latin letters (१२३४/अ, but PNA/HSG/1234)."""
    t = str(v)
    return t if any("a" <= ch.lower() <= "z" for ch in t) else mr_num(t)


def premium_payments(card: dict) -> list[dict]:
    return sorted((p for p in card["facts"].get("payments") or [] if _val(p, "amount") not in (None, "")),
                  key=lambda p: _val(p, "payment_date") or "")


def _jinja(cfg_value, slots: dict) -> Any:
    if isinstance(cfg_value, list):
        return [_jinja(x, slots) for x in cfg_value]
    if isinstance(cfg_value, str) and "{" in cfg_value:
        return cl.render(cfg_value, slots)[0]
    return cfg_value


def base_rule_set(inputs: DraftInputs) -> str | None:
    regime = inputs.analysis.get("regime") or []
    return next((c for c in regime if (inputs.rule_sets.get(c) or {}).get("kind") == "base"), regime[0] if regime else None)


def slot_values(inputs: DraftInputs) -> dict:
    card, cfg = inputs.card, inputs.cfg
    f = card["facts"]
    fee = inputs.analysis.get("fee") or {}
    fin = fee.get("inputs") or {}
    queries = sorted(f.get("queries") or [], key=lambda q: _val(q, "letter_date") or "")
    insp = [i for i in f.get("inspections") or [] if i["kind"] == "FIELD_REPORT"]
    ctso = [i for i in f.get("inspections") or [] if i["kind"] == "CTSO_REPORT"]
    pays = premium_payments(card)
    reply = next((e.get("date") for e in card.get("events") or [] if e["code"] == "QUERY_REPLIED" and e.get("date")), None)
    district = _v(card, "land.district") or inputs.office.get("district")
    s = {
        "case_ref": inputs.case["case_ref"], "society_name": _v(card, "society.name"),
        "registration_no": _v(card, "society.registration_no"), "village": _v(card, "land.village"),
        "taluka": _v(card, "land.taluka"), "district": district, "cts_no": _v(card, "land.cts_no"),
        "area_sqm": _v(card, "land.area_sqm"), "tenure_mr": TENURE.get(_v(card, "land.tenure"), (None,))[0],
        "application_date": _v(card, "application.date"), "property_card_date": _v(card, "property_card.issue_date"),
        "sgm_date": _v(card, "sgm.meeting_date"), "lease_date": _v(card, "lease.lease_date"),
        "query_date": _val(queries[0], "letter_date") if queries else None, "reply_date": reply,
        "inspection_date": _val(insp[-1], "inspection_date") if insp else None,
        "inspection_area": _val(insp[-1], "area_sqm") if insp else None,
        "ctso_date": _val(ctso[-1], "inspection_date") if ctso else None, "ctso_area": _val(ctso[-1], "area_sqm") if ctso else None,
        "demand_date": _v(card, "demand.letter_date"),
        "premium": _v(card, "demand.amount") or fee.get("total"),
        "asr_year": fin.get("asr_year"), "asr_rate": fin.get("asr_rate_per_sqm"),
        "rate_pct": plain_number((fin.get("slab") or {}).get("rate_pct")),
        "paid_amount": _val(pays[-1], "amount") if pays else None, "payment_date": _val(pays[-1], "payment_date") if pays else None,
        "grn": _val(pays[-1], "grn") if pays else None,
        "order_no": _v(card, "order.order_no"), "order_date": _v(card, "order.order_date"),
        "reply_days": cfg.value("reply_days"), "payment_days": cfg.value("payment_days"),
        "head_of_account": cfg.value("premium_head_of_account"),
        "office_name_mr": inputs.office.get("name_mr"), "today": inputs.today.isoformat(),
    }
    base = base_rule_set(inputs)
    names = cfg.value("rule_set_names_mr", {}) or {}
    s["rule_name_mr"] = names.get(base) or (inputs.rule_sets.get(base) or {}).get("name") if base else None
    s["collector_title"] = _jinja(cfg.value("collector_title"), s)
    return s


def condition_facts(inputs: DraftInputs) -> dict:
    card = inputs.card
    return {"tenure": _v(card, "land.tenure"),
            "validators_failed": {v["code"] for v in card.get("validations") or [] if v["status"] == "fail"},
            "premium_paid": bool(premium_payments(card)), "has_query_letter": bool(card["facts"].get("queries")),
            "fee_status": (inputs.analysis.get("fee") or {}).get("status")}


# ------------------------------------------------------------------------------------------------
# Builder
# ------------------------------------------------------------------------------------------------
class Builder:
    def __init__(self, inputs: DraftInputs, template_code: str, mode: str):
        self.i, self.mode, self.internal = inputs, mode, mode == "internal"
        self.doc = DocModel(template_code, mode, {})
        self.kind = TEMPLATES[template_code]["kind"]
        self.slots = slot_values(inputs)
        self.facts = condition_facts(inputs)
        self.regime = set(inputs.analysis.get("regime") or [])
        self.allowed = allowed_from(inputs.card, fee=inputs.analysis.get("fee"),
                                    extra=[self.slots.get(k) for k in ("reply_days", "payment_days", "asr_year", "rate_pct",
                                                                       "asr_rate", "head_of_account", "case_ref", "rule_name_mr",
                                                                       "collector_title")])
        self._missing: set[str] = set()

    # -- provenance ------------------------------------------------------------------------------
    def note(self, text: str) -> str:
        if not self.internal:
            return ""
        n = len(self.doc.notes) + 1
        self.doc.notes.append({"n": n, "n_mr": mr_num(n), "text": text})
        return f" {MARK_OPEN}{mr_num(n)}{MARK_CLOSE}"

    def _refs(self, refs: list[dict], limit: int = 4) -> str:
        own = self.i.case["id"]
        seen, out = set(), []
        for r in refs:
            if r.get("case_id") == own or r.get("case_ref") in seen:
                continue
            seen.add(r.get("case_ref"))
            out.append(f"{r.get('case_ref')}{' पृ. ' + mr_num(r['page_seq']) if r.get('page_seq') else ''}")
        more = len({r.get("case_ref") for r in refs if r.get("case_id") != own}) - len(out[:limit])
        return ", ".join(out[:limit]) + (f" (+{mr_num(more)})" if more > 0 else "")

    def forbid_refs(self, refs: list[dict]) -> None:
        own = self.i.case["id"]
        own_soc = self.slots.get("society_name")
        for r in refs:
            if r.get("case_id") and r["case_id"] != own:
                self.doc.forbidden += [x for x in (r.get("case_ref"), r.get("case_id"), r.get("exhibit_id")) if x]
                if r.get("society_name") and r["society_name"] != own_soc:
                    self.doc.forbidden.append(r["society_name"])

    # -- clauses ---------------------------------------------------------------------------------
    def clause(self, key: str, *, extra: dict | None = None, required: bool = True, fallback: str | None = None,
               note_extra: str = "") -> tuple[str, str] | None:
        row = cl.pick(self.i.clauses, key, self.facts, self.regime, self.i.case["data_class"])
        if row is None and fallback:
            row = cl.pick(self.i.clauses, fallback, self.facts, self.regime, self.i.case["data_class"])
        if row is None:
            if required:
                self.doc.warnings.append(f"incomplete: no canonical clause for {key}")
                return Blank(key), ""
            return None
        slots = {**self.slots, **(extra or {})}
        text, missing = cl.render(row["template"], slots)
        self._missing |= set(missing)
        for v in check(text, self.allowed):
            self.doc.crosscheck.append({"where": key, **v})
        src = self._refs(row["source_refs"]) if row["source_refs"] else ""
        self.forbid_refs(row["source_refs"])
        self.doc.clauses_used.append({"clause_id": row["id"], "clause_key": row["clause_key"], "origin": row["origin"],
                                      "verification": row["verification"], "data_class": row["data_class"], "text": text,
                                      "missing": missing, "source_case_ids": row["source_case_ids"]})
        mark = self.note(f"वाक्यरचना {row['clause_key']} · {row['origin']} · {row['verification']}"
                         + (f" · स्रोत प्रकरणे: {src}" if src else "") + (f" · {note_extra}" if note_extra else "")
                         + (f" · रिक्त: {', '.join(missing)}" if missing else ""))
        return text, mark

    def text(self, key: str, **kw) -> str:
        r = self.clause(key, **kw)
        return (r[0] + r[1]) if r else ""

    def slot(self, name: str, fmt=None) -> str:
        v = self.slots.get(name)
        if v in (None, ""):
            self._missing.add(name)
            return Blank(name)
        return fmt(v) if fmt else str(v)

    # -- common blocks ---------------------------------------------------------------------------
    def letter_head(self, title: str, signatory: str) -> dict:
        cfg = self.i.cfg
        date_mode = cfg.value("letter_date", "today")
        ref = _jinja(cfg.value("ref_format"), self.slots)
        if self.kind == "collector_order" and self.slots.get("order_no"):
            ref = self.slots["order_no"]
        return {"internal": self.internal, "letterhead": _jinja(cfg.value("letterhead", []), self.slots),
                "ref_no": ref, "date": mr_date(self.i.today) if date_mode == "today" else Blank("date"),
                "title": title, "to": _jinja(cfg.value("addressee", []), self.slots),
                "signatory": _jinja(cfg.value(f"signatory.{signatory}", []), self.slots), "notes": self.doc.notes,
                "notes_title": "मसुदा टिपा (अंतर्गत वापरासाठी — प्रत पाठविताना वगळल्या जातात)"}

    def narrative_block(self, include_application: bool) -> str:
        use_llm = bool(self.i.cfg.value("narrative.llm", True))
        res = narrative(self.i.card, fee=self.i.analysis.get("fee"), llm=self.i.llm if use_llm else None,
                        data_class=self.i.case["data_class"], include_application=include_application)
        self.doc.narratives.append({"slot": "narrative", **res})
        for v in res["violations"]:
            self.doc.crosscheck.append({"where": "narrative", **v})
        mark = self.note(f"वस्तुस्थिती: {'LLM (तपासणी उत्तीर्ण)' if res['source'] == 'llm' else 'साचा'} — "
                         "सर्व दिनांक / आकडे प्रकरणाच्या नोंदींशी तपासले")
        self.slots["narrative"] = res["text"]
        return res["text"] + mark

    def finish(self, ctx: dict) -> DocModel:
        d = self.doc
        d.context = ctx
        d.missing_slots = sorted(self._missing)
        if d.missing_slots:
            d.warnings.append(f"blank slots to fill: {', '.join(d.missing_slots)}")
        if d.crosscheck:
            d.warnings.append(f"{len(d.crosscheck)} figure(s) not traceable to the case facts")
        d.forbidden = sorted({x for x in d.forbidden if x and len(x) >= 3})
        return d

    # -- analysis citations -------------------------------------------------------------------------
    def analysis_refs(self) -> None:
        a = self.i.analysis
        refs = [p["link"] for p in a.get("precedents") or [] if p.get("link")]
        refs += [{"case_id": p["case_id"], "case_ref": p["case_ref"], "society_name": p.get("society_name")}
                 for p in a.get("precedents") or []]
        for g in a.get("gaps") or []:
            refs += g.get("citations") or []
        for st in (a.get("steps") or {}).get("next", []) + (a.get("steps") or {}).get("done", []):
            refs += st.get("citations") or []
        self.forbid_refs(refs)


# ------------------------------------------------------------------------------------------------
# Templates
# ------------------------------------------------------------------------------------------------
def query_gaps(inputs: DraftInputs) -> list[dict]:
    bases = set(inputs.cfg.value("query_letter.include_bases", ["STATUTORY", "PRACTICE", "PRACTICE_OLD_RULES"]))
    return [g for g in inputs.analysis.get("gaps") or [] if g["basis"] in bases]


def _gap_note(b: Builder, g: dict) -> str:
    parts = [f"{g['code']} · {LABEL_TEXT[g['basis']][1]}"]
    if g.get("n_applicable"):
        parts.append(f"समान प्रकरणे {mr_num(g['n_supporting'])}/{mr_num(g['n_applicable'])}")
    if g.get("was_queried_in"):
        parts.append("त्रुटी पत्रांत मागवले: " + b._refs(g["was_queried_in"]))
    for r in g.get("rules") or []:
        parts.append(f"नियम {r['rule_set_code']} ({r['status']})")
    for v in g.get("validators") or []:
        pages = ", ".join(f"पृ. {mr_num(e['page_seq'])}" for e in v.get("evidence") or [] if e.get("page_seq"))
        parts.append(f"तपासणी {v['code']}{' — ' + pages if pages else ''}")
    return " · ".join(parts)


def build_query_letter(b: Builder) -> dict:
    ctx = b.letter_head("त्रुटी पत्र", "letter")
    ctx.update(subject=b.text("query.subject"), reference=b.text("query.reference"), intro=b.text("query.intro"))
    items = []
    gaps = query_gaps(b.i)
    for g in gaps:
        name = g.get("name_mr") or (b.i.catalogs["requirements"].get(g["code"]) or {}).get("name_mr") or g["code"]
        r = b.clause(f"query_item.{g['code']}", fallback="query_item.generic",
                     extra={"requirement_name_mr": name, "requirement_code": g["code"]}, note_extra=_gap_note(b, g))
        items.append({"n": mr_num(len(items) + 1), "text": r[0] + r[1]})
    excluded = [g for g in b.i.analysis.get("gaps") or [] if g not in gaps]
    for g in excluded:
        b.note(f"वगळलेली त्रुटी {g['code']} ({LABEL_TEXT[g['basis']][1]}) — अधिकाऱ्यांनी आवश्यकता तपासावी")
    if not gaps:
        b.doc.warnings.append("incomplete: the analysis reports no gaps — a query letter is not indicated")
        items.append({"n": mr_num(1), "text": Blank("query_item")})
    ctx.update(items=items, closing=b.text("query.closing"))
    return ctx


def fee_lines(b: Builder) -> tuple[list[dict], str, str]:
    fee = b.i.analysis.get("fee") or {}
    comp = fee.get("components") or {}
    s = b.slots
    blank = lambda n: Blank(n)  # noqa: E731
    money = lambda v: mr_num(inr(v, "auto")) if v not in (None, "") else None  # noqa: E731
    lines = [
        {"label": f"वार्षिक बाजारमूल्य दर तक्ता {fy_label(s['asr_year']) if s.get('asr_year') else blank('asr_year')} नुसार दर",
         "value": f"रु. {money(s['asr_rate']) or blank('asr_rate')}/- प्रति चौ.मी."},
        {"label": "क्षेत्र", "value": f"{mr_num(s['area_sqm']) if s.get('area_sqm') else blank('area_sqm')} चौ.मी."},
        {"label": "नजराणा दर", "value": f"{mr_num(s['rate_pct']) if s.get('rate_pct') not in (None, '') else blank('rate_pct')} टक्के"},
    ]
    if comp.get("market_value"):
        lines.append({"label": "जमिनीचे बाजारमूल्य", "value": f"रु. {money(comp['market_value'])}/-"})
    for k, v in comp.items():
        if k.startswith("adj."):
            lines.append({"label": f"समायोजन ({k[4:]})", "value": f"रु. {money(v)}/-"})
    total = fee.get("total")
    if total is None:
        for m in ("asr_year", "asr_rate", "rate_pct", "area_sqm", "premium"):
            if s.get(m) in (None, ""):
                b._missing.add(m)
        b._missing.add("premium")
        reason = "; ".join(fee.get("blockers") or ["fee not computed"])
        b.doc.warnings.append(f"incomplete: premium not computed — {reason}")
        b.note("नजराणा परिगणना अपूर्ण: " + reason)
        return lines, Blank("premium"), Blank("premium")
    mark = b.note(f"गणना {fee.get('engine_version')} · स्थिती {fee.get('status')}"
                  + (f" · अपुष्ट: {'; '.join(fee.get('unverified') or [])}" if fee.get("unverified") else "")
                  + (f" · नियम संच {fee.get('rule_set_code')}" if fee.get("rule_set_code") else ""))
    if fee.get("status") == "unverified":
        b.doc.warnings.append("premium computed on rules / rates / parameters still marked to_verify")
    return lines, f"रु. {money(total)}/-{mark}", f"({mr_rupees(total)})"


def build_demand_letter(b: Builder) -> dict:
    ctx = b.letter_head("मागणी पत्र", "letter")
    ctx.update(subject=b.text("demand.subject"), reference=b.text("demand.reference"), intro=b.text("demand.intro"))
    lines, total, words = fee_lines(b)
    fee = b.i.analysis.get("fee") or {}
    if fee.get("total") is not None:
        b.slots["premium"] = fee["total"]  # the letter demands what the engine computed (the officer edits if needed)
    extra = b.clause("demand.lease_rent_note", required=False)
    ctx.update(lines=lines, total=total, total_words=words, payment_instruction=b.text("demand.payment_instruction"),
               lease_note=(extra[0] + extra[1]) if extra else "", closing=b.text("demand.closing"))
    return ctx


def build_collector_order(b: Builder) -> dict:
    ctx = b.letter_head("आदेश", "order")
    s = b.slots
    read_keys = [("order.read.application", True), ("order.read.query_letter", bool(s.get("query_date"))),
                 ("order.read.query_reply", bool(s.get("reply_date"))), ("order.read.field_report", bool(s.get("inspection_date"))),
                 ("order.read.ctso_report", bool(s.get("ctso_date"))), ("order.read.demand_letter", bool(s.get("demand_date"))),
                 ("order.read.challan", True)]
    read = []
    for key, present in read_keys:
        if present:
            read.append({"n": mr_num(len(read) + 1), "text": b.text(key)})
    whereas = [b.text("order.whereas.application")]
    b.narrative_block(include_application=False)
    whereas.append(b.text("order.whereas.facts"))
    if not b.facts["premium_paid"]:
        b.doc.warnings.append("incomplete: no premium challan on the case yet — the order cannot issue before payment")
    whereas.append(b.text("order.whereas.payment"))
    if not s.get("rule_name_mr"):
        b._missing.add("rule_name_mr")
    conditions = [{"n": mr_num(i + 1), "text": t} for i, t in enumerate(
        x for x in (b.text(k, required=False) for k in ("order.condition.mutation", "order.condition.use",
                                                         "order.condition.lease_rent", "order.condition.breach")) if x)]
    copy_to = [x for x in (b.text(k, required=False) for k in ("order.copy_to.ctso", "order.copy_to.tahsildar",
                                                                "order.copy_to.society")) if x]
    ctx.update(read=read, whereas=whereas, operative=b.text("order.operative"), conditions=conditions, copy_to=copy_to)
    return ctx


def build_scrutiny_note(b: Builder) -> dict:
    ctx = b.letter_head("छाननी टिप्पणी", "note")
    i, s, card = b.i, b.slots, b.i.card
    ctx.update(subject=b.text("note.subject"), intro=b.text("note.intro"), narrative=b.narrative_block(include_application=True))

    def fact(label: str, path: str, fmt=None) -> dict:
        f = _f(card, path)
        v = f["value"] if f else None
        mark = b.note(f"{label}: {f.get('exhibit_type')} पृ. {mr_num(f.get('page_seq'))}") if f and f.get("page_seq") else ""
        return {"label": label, "value": (fmt(v) if (fmt and v is not None) else (str(v) if v is not None else "—")) + mark}

    facts = [fact("अर्जदार संस्था", "society.name"), fact("नोंदणी क्रमांक", "society.registration_no", id_text),
             {"label": "गाव / तालुका / जिल्हा", "value": " / ".join(str(x or "—") for x in (s["village"], s["taluka"], s["district"]))},
             fact("नगर भूमापन क्रमांक", "land.cts_no", id_text), fact("क्षेत्र (चौ.मी.)", "land.area_sqm", mr_num),
             {"label": "सध्याचा धारणाधिकार", "value": s.get("tenure_mr") or "—"},
             fact("अर्ज दिनांक", "application.date", mr_date), fact("विशेष सर्वसाधारण सभा दिनांक", "sgm.meeting_date", mr_date),
             fact("मिळकत पत्रिका दिनांक", "property_card.issue_date", mr_date)]
    names = i.catalogs.get("exhibit_types") or {}
    documents = [{"n": mr_num(k + 1), "name": (names.get(e["type"]) or {}).get("name_mr") or e["type"],
                  "stage": STAGE_MR.get(e["stage"], e["stage"]), "date": mr_date(e["doc_date"]) if e.get("doc_date") else "—",
                  "pages": mr_num(e.get("page_range") or "")} for k, e in enumerate(card.get("exhibits") or [])]
    vnames = i.cfg.value("validator_names_mr", {}) or {}
    checks = [{"name": vnames.get(v["code"], v["code"]), "result": CHECK_MR.get(v["status"], v["status"])
               + (" (अपुष्ट निकष)" if v.get("depends_on_unverified") else "")} for v in card.get("validations") or []]
    gaps = [{"n": mr_num(k + 1), "name": g.get("name_mr") or g["code"], "status": GAP_STATUS_MR.get(g["query_status"], g["query_status"]),
             "basis": LABEL_TEXT[g["basis"]][1], "why": (g.get("explanation_mr") or "") + b.note(_gap_note(b, g))}
            for k, g in enumerate(i.analysis.get("gaps") or [])]
    fee = i.analysis.get("fee") or {}
    lines, total, words = fee_lines(b)
    fee_rows = lines + [{"label": "एकूण देय नजराणा", "value": total if isinstance(total, Blank) else f"{total} {words}"}]
    fee_status = {"computed": "परिगणना पूर्ण.", "unverified": "परिगणना अपुष्ट नियम / दर / निकषांवर आधारित — खात्री करावी.",
                  "blocked": "परिगणना अपूर्ण — " + "; ".join(fee.get("blockers") or [])}.get(fee.get("status"), "")
    # the note may be put up before a fee exists: a blocked fee is information here, not a gap in the note itself
    b.doc.warnings = [w for w in b.doc.warnings if not w.startswith("incomplete: premium not computed")]
    b._missing -= {"premium", "asr_year", "asr_rate", "rate_pct"}
    ev = i.catalogs.get("events") or {}
    steps = [{"n": mr_num(k + 1), "name": st.get("name_mr") or (ev.get(st["code"]) or {}).get("name_mr") or st["code"],
              "when": mr_date(st["expected_by"]) if st.get("expected_by") else "—"}
             for k, st in enumerate((i.analysis.get("steps") or {}).get("next", []))]
    if query_gaps(i):
        proposal = "note.proposal.query"
    elif b.facts["premium_paid"]:
        proposal = "note.proposal.order"
    elif fee.get("total") is not None:
        proposal = "note.proposal.demand"
    else:
        proposal = "note.proposal.fee_pending"
    ctx.update(facts=facts,
               documents=documents, checks=checks, gaps=gaps, no_gaps=not gaps, fee_rows=fee_rows, fee_status=fee_status,
               steps=steps, proposal=b.text(proposal), closing=b.text("note.closing"))
    return ctx


BUILDERS = {"SCRUTINY_NOTE": build_scrutiny_note, "QUERY_LETTER": build_query_letter, "DEMAND_LETTER": build_demand_letter,
            "COLLECTOR_ORDER": build_collector_order}


def build(template_code: str, inputs: DraftInputs, mode: str) -> DocModel:
    if template_code not in BUILDERS:
        raise ValueError(f"unknown template {template_code!r}")
    if mode not in ("internal", "issue"):
        raise ValueError("mode must be internal | issue")
    b = Builder(inputs, template_code, mode)
    b.analysis_refs()  # every template: no precedent's ref / id / society may reach an issue render
    ctx = BUILDERS[template_code](b)
    if b.internal:
        cfg_unverified = inputs.cfg.unverified(["letterhead", "ref_format", "addressee", f"signatory.{'order' if template_code == 'COLLECTOR_ORDER' else 'note' if template_code == 'SCRUTINY_NOTE' else 'letter'}",
                                                "reply_days", "payment_days", "premium_head_of_account", "rule_set_names_mr"])
        if cfg_unverified:
            b.note("कार्यालयीन स्वरूप / मुदती अपुष्ट (to_verify): " + ", ".join(cfg_unverified))
    return b.finish(ctx)
