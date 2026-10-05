"""Intake report: checks across the uploaded documents, the verdict and the feedback for the case (pure).

Input is the stored per-document scrutiny (rci.intake.extract); nothing here calls a model. Every finding names the
evidence it used and, like rci.pipeline.validators, the parameters it depended on with their verification status.

Levels: problem (must be fixed before scrutiny) · warning (the officer should look) · info · unchecked (not enough
evidence to run the check) · ok. Verdict: incomplete (a required document is missing, unreadable or the wrong
document) · needs_attention (any other problem) · ready.

The rules in force on the reference date (rci.rules) decide which documents are required (rule_requirements whose
catalog entry names an intake slot; applicant documents without one must be among the additional documents) and what
each document must show (rule_document_checks, judged per document by the scrutiny). Every such finding cites its rule
row. A rule that is still to_verify is applied, but what it finds is at most a warning and is labelled "rule not yet
verified" — except a missing required document, which stays a problem: until the seeded rows are verified no case
could otherwise be incomplete. The hard-coded cross-document checks below (CROSS_CHECKS) stay as they are.

Files the intake identified itself (rci.intake.identify) are judged as what they were identified as; a disagreement
between identification and scrutiny is a point for the officer, not a wrong upload. Additional documents (slots.OTHER)
are listed with what they are, flagged when they do not concern the application, and — when they do — their land
identifiers join the cross-document checks.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation

from rci.config import Parameters
from rci.intake.slots import BY_KEY, OTHER, SLOTS
from rci.pipeline.extract.common import society_key
from rci.pipeline.normalise import ascii_digits
from rci.rules.resolve import RuleStack, document_checks_for, requirements_for

INTAKE_VERSION = "intake.v2"
LEVEL_ORDER = {"problem": 0, "warning": 1, "info": 2, "unchecked": 3, "ok": 4}
BLOCKING = {"DOC_MISSING", "DOC_FAILED", "DOC_UNREADABLE", "DOC_WRONG_TYPE"}
TENURE_TEXT = {"occupancy_class_2": "Occupancy Class-II", "govt_lease": "Government lease", "occupancy_class_1": "Occupancy Class-I"}
TOKEN_RE = re.compile(r"\[[A-Z_]+_[0-9a-f]{8}\]")
NIL_RE = re.compile(r"^\s*(?:-+|—|nil|none|निरंक|नाही|काही\s*नाही)?\s*$", re.I)
UNVERIFIED_RULE = "rule not yet verified"


@dataclass
class DocIn:
    document_id: str
    filename: str
    slot: str
    result: dict | None  # rci.intake.extract.scrutinise_document output; None when not scrutinised
    error: str | None = None  # the file could not be processed at all
    identified: dict | None = None  # rci.intake.identify output when the intake decided the slot


@dataclass
class Finding:
    code: str
    level: str
    title: str
    detail: str = ""
    slots: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    action: str | None = None  # what the applicant has to do, when it is theirs to fix
    source: str = "check"  # check | ai (a model observation — verify on the cited page) | rule (a rule_document_checks verdict)
    parameters: list[dict] = field(default_factory=list)
    rule: dict | None = None  # the rule row the finding rests on: code, rule_set_code, source_ref, status, label

    def to_json(self) -> dict:
        d = asdict(self)
        d["depends_on_unverified"] = any(p["status"] != "verified" for p in self.parameters)
        return d


# ------------------------------------------------------------------------------------------------
# Evidence helpers
# ------------------------------------------------------------------------------------------------
class Docs:
    def __init__(self, docs: list[DocIn]):
        self.docs = docs
        self.by_slot: dict[str, list[DocIn]] = {}
        for d in docs:
            self.by_slot.setdefault(d.slot, []).append(d)

    def values(self, slot: str, name: str) -> list[tuple[DocIn, dict]]:
        out = []
        for d in self.by_slot.get(slot, []):
            if (d.result or {}).get("relevance") == "unrelated":  # an additional document about something else
                continue
            ev = ((d.result or {}).get("fields") or {}).get(name)
            if ev and ev.get("value") not in (None, ""):
                out.append((d, ev))
        return out

    def first(self, slot: str, name: str) -> tuple[DocIn, dict] | None:
        v = self.values(slot, name)
        return v[0] if v else None

    def first_of(self, sources: list[tuple[str, str]]) -> tuple[DocIn, dict, str] | None:
        for slot, name in sources:
            hit = self.first(slot, name)
            if hit:
                return hit[0], hit[1], name
        return None


def ref(d: DocIn, name: str, ev: dict) -> dict:
    f = next((f for f in BY_KEY[d.slot].fields if f.name == name), None) if d.slot in BY_KEY else None
    return {"slot": d.slot, "document_id": d.document_id, "filename": d.filename, "field": name,
            "label": f.label if f else name, "value": ev.get("value"), "raw": ev.get("raw"),
            "page_seq": ev.get("page_seq"), "snippet": ev.get("snippet")}


def _param(params: Parameters, key: str) -> dict:
    p = params.get(key)
    return {"key": key, "value": p.value, "status": p.status}


def _date(ev: dict | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(ev["value"])) if ev and ev.get("value") else None
    except ValueError:
        return None


def _decimal(v) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


def _base_cts(v: str) -> str:
    return str(v).removesuffix("PT")


def survey_key(v: str) -> str:
    """'स.नं. १२/३ अ' and 'S. No. 12/3A' → '12/3अ' / '12/3A': the number with its hissa, prefixes and spaces dropped."""
    s = ascii_digits(str(v))
    m = re.search(r"\d+(?:\s*/\s*[0-9A-Za-zऀ-ॿ]+)*(?:\s*[A-Za-zऀ-ॿ](?![A-Za-zऀ-ॿ]))?", s)
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def _slot_label(key: str) -> str:
    return BY_KEY[key].label if key in BY_KEY else "document"


def _the(d: DocIn) -> str:
    """How a finding names the document: its slot, or its filename when it has no standard slot."""
    return f"the {_slot_label(d.slot)}" if d.slot in BY_KEY and d.slot != OTHER.key else d.filename


def _doc_ev(d: DocIn, page_seq=None) -> list[dict]:
    seq = page_seq if page_seq is not None else ((d.result or {}).get("pages") or [None])[0]
    return [{"slot": d.slot, "document_id": d.document_id, "filename": d.filename, "page_seq": seq}] if seq else []


# ------------------------------------------------------------------------------------------------
# Per-document findings
# ------------------------------------------------------------------------------------------------
def document_findings(docs: Docs, stack: RuleStack, facts: dict) -> list[Finding]:
    out: list[Finding] = []
    reqs = [r for r in requirements_for(stack, facts) if r.get("category") == "applicant_document"]
    for slot in SLOTS:
        present = docs.by_slot.get(slot.key, [])
        if not present:
            out.append(_missing_slot(slot, [r for r in reqs if r.get("intake_slot") == slot.key], stack))
        elif len(present) > 1 and not slot.multiple:
            out.append(Finding("DOC_DUPLICATE", "info", f"{len(present)} files are the {slot.label}",
                               ", ".join(d.filename for d in present) + " — the checks use all of them; the officer "
                               "should confirm whether they are copies or cover different land.", [slot.key]))
    slotted = {s.key for s in SLOTS}
    out += [f for r in reqs if r.get("intake_slot") not in slotted and (f := _unslotted_requirement(docs, r, stack))]
    for d in docs.docs:
        label = _slot_label(d.slot)
        r = d.result or {}
        if d.error:
            out.append(Finding("DOC_FAILED", "problem", f"{d.filename} could not be processed", d.error[:300], [d.slot],
                               action=f"Upload {_the(d)} again as a PDF or image."))
            continue
        if not d.result:
            continue
        if not r.get("readable"):
            notes = r.get("notes") or []
            system_cause = any("OCR" in n for n in notes)
            out.append(Finding("DOC_UNREADABLE", "problem", f"No text could be read from {_the(d)}",
                               "; ".join(notes) or f"{d.filename} contains no readable text.", [d.slot],
                               action=None if system_cause else f"Upload a clearer scan or a text PDF of {_the(d)}."))
            continue
        if d.slot == OTHER.key:
            out.append(_additional(d))
        elif r.get("document_matches") is False and d.identified:
            seen = r.get("detected_as") or "a different document"
            out.append(Finding("DOC_TYPE_UNCERTAIN", "warning", f"Unsure what {d.filename} is",
                               f"It was identified as the {label}, but read in full it appears to be: {seen}. "
                               f"{r.get('match_reason') or ''} The officer should confirm.".replace("  ", " "), [d.slot],
                               evidence=_doc_ev(d)))
        elif r.get("document_matches") is False:
            seen = r.get("detected_as") or "a different document"
            out.append(Finding("DOC_WRONG_TYPE", "problem", f"{d.filename} does not look like a {label}",
                               f"It appears to be: {seen}. {r.get('match_reason') or ''}".strip(), [d.slot],
                               evidence=_doc_ev(d),
                               action=f"Upload the correct {label} — {BY_KEY[d.slot].label_mr}."))
        bundled = (d.identified or {}).get("bundled") or []
        if bundled:
            listed = "; ".join(bundled)
            out.append(Finding("DOC_BUNDLED", "warning", f"{d.filename} holds several documents",
                               f"Besides {_the(d)} it contains: {listed}. Only {_the(d)} was checked.", [d.slot],
                               evidence=_doc_ev(d),
                               action=f"Upload each document in {d.filename} as a separate file ({listed})."))
        for o in r.get("observations") or []:
            if o["severity"] in ("problem", "warning"):
                out.append(Finding("DOC_OBSERVATION", o["severity"], o["text"], f"Noted on the {label} ({d.filename}).",
                                   [d.slot], evidence=_doc_ev(d, o.get("page_seq") or 0), source="ai"))
    return out


# ------------------------------------------------------------------------------------------------
# Rules in force: required documents and what each document must show
# ------------------------------------------------------------------------------------------------
def case_tenure(docs: Docs) -> str | None:
    """The tenure the rule conditions are evaluated with: the land records', else the grant's / application's; None
    when it is not stated or the documents disagree (TENURE_ELIGIBLE reports that)."""
    for slots in (("satbara", "property_card"), ("grant", "application")):
        kinds = {ev["value"] for _, ev in _tenures(docs, slots)}
        if kinds:
            return kinds.pop() if len(kinds) == 1 else None
    return None


def rule_ref(stack: RuleStack, row: dict) -> dict:
    """How a finding cites its rule row. Verified only when the row and its rule set both are."""
    rs = stack.rule_set(row["rule_set_code"])
    verified = row["status"] == "verified" and rs.status == "verified"
    return {"code": row["code"], "rule_set_code": rs.code, "rule_set_name": rs.name, "source_ref": row.get("source_ref"),
            "status": "verified" if verified else "to_verify", "label": None if verified else UNVERIFIED_RULE}


def _cited(ref: dict) -> str:
    return f"{ref['rule_set_code']}" + (f", {ref['source_ref']}" if ref.get("source_ref") else "")


def _condition_text(condition: dict) -> str:
    parts = []
    for key, spec in (condition or {}).items():
        ops = spec if isinstance(spec, dict) else {"eq": spec}
        for op, v in ops.items():
            shown = ", ".join(TENURE_TEXT.get(x, str(x)) for x in v) if isinstance(v, list) else TENURE_TEXT.get(v, str(v))
            parts.append(f"{key} {'is' if op == 'eq' else op.replace('_', ' ')} {shown}")
    return " and ".join(parts) or "a condition holds"


def _condition_of(stack: RuleStack, row: dict) -> dict:
    """The condition of the row that won the stacking (requirements_for does not carry it)."""
    for r in stack.requirements:
        if r.rule_set_code == row["rule_set_code"] and r.code == row["code"]:
            return r.condition
    return row.get("condition") or {}


def _need(rows: list[dict]) -> tuple[str, dict | None]:
    """How strongly the rules ask for a document — required, maybe (a condition needs a fact the documents do not give)
    or optional — and the requirement row that decides it."""
    mandatory = [r for r in rows if r.get("mandatory", True)]
    for applies, need in ((True, "required"), (None, "maybe")):
        row = next((r for r in mandatory if r["applies"] is applies), None)
        if row:
            return need, row
    return "optional", next(iter(rows), None)


def _missing_slot(slot, rows: list[dict], stack: RuleStack) -> Finding:
    need, row = _need(rows)
    ref = rule_ref(stack, row) if row else None
    if need == "required":
        return Finding("DOC_MISSING", "problem", f"{slot.label} not uploaded", f"Required by {_cited(ref)}.", [slot.key],
                       action=f"Upload the {slot.label} — {slot.label_mr}.", rule=ref)
    if need == "maybe":
        return Finding("DOC_MISSING", "unchecked", f"{slot.label} not uploaded — it may be required",
                       f"{_cited(ref)} requires it when {_condition_text(_condition_of(stack, row))}; the "
                       f"{', '.join(row['needs'])} could not be determined from the documents.", [slot.key], rule=ref)
    why = (f"{_cited(ref)} requires it only when {_condition_text(_condition_of(stack, row))}."
           if row and row["applies"] is False else "")
    return Finding("DOC_MISSING", "info", f"{slot.label} not uploaded (optional)", why, [slot.key], rule=ref)


def _unslotted_requirement(docs: Docs, row: dict, stack: RuleStack) -> Finding | None:
    """A required applicant document without an upload slot of its own (e.g. the general body resolution): one of the
    additional documents has to be it (the scrutiny's `satisfies_requirement`)."""
    if not row.get("mandatory", True) or row["applies"] is False:
        return None
    ref = rule_ref(stack, row)
    name, name_mr = row.get("name_en") or row["code"], row.get("name_mr")
    others = [d for d in docs.by_slot.get(OTHER.key, []) if (d.result or {}).get("relevance") != "unrelated"]
    hit = next((d for d in others if (d.result or {}).get("satisfies_requirement") == row["code"]), None)
    if hit:
        return Finding("DOC_PRESENT", "ok", f"{name}: {hit.filename}", f"Required by {_cited(ref)}.", [OTHER.key],
                       evidence=_doc_ev(hit), rule=ref)
    if row["applies"] is None:
        return Finding("DOC_MISSING", "unchecked", f"{name} not found — it may be required",
                       f"{_cited(ref)} requires it when {_condition_text(_condition_of(stack, row))}; the "
                       f"{', '.join(row['needs'])} could not be determined from the documents.", [OTHER.key], rule=ref)
    if any((d.result or {}).get("readable") and d.result.get("method") != "llm" for d in others):
        return Finding("DOC_MISSING", "unchecked", f"Could not tell whether the {name} was filed",
                       f"Required by {_cited(ref)}. Additional documents are recognised by the AI reader, and some were "
                       "read without it.", [OTHER.key], rule=ref)
    return Finding("DOC_MISSING", "problem", f"{name} not uploaded", f"Required by {_cited(ref)}.", [OTHER.key],
                   action=f"Upload the {name}" + (f" — {name_mr}." if name_mr else "."), rule=ref)


def rule_findings(docs: Docs, stack: RuleStack, facts: dict) -> list[Finding]:
    """One finding per rule_document_checks verdict stored with a document, judged with the rows in force now (a rule
    dropped since the document was read is ignored; one whose condition is false for this case too)."""
    checks = {c["code"]: c for c in document_checks_for(stack, facts=facts)}
    out: list[Finding] = []
    for d in docs.docs:
        r = d.result or {}
        if d.error or not r.get("readable") or r.get("relevance") == "unrelated":
            continue
        for res in r.get("rule_results") or []:
            c = checks.get(res["code"])
            if c is None or c["slot"] != d.slot or c["applies"] is False or res["verdict"] == "not_applicable":
                continue
            out.append(_rule_finding(d, res, c, stack))
    return out


def _rule_finding(d: DocIn, res: dict, c: dict, stack: RuleStack) -> Finding:
    ref = rule_ref(stack, c)
    verdict, notes = res["verdict"], []
    if verdict == "met":
        level = "ok"
    elif verdict == "cannot_tell":
        level = "unchecked"
    else:
        level = c.get("severity") or "problem"
        if c["applies"] is None:
            notes.append(f"The rule applies only when {_condition_text(c.get('condition'))}, which the documents do not settle.")
        if level == "problem" and (ref["status"] != "verified" or c["applies"] is None):
            level = "warning"
    title = f"Not met: {c['text_en']}" if verdict == "not_met" else c["text_en"]
    detail = " ".join(x for x in [res.get("reason"), f"({_the(d)}, {d.filename}; {_cited(ref)})", *notes] if x)
    evidence = ([{"slot": d.slot, "document_id": d.document_id, "filename": d.filename, "page_seq": res["page_seq"],
                  "raw": res.get("quote"), "snippet": res.get("snippet")}] if res.get("page_seq") else _doc_ev(d))
    action = f"Correct {_the(d)}: {c['text_en']}" if verdict == "not_met" and level in ("problem", "warning") else None
    return Finding("RULE_CHECK", level, title, detail, [d.slot], evidence, action, source="rule", rule=ref)


def _additional(d: DocIn) -> Finding:
    r = d.result or {}
    what = r.get("detected_as") or (d.identified or {}).get("detected_as") or "a document of unknown type"
    if r.get("relevance") == "unrelated":
        return Finding("DOC_UNRELATED", "warning", f"{d.filename} does not seem to belong to this case",
                       f"It appears to be: {what}. {r.get('summary') or ''}".strip(), [d.slot], evidence=_doc_ev(d),
                       action=f"Remove {d.filename} from the case file, or explain why it was filed.")
    return Finding("DOC_ADDITIONAL", "info", f"Additional document: {what}",
                   r.get("summary") or f"{d.filename} is not one of the standard filing documents.", [d.slot],
                   evidence=_doc_ev(d))


# ------------------------------------------------------------------------------------------------
# Cross-document checks
# ------------------------------------------------------------------------------------------------
def cts_match(docs: Docs, params: Parameters, ref_date) -> Finding:
    obs = [(d, ev) for s in ("property_card", "application", "grant", "index_ii", "other") for d, ev in docs.values(s, "cts_no")]
    refs = [ref(d, "cts_no", ev) for d, ev in obs]
    slots = sorted({d.slot for d, _ in obs})
    if len(obs) < 2:
        return Finding("LAND_CTS_MATCH", "unchecked", "CTS number could not be compared",
                       "The CTS number was found on fewer than two documents.", slots, refs)
    values = sorted({_base_cts(ev["value"]) for _, ev in obs})
    if len(values) == 1:
        return Finding("LAND_CTS_MATCH", "ok", f"CTS no. {values[0]} is the same on all {len(obs)} documents", "", slots, refs)
    return Finding("LAND_CTS_MATCH", "problem", "The documents give different CTS numbers",
                   f"Found: {', '.join(values)}.", slots, refs,
                   action=f"Correct or explain the CTS number — the documents give {', '.join(values)}.")


def survey_match(docs: Docs, params: Parameters, ref_date) -> Finding:
    obs = [(d, ev) for s in ("satbara", "grant", "application", "index_ii", "other") for d, ev in docs.values(s, "survey_no")]
    obs = [(d, ev) for d, ev in obs if survey_key(ev["value"])]
    refs = [ref(d, "survey_no", ev) for d, ev in obs]
    slots = sorted({d.slot for d, _ in obs})
    if len(obs) < 2:
        return Finding("LAND_SURVEY_MATCH", "unchecked", "Survey / Gat number could not be compared",
                       "The survey number was found on fewer than two documents.", slots, refs)
    values = sorted({survey_key(ev["value"]) for _, ev in obs})
    if len(values) == 1:
        return Finding("LAND_SURVEY_MATCH", "ok", f"Survey no. {values[0]} is the same on all {len(obs)} documents", "", slots, refs)
    return Finding("LAND_SURVEY_MATCH", "warning", "The documents give different survey numbers",
                   f"Found: {', '.join(values)}. Hissa numbers or old and new numbering can differ — the officer should confirm.",
                   slots, refs)


def area_match(docs: Docs, params: Parameters, ref_date) -> Finding:
    ps = [_param(params, "area_tolerance_pct"), _param(params, "area_tolerance_abs_sqm")]
    obs = [(d, ev, _decimal(ev["value"])) for s in ("property_card", "application", "grant", "other") for d, ev in docs.values(s, "area_sqm")]
    obs = [o for o in obs if o[2] is not None]
    refs = [ref(d, "area_sqm", ev) for d, ev, _ in obs]
    slots = sorted({d.slot for d, _, _ in obs})
    if len(obs) < 2:
        return Finding("LAND_AREA_MATCH", "unchecked", "Land area could not be compared",
                       "The area (in sq m / sq ft) was found on fewer than two documents.", slots, refs, parameters=ps)
    base_doc, _, base = obs[0]  # property card first when uploaded, else the application
    tol = max(base * Decimal(str(params.value("area_tolerance_pct"))) / 100, Decimal(str(params.value("area_tolerance_abs_sqm"))))
    off = [(d, a) for d, _, a in obs[1:] if abs(a - base) > tol]
    if not off:
        return Finding("LAND_AREA_MATCH", "ok", f"Land area agrees ({base} sq m, within {tol:.2f} sq m)", "", slots, refs, parameters=ps)
    diffs = "; ".join(f"{_slot_label(d.slot)} {a} sq m" for d, a in off)
    return Finding("LAND_AREA_MATCH", "problem", "The land area differs between documents",
                   f"{_slot_label(base_doc.slot)} gives {base} sq m; {diffs} (tolerance {tol:.2f} sq m).", slots, refs,
                   action="Reconcile the land area across the documents, or explain the difference (e.g. a later measurement).",
                   parameters=ps)


def _tenures(docs: Docs, slots: tuple[str, ...]) -> list[tuple[DocIn, dict]]:
    return [(d, ev) for s in slots for d, ev in docs.values(s, "tenure") if ev["value"] in TENURE_TEXT]


def holder_is_society(docs: Docs, params: Parameters, ref_date) -> Finding:
    soc = docs.first_of([("application", "society_name"), ("grant", "grantee")])
    holders = docs.values("property_card", "holder_name") + docs.values("satbara", "holder_name")
    refs = [ref(d, "holder_name", ev) for d, ev in holders]
    if soc is None or not holders:
        return Finding("HOLDER_IS_SOCIETY", "unchecked", "Could not check that the land is recorded in the society's name",
                       "Need the society's name (application) and the holder on the 7/12 or property card.",
                       sorted({d.slot for d, _ in holders}), refs)
    sd, sev, sname = soc
    refs.append(ref(sd, sname, sev))
    sk = society_key(str(sev["value"]))
    other = [(d, ev) for d, ev in holders if not (sk and (sk in society_key(str(ev["value"])) or society_key(str(ev["value"])) in sk))]
    slots = sorted({d.slot for d, _ in holders} | {sd.slot})
    if not other:
        return Finding("HOLDER_IS_SOCIETY", "ok", "The land records show the applicant society as holder", "", slots, refs)
    lease = any(ev["value"] == "govt_lease" for _, ev in _tenures(docs, ("satbara", "property_card", "grant")))
    names = "; ".join(f"{_slot_label(d.slot)}: {ev['value']}" for d, ev in other)
    return Finding("HOLDER_IS_SOCIETY", "warning" if lease else "problem",
                   "The land records do not show the applicant society as holder",
                   f"Applicant: {sev['value']}. Recorded holder — {names}."
                   + (" For government-lease land the record may name the State; the officer should confirm." if lease else ""),
                   slots, refs,
                   action=None if lease else "Explain why the land is recorded in another name, or have the record corrected.")


def tenure_eligible(docs: Docs, params: Parameters, ref_date) -> Finding:
    records = _tenures(docs, ("satbara", "property_card"))
    stated = _tenures(docs, ("grant", "application"))
    refs = [ref(d, "tenure", ev) for d, ev in records + stated]
    slots = sorted({d.slot for d, _ in records + stated})
    basis = records or stated
    if not basis:
        return Finding("TENURE_ELIGIBLE", "unchecked", "The occupancy class of the land was not found",
                       "Neither the 7/12 extract nor the property card states the tenure in a recognised form.", slots, refs)
    kinds = sorted({ev["value"] for _, ev in basis})
    if "occupancy_class_1" in kinds:
        return Finding("TENURE_ELIGIBLE", "problem", "The land is already recorded as Occupancy Class-I",
                       "Conversion to Class-I may not be needed.", slots, refs,
                       action="Explain why conversion is sought for land already recorded as Occupancy Class-I.")
    if len(kinds) > 1:
        return Finding("TENURE_ELIGIBLE", "warning", "The documents record the tenure differently",
                       "Found: " + ", ".join(TENURE_TEXT[k] for k in kinds) + ".", slots, refs)
    src = "land records" if records else "grant / application (not the land records)"
    return Finding("TENURE_ELIGIBLE", "ok" if records else "warning", f"Land is held as {TENURE_TEXT[kinds[0]]} — eligible for conversion",
                   f"According to the {src}.", slots, refs)


def _recency(code: str, slot: str, name: str, param: str, docs: Docs, params: Parameters, ref_date: tuple[dt.date, str]) -> Finding | None:
    ps = [_param(params, param)]
    label = _slot_label(slot)
    if not docs.by_slot.get(slot):
        return None
    dated = [(d, ev, _date(ev)) for d, ev in docs.values(slot, name)]
    dated = [x for x in dated if x[2]]
    if not dated:
        return Finding(code, "unchecked", f"The date of the {label} was not found", "", [slot], parameters=ps)
    d, ev, when = max(dated, key=lambda x: x[2])
    ref_day, ref_what = ref_date
    age, limit = (ref_day - when).days, int(params.value(param))
    refs = [ref(d, name, ev)]
    if age > limit:
        return Finding(code, "problem", f"The {label} is {age} days old (limit {limit})",
                       f"Dated {when.isoformat()}, compared with {ref_what} {ref_day.isoformat()}.", [slot], refs,
                       action=f"Upload a {label} issued within the last {limit} days.", parameters=ps)
    return Finding(code, "ok", f"The {label} is recent ({max(age, 0)} days old, limit {limit})",
                   f"Dated {when.isoformat()}, compared with {ref_what} {ref_day.isoformat()}.", [slot], refs, parameters=ps)


def property_card_recent(docs, params, ref_date):
    return _recency("PROPERTY_CARD_RECENT", "property_card", "issue_date", "property_card_max_age_days", docs, params, ref_date)


def satbara_recent(docs, params, ref_date):
    return _recency("SATBARA_RECENT", "satbara", "issue_date", "satbara_max_age_days", docs, params, ref_date)


def tax_receipt_recent(docs, params, ref_date):
    return _recency("TAX_RECEIPT_RECENT", "tax_receipts", "receipt_date", "tax_receipt_max_age_days", docs, params, ref_date)


def tax_arrears(docs: Docs, params: Parameters, ref_date) -> Finding | None:
    obs = [(d, ev, _decimal(ev["value"])) for d, ev in docs.values("tax_receipts", "arrears")]
    obs = [o for o in obs if o[2] is not None]
    if not obs:
        return None
    refs = [ref(d, "arrears", ev) for d, ev, _ in obs]
    due = [a for _, _, a in obs if a > 0]
    if due:
        total = max(due)
        return Finding("TAX_ARREARS", "problem", f"Property tax arrears of ₹{total:,} are outstanding",
                       "A receipt records a balance still due.", ["tax_receipts"], refs,
                       action="Pay the outstanding property tax and upload the receipt showing nil arrears.")
    return Finding("TAX_ARREARS", "ok", "No property tax arrears outstanding", "", ["tax_receipts"], refs)


def application_addressed(docs: Docs, params: Parameters, ref_date) -> Finding | None:
    if not docs.by_slot.get("application"):
        return None
    hit = docs.first("application", "addressed_to")
    if hit is None:
        return Finding("APPLICATION_ADDRESSEE", "unchecked", "Could not find whom the application is addressed to", "", ["application"])
    d, ev = hit
    if re.search(r"जिल्हाधिकारी|collector", str(ev["value"]), re.I):
        return Finding("APPLICATION_ADDRESSEE", "ok", "The application is addressed to the Collector", "", ["application"], [ref(d, "addressed_to", ev)])
    return Finding("APPLICATION_ADDRESSEE", "warning", "The application is not addressed to the District Collector",
                   f"Addressed to: {ev['value']}.", ["application"], [ref(d, "addressed_to", ev)],
                   action="Address the application to the District Collector (मा. जिल्हाधिकारी).")


def signatory_identity(docs: Docs, params: Parameters, ref_date) -> Finding | None:
    if not docs.by_slot.get("aadhaar"):
        return None
    sig, card = docs.first("application", "signatory"), docs.first("aadhaar", "name")
    if sig is None or card is None:
        return Finding("SIGNATORY_IDENTITY", "unchecked", "Could not compare the identity proof with the application's signatory",
                       "Need the signatory on the application and the name on the Aadhaar card.", ["application", "aadhaar"])
    refs = [ref(sig[0], "signatory", sig[1]), ref(card[0], "name", card[1])]
    a, b = str(sig[1]["value"]), str(card[1]["value"])
    # names are PII tokens, and the vault gives the same name the same token within a case
    same = bool(set(TOKEN_RE.findall(a)) & set(TOKEN_RE.findall(b))) or (society_key(b) and society_key(b) in society_key(a))
    if same:
        return Finding("SIGNATORY_IDENTITY", "ok", "The identity proof belongs to the person who signed the application", "",
                       ["application", "aadhaar"], refs)
    return Finding("SIGNATORY_IDENTITY", "warning", "The name on the identity proof does not match the application's signatory",
                   "Names written in different scripts do not match automatically — the officer should confirm.",
                   ["application", "aadhaar"], refs,
                   action="Upload the identity proof of the office-bearer who signed the application.")


def other_rights(docs: Docs, params: Parameters, ref_date) -> Finding | None:
    hits = [(d, ev) for d, ev in docs.values("satbara", "other_rights") if not NIL_RE.match(str(ev["value"]))]
    if not hits:
        return None
    d, ev = hits[0]
    return Finding("SATBARA_OTHER_RIGHTS", "warning", "The 7/12 extract records other rights or encumbrances",
                   f"इतर हक्क: {ev['value']}", ["satbara"], [ref(d, "other_rights", ev)])


def grant_conditions(docs: Docs, params: Parameters, ref_date) -> Finding | None:
    hit = docs.first("grant", "conditions")
    if hit is None:
        return None
    d, ev = hit
    return Finding("GRANT_CONDITIONS", "info", "The original grant carries conditions",
                   f"{ev['value']} — the officer should check they have been complied with.", ["grant"], [ref(d, "conditions", ev)])


CROSS_CHECKS = [tenure_eligible, holder_is_society, cts_match, survey_match, area_match, property_card_recent,
                satbara_recent, tax_receipt_recent, tax_arrears, application_addressed, signatory_identity, other_rights,
                grant_conditions]


# ------------------------------------------------------------------------------------------------
# Report
# ------------------------------------------------------------------------------------------------
FACTS = [
    ("Society", [("application", "society_name"), ("grant", "grantee")]),
    ("CTS no.", [("property_card", "cts_no"), ("application", "cts_no"), ("grant", "cts_no"), ("index_ii", "cts_no")]),
    ("Survey / Gat no.", [("satbara", "survey_no"), ("grant", "survey_no"), ("application", "survey_no")]),
    ("Village", [("satbara", "village"), ("property_card", "village"), ("application", "village")]),
    ("Area", [("property_card", "area_sqm"), ("application", "area_sqm"), ("grant", "area_sqm"), ("satbara", "area")]),
    ("Tenure", [("satbara", "tenure"), ("property_card", "tenure"), ("grant", "tenure"), ("application", "tenure")]),
    ("Application date", [("application", "application_date")]),
    ("Granted on", [("grant", "grant_date")]),
    ("Last registered transaction", [("index_ii", "registration_date")]),
]


def reference_date(docs: Docs, today: dt.date) -> tuple[dt.date, str]:
    app = docs.first("application", "application_date")
    when = _date(app[1]) if app else None
    return (when, "the application date") if when else (today, "today (the application is undated)")


def build_report(docs_in: list[DocIn], params: Parameters, stack: RuleStack, *, today: dt.date, llm_provider: str) -> dict:
    """`stack`: the rules in force on the reference date (reference_date — the service loads it)."""
    docs = Docs(docs_in)
    ref_date = reference_date(docs, today)
    facts = {"tenure": case_tenure(docs)}
    findings = document_findings(docs, stack, facts)
    findings += rule_findings(docs, stack, facts)
    findings += [f for check in CROSS_CHECKS if (f := check(docs, params, ref_date)) is not None]
    if not stack.rule_sets:
        findings.append(Finding("RULES_NONE_IN_FORCE", "warning", f"No rule set is in force on {stack.on.isoformat()}",
                                "The required documents and what each must show come from the rules; none were applied. "
                                "Check the rule sets' dates in the rules admin."))
    scrutinised = [d for d in docs.docs if d.result and d.result.get("readable")]
    limited = bool(scrutinised) and all(d.result["method"] != "llm" for d in scrutinised)
    if limited:
        notes = sorted({n for d in scrutinised for n in d.result.get("notes", [])})
        findings.append(Finding("READING_LIMITED", "info", "Documents were read with pattern rules only",
                                "Wrong-document detection, per-document observations and the rules' document checks "
                                "need the LLM. "
                                + " ".join(n[:1].upper() + n[1:] + "." for n in notes)))
    findings.sort(key=lambda f: LEVEL_ORDER[f.level])
    counts = {k: sum(1 for f in findings if f.level == k) for k in LEVEL_ORDER}
    blocking = [f for f in findings if f.level == "problem" and f.code in BLOCKING]
    if blocking:
        verdict = "incomplete"
        n = len({(s, (f.rule or {}).get("code")) for f in blocking for s in f.slots})
        headline = f"{n} required document{'s are' if n != 1 else ' is'} missing or unusable — the case cannot be scrutinised yet."
    elif counts["problem"]:
        verdict = "needs_attention"
        headline = f"All documents received, but {counts['problem']} problem{'s' if counts['problem'] != 1 else ''} must be resolved."
    else:
        verdict = "ready"
        headline = "All documents received and consistent — ready for the officer's scrutiny."
        if counts["warning"]:
            headline += f" {counts['warning']} point{'s' if counts['warning'] != 1 else ''} to note."
    actions: list[str] = []
    for f in findings:
        if f.action and f.level in ("problem", "warning") and f.action not in actions:
            actions.append(f.action)
    fact_rows = []
    for label, sources in FACTS:
        hit = docs.first_of(sources)
        if hit:
            d, ev, name = hit
            fact_rows.append({**ref(d, name, ev), "fact": label})
    return {
        "version": INTAKE_VERSION, "verdict": verdict, "headline": headline, "limited": limited, "llm_provider": llm_provider,
        "reference_date": ref_date[0].isoformat(), "reference_date_source": ref_date[1],
        "counts": counts, "actions": actions, "facts": fact_rows, "findings": [f.to_json() for f in findings],
        "rules_applied": stack.to_json(), "rule_facts": facts,
    }
