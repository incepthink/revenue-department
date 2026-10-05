"""Review working state: what a reviewer has decided so far, and everything derived from it.

The working state is plain JSON (review_assignments.working), so two reviewers in double-review mode work
independently and nothing touches exhibits / cards until approval.

    {
      "version": 1,
      "base_card_id": ..., "base_card_version": 3, "base_card_status": "draft",
      "draft_exhibits": [...],          # immutable snapshot of the machine draft (for correction logging)
      "exhibits": [ {exhibit_id, type, stage, start, end, confidence, method, origin, extraction} ],
      "decisions": { exhibit_id: { field: {action, value, page_seq, raw} } },
      "query_map": { "exhibit_id#index": {"codes": [...], "not_requirement": false} },
      "requirements": { code: {status, satisfied_by_exhibit_id, notes} },     # overrides only
      "events": null | [ {code, date, exhibit_id, page_seq} ]                # null = derived from exhibits
    }

`apply_op` mutates it; `derive` turns it into reviewed exhibit facts → validations → card facts,
requirements and events, using the same card assembly code as the pipeline.
"""
from __future__ import annotations

import copy
import datetime as dt
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from rci.config import Parameters
from rci.pipeline.card import CardExhibit, assemble_card, doc_date_of, suggest_codes
from rci.pipeline.extract.common import PageText, jsonable, make_evidence, money, normalise_cts, parse_date, parse_decimal
from rci.pipeline.extract.extractor import _locate, extract_exhibit
from rci.pipeline.extract.fields import FIELDS, FieldSpec
from rci.pipeline.normalise import ascii_digits
from rci.pipeline.validators import ExhibitFacts, run_validators

STATE_VERSION = 1
STAGES = ("filing", "query_response", "internal", "final")
FIELD_ACTIONS = ("accept", "edit", "not_present")
REQ_STATUSES = ("present", "missing", "invalid", "queried", "waived", "not_applicable")
NOT_A_REQUIREMENT = "NOT_A_REQUIREMENT"


class ReviewError(ValueError):
    """A reviewer operation that cannot be applied (→ HTTP 422)."""


@dataclass
class PageInfo:
    id: uuid.UUID
    seq: int
    page_no: int
    document: str
    text: str
    ascii: str

    def page_text(self) -> PageText:
        return PageText(self.id, self.seq, self.page_no, self.document, self.text, self.ascii)


@dataclass
class ReviewContext:
    case_id: uuid.UUID
    case: dict  # card.case block
    data_class: str
    pages: dict[int, PageInfo]
    exhibit_types: dict[str, dict]  # code -> {party, default_stage, name_en, name_mr}
    requirement_catalog: list[dict]
    event_catalog: list[dict]
    params: Parameters
    llm: Any = None
    pipeline_version: str = "p1.0"


def exhibit_uuid(case_id: uuid.UUID, exhibit_type: str, start: int, end: int) -> str:
    # same scheme as the pipeline (rci.pipeline.runner.exhibit_uuid)
    return str(uuid.uuid5(case_id, f"{exhibit_type}:{start}-{end}"))


def qkey(exhibit_id: str, index: int) -> str:
    return f"{exhibit_id}#{index}"


# ------------------------------------------------------------------------------------------------
# Initial state
# ------------------------------------------------------------------------------------------------
def initial_state(base_card: dict, base_card_id: str, base_card_version: int, base_card_status: str,
                  extractions: dict[str, dict]) -> dict:
    """Working state from a card and the exhibits' stored extraction (exhibit_id -> extraction JSON).

    From an approved card (a re-review) every field starts as accepted; from a machine draft nothing is decided."""
    exhibits = []
    for e in base_card["exhibits"]:
        ext = extractions.get(e["exhibit_id"]) or {"fields": {}, "methods": [], "unverified": []}
        exhibits.append({
            "exhibit_id": e["exhibit_id"], "type": e["type"], "stage": e["stage"], "start": e["pages"][0], "end": e["pages"][-1],
            "confidence": e["confidence"], "method": e["classification_method"], "origin": "auto",
            "extraction": {"fields": ext.get("fields", {}), "methods": ext.get("methods", []), "unverified": ext.get("unverified", [])},
        })
    state = {
        "version": STATE_VERSION, "base_card_id": base_card_id, "base_card_version": base_card_version,
        "base_card_status": base_card_status,
        "draft_exhibits": [{k: e[k] for k in ("exhibit_id", "type", "stage", "start", "end")} | {"fields": e["extraction"]["fields"]}
                           for e in exhibits],
        "exhibits": exhibits, "decisions": {}, "query_map": {}, "requirements": {}, "events": None,
    }
    if base_card_status == "approved":
        for e in exhibits:
            state["decisions"][e["exhibit_id"]] = {spec.name: {"action": "accept"} for spec in FIELDS.get(e["type"], [])}
        for q in base_card["facts"]["queries"]:
            for i, it in enumerate(q["items"]):
                if "requirement_codes" in it:
                    state["query_map"][qkey(q["exhibit_id"], i)] = {
                        "codes": it["requirement_codes"], "not_requirement": it.get("mapping") == "not_requirement"}
        for r in base_card["requirements"]:
            if r.get("origin") == "review":
                state["requirements"][r["code"]] = {"status": r["status"], "satisfied_by_exhibit_id": r["satisfied_by_exhibit_id"],
                                                    "notes": r.get("notes")}
        state["events"] = [{"code": e["code"], "date": e["date"], "exhibit_id": e["exhibit_id"],
                            "page_seq": (e.get("evidence") or {}).get("page_seq")} for e in base_card["events"]]
    return state


# ------------------------------------------------------------------------------------------------
# Value parsing for manual edits
# ------------------------------------------------------------------------------------------------
def parse_manual_value(spec: FieldSpec, value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ReviewError(f"{spec.name}: value required (use not_present for an absent field)")
    s = ascii_digits(str(value)).strip()
    if spec.kind == "date":
        try:
            return dt.date.fromisoformat(s).isoformat()
        except ValueError:
            d = parse_date(s)
            if d is None:
                raise ReviewError(f"{spec.name}: not a date: {value!r} (use YYYY-MM-DD or DD/MM/YYYY)")
            return d.isoformat()
    if spec.kind in ("amount", "area"):
        d = parse_decimal(s.replace("₹", "").replace(" ", ""))
        if d is None or d < 0:
            raise ReviewError(f"{spec.name}: not a non-negative number: {value!r}")
        return str(money(d))
    if spec.kind == "int":
        if not re.fullmatch(r"\d+", s):
            raise ReviewError(f"{spec.name}: not a whole number: {value!r}")
        return int(s)
    if spec.kind == "bool":
        if isinstance(value, bool):
            return value
        if s.lower() in ("true", "yes", "होय", "1"):
            return True
        if s.lower() in ("false", "no", "नाही", "0"):
            return False
        raise ReviewError(f"{spec.name}: not yes/no: {value!r}")
    if spec.kind == "enum":
        if s not in spec.enum:
            raise ReviewError(f"{spec.name}: must be one of {list(spec.enum)}")
        return s
    if spec.kind == "cts":
        return normalise_cts(s)
    return re.sub(r"\s+", " ", str(value)).strip()


def manual_evidence(page: PageInfo, value: Any, raw: str | None) -> dict:
    """Evidence for a reviewer-entered value. `raw` (the text as written on the page) is located on the
    page when possible so the snippet shows the context; otherwise the snippet is the raw text itself."""
    raw = (raw or "").strip() or str(value)
    span = _locate(raw, page.page_text())
    if span:
        ev = make_evidence(page.page_text(), span[0], span[1], value, 1.0, method="manual").to_json()
        ev["located"] = True
    else:
        ev = {"value": jsonable(value), "raw": raw, "page_seq": page.seq, "doc_page_no": page.page_no, "document": page.document,
              "snippet": raw, "method": "manual", "confidence": 1.0, "page_id": str(page.id), "alternatives": [], "located": False}
    return ev


# ------------------------------------------------------------------------------------------------
# Operations
# ------------------------------------------------------------------------------------------------
def _find(state: dict, exhibit_id: str) -> tuple[int, dict]:
    for i, e in enumerate(state["exhibits"]):
        if e["exhibit_id"] == exhibit_id:
            return i, e
    raise ReviewError(f"unknown exhibit {exhibit_id}")


def _page(ctx: ReviewContext, seq: Any) -> PageInfo:
    try:
        return ctx.pages[int(seq)]
    except (KeyError, TypeError, ValueError):
        raise ReviewError(f"page_seq {seq!r} is not a page of this case")


def _extract(ctx: ReviewContext, exhibit_type: str, start: int, end: int) -> dict:
    texts = [ctx.pages[s].page_text() for s in range(start, end + 1)]
    return extract_exhibit(exhibit_type, texts, llm=ctx.llm, data_class=ctx.data_class)


def _new_exhibit(ctx: ReviewContext, exhibit_type: str, stage: str, start: int, end: int) -> dict:
    return {"exhibit_id": exhibit_uuid(ctx.case_id, exhibit_type, start, end), "type": exhibit_type, "stage": stage,
            "start": start, "end": end, "confidence": 1.0, "method": "manual", "origin": "manual",
            "extraction": _extract(ctx, exhibit_type, start, end)}


def _drop_references(state: dict, removed: set[str]) -> None:
    for eid in removed:
        state["decisions"].pop(eid, None)
    state["query_map"] = {k: v for k, v in state["query_map"].items() if k.split("#")[0] not in removed}
    for r in state["requirements"].values():
        if r.get("satisfied_by_exhibit_id") in removed:
            r["satisfied_by_exhibit_id"] = None
    for ev in state["events"] or []:
        if ev.get("exhibit_id") in removed:
            ev["exhibit_id"] = None


def _replace_exhibits(state: dict, index: int, count: int, new: list[dict]) -> None:
    old = state["exhibits"][index:index + count]
    state["exhibits"][index:index + count] = new
    _drop_references(state, {e["exhibit_id"] for e in old} - {e["exhibit_id"] for e in new})


def op_split(state, ctx, exhibit_id: str, at_page_seq: int, **_):
    i, e = _find(state, exhibit_id)
    at = int(at_page_seq)
    if not e["start"] < at <= e["end"]:
        raise ReviewError(f"split point must be in {e['start'] + 1}..{e['end']} (the first page of the second part)")
    _replace_exhibits(state, i, 1, [_new_exhibit(ctx, e["type"], e["stage"], e["start"], at - 1),
                                    _new_exhibit(ctx, e["type"], e["stage"], at, e["end"])])


def op_merge(state, ctx, exhibit_id: str, **_):
    i, e = _find(state, exhibit_id)
    if i + 1 >= len(state["exhibits"]):
        raise ReviewError("the last exhibit has nothing after it to merge with")
    nxt = state["exhibits"][i + 1]
    _replace_exhibits(state, i, 2, [_new_exhibit(ctx, e["type"], e["stage"], e["start"], nxt["end"])])


def op_retype(state, ctx, exhibit_id: str, type: str, **_):  # noqa: A002 - op payload key
    i, e = _find(state, exhibit_id)
    if type not in ctx.exhibit_types:
        raise ReviewError(f"unknown exhibit type {type!r}")
    if type == e["type"]:
        return
    _replace_exhibits(state, i, 1, [_new_exhibit(ctx, type, e["stage"], e["start"], e["end"])])


def op_stage(state, ctx, exhibit_id: str, stage: str, **_):
    _, e = _find(state, exhibit_id)
    if stage not in STAGES:
        raise ReviewError(f"stage must be one of {STAGES}")
    if e["stage"] != stage:
        e["stage"], e["origin"] = stage, "manual"


def op_field(state, ctx, exhibit_id: str, field: str, action: str, value: Any = None, page_seq: Any = None,
             raw: str | None = None, items: list | None = None, **_):
    _, e = _find(state, exhibit_id)
    spec = next((s for s in FIELDS.get(e["type"], []) if s.name == field), None)
    if spec is None:
        raise ReviewError(f"{e['type']} has no field {field!r}")
    decisions = state["decisions"].setdefault(exhibit_id, {})
    if action == "reset":
        decisions.pop(field, None)
        return
    if action not in FIELD_ACTIONS:
        raise ReviewError(f"action must be one of {FIELD_ACTIONS} or reset")
    if action != "edit":
        decisions[field] = {"action": action}
        return
    pages = set(range(e["start"], e["end"] + 1))
    if spec.kind == "list":
        out = []
        for it in items or []:
            if it.get("page_seq") in (None, ""):
                raise ReviewError(f"{field}: every item needs a page reference")
            p = _page(ctx, it["page_seq"])
            if p.seq not in pages:
                raise ReviewError(f"{field}: page {p.seq} is outside this exhibit (pages {e['start']}-{e['end']})")
            text = re.sub(r"\s+", " ", str(it.get("raw") or it.get("value") or "")).strip()
            if not text:
                raise ReviewError(f"{field}: empty item")
            out.append({"raw": text, "page_seq": p.seq})
        if not out:
            raise ReviewError(f"{field}: no items (use not_present)")
        decisions[field] = {"action": "edit", "items": out}
        return
    if page_seq in (None, ""):
        raise ReviewError(f"{field}: a page reference is required for an edit")
    p = _page(ctx, page_seq)
    if p.seq not in pages:
        raise ReviewError(f"{field}: page {p.seq} is outside this exhibit (pages {e['start']}-{e['end']})")
    decisions[field] = {"action": "edit", "value": parse_manual_value(spec, value), "page_seq": p.seq, "raw": raw}


def op_accept_all(state, ctx, exhibit_id: str | None = None, min_confidence: float = 0.0, include_empty: bool = True, **_):
    """Accept every undecided field (of one exhibit, or all) whose draft confidence is at least `min_confidence`.
    Empty draft fields are accepted as 'not present' when include_empty."""
    for e in state["exhibits"]:
        if exhibit_id and e["exhibit_id"] != exhibit_id:
            continue
        decisions = state["decisions"].setdefault(e["exhibit_id"], {})
        for spec in FIELDS.get(e["type"], []):
            if spec.name in decisions:
                continue
            draft = e["extraction"]["fields"].get(spec.name)
            if not draft:
                if include_empty:
                    decisions[spec.name] = {"action": "not_present"}
            elif spec.kind == "list" or float(draft.get("confidence", 0)) >= float(min_confidence):
                decisions[spec.name] = {"action": "accept"}


def op_query_map(state, ctx, exhibit_id: str, index: int, codes: list[str] | None = None, not_requirement: bool = False, **_):
    known = {r["code"] for r in ctx.requirement_catalog}
    codes = [c for c in dict.fromkeys(codes or [])]
    unknown = [c for c in codes if c not in known]
    if unknown:
        raise ReviewError(f"unknown requirement code(s) {unknown} — create them first")
    if not codes and not not_requirement:
        state["query_map"].pop(qkey(exhibit_id, int(index)), None)
        return
    if codes and not_requirement:
        raise ReviewError("an item is either mapped to codes or 'not a requirement', not both")
    state["query_map"][qkey(exhibit_id, int(index))] = {"codes": codes, "not_requirement": bool(not_requirement)}


def op_requirement(state, ctx, code: str, status: str | None = None, satisfied_by_exhibit_id: str | None = None,
                   notes: str | None = None, reset: bool = False, **_):
    if code not in {r["code"] for r in ctx.requirement_catalog}:
        raise ReviewError(f"unknown requirement code {code!r}")
    if reset:
        state["requirements"].pop(code, None)
        return
    if status is not None and status not in REQ_STATUSES:
        raise ReviewError(f"status must be one of {REQ_STATUSES}")
    if satisfied_by_exhibit_id:
        _find(state, satisfied_by_exhibit_id)
    state["requirements"][code] = {"status": status, "satisfied_by_exhibit_id": satisfied_by_exhibit_id or None, "notes": notes or None}


def op_events(state, ctx, events: list[dict] | None = None, reset: bool = False, **_):
    if reset:
        state["events"] = None
        return
    codes = {e["code"] for e in ctx.event_catalog}
    out = []
    for i, ev in enumerate(events or [], start=1):
        if ev.get("code") not in codes:
            raise ReviewError(f"event {i}: unknown event code {ev.get('code')!r}")
        if not ev.get("exhibit_id"):
            raise ReviewError(f"event {i}: choose the exhibit that records it")
        _, ex = _find(state, ev["exhibit_id"])
        date = None
        if ev.get("date"):
            try:
                date = dt.date.fromisoformat(str(ev["date"])).isoformat()
            except ValueError:
                raise ReviewError(f"event {i}: date must be YYYY-MM-DD")
        page_seq = ev.get("page_seq")
        if page_seq in (None, ""):
            page_seq = ex["start"]
        p = _page(ctx, page_seq)
        if not ex["start"] <= p.seq <= ex["end"]:
            raise ReviewError(f"event {i}: page {p.seq} is outside exhibit pages {ex['start']}-{ex['end']}")
        out.append({"code": ev["code"], "date": date, "exhibit_id": ex["exhibit_id"], "page_seq": p.seq})
    state["events"] = out


OPS: dict[str, Callable] = {
    "exhibit.split": op_split, "exhibit.merge": op_merge, "exhibit.retype": op_retype, "exhibit.stage": op_stage,
    "field.decide": op_field, "field.accept_all": op_accept_all, "query.map": op_query_map,
    "requirement.set": op_requirement, "events.set": op_events,
}


def apply_ops(state: dict, ctx: ReviewContext, ops: list[dict]) -> dict:
    """Apply ops to a copy of the state; all-or-nothing."""
    new = copy.deepcopy(state)
    for n, op in enumerate(ops, start=1):
        name = op.get("op")
        if name not in OPS:
            raise ReviewError(f"op {n}: unknown op {name!r}")
        try:
            OPS[name](new, ctx, **{k: v for k, v in op.items() if k != "op"})
        except TypeError as e:
            raise ReviewError(f"op {n} ({name}): bad arguments: {e}")
    return new


# ------------------------------------------------------------------------------------------------
# Derivation
# ------------------------------------------------------------------------------------------------
@dataclass
class Derived:
    exhibits: list[CardExhibit]
    fields: dict[str, dict[str, Any]]  # exhibit_id -> reviewed fields (evidence json | list | None)
    card: dict
    requirements: list[dict]
    derived_requirements: list[dict]  # before reviewer overrides
    events: list[dict]
    validations: list[dict]
    undecided: list[tuple[str, str]] = field(default_factory=list)  # (exhibit_id, field)
    unmapped: list[tuple[str, int]] = field(default_factory=list)  # (exhibit_id, index)


def reviewed_fields(e: dict, decisions: dict, ctx: ReviewContext) -> tuple[dict, list[str]]:
    out, undecided = {}, []
    for spec in FIELDS.get(e["type"], []):
        draft = e["extraction"]["fields"].get(spec.name)
        d = decisions.get(spec.name)
        if d is None:
            undecided.append(spec.name)
            out[spec.name] = draft
        elif d["action"] == "accept":
            out[spec.name] = draft
        elif d["action"] == "not_present":
            out[spec.name] = [] if spec.kind == "list" else None
        elif spec.kind == "list":
            out[spec.name] = [manual_evidence(ctx.pages[it["page_seq"]], it["raw"], it["raw"]) for it in d["items"]]
        else:
            out[spec.name] = manual_evidence(ctx.pages[d["page_seq"]], d["value"], d.get("raw"))
    return out, undecided


def _event_evidence(ctx: ReviewContext, ev: dict, ex: dict) -> dict | None:
    p = ctx.pages.get(ev.get("page_seq") or ex["start"])
    if p is None:
        return None
    raw = ev["date"] or ""
    evidence = manual_evidence(p, ev["date"], raw) if raw else {
        "value": None, "raw": "", "page_seq": p.seq, "doc_page_no": p.page_no, "document": p.document, "snippet": "",
        "method": "manual", "confidence": 1.0, "page_id": str(p.id), "alternatives": []}
    return {**evidence, "exhibit_id": ex["exhibit_id"], "exhibit_type": ex["type"], "field": "event_date"}


def derive(state: dict, ctx: ReviewContext, *, validate: bool = False) -> Derived:
    card_exhibits: list[CardExhibit] = []
    fields_by_ex: dict[str, dict] = {}
    undecided: list[tuple[str, str]] = []
    for n, e in enumerate(state["exhibits"], start=1):
        fields, und = reviewed_fields(e, state["decisions"].get(e["exhibit_id"], {}), ctx)
        undecided += [(e["exhibit_id"], f) for f in und]
        fields_by_ex[e["exhibit_id"]] = fields
        pages = list(range(e["start"], e["end"] + 1))
        card_exhibits.append(CardExhibit(ExhibitFacts(e["exhibit_id"], n, e["type"], e["stage"], fields), pages,
                                         float(e["confidence"]), e["method"], doc_date_of(e["type"], fields)))
    validations = run_validators([ce.facts for ce in card_exhibits], ctx.params)

    unmapped: list[tuple[str, int]] = []

    def item_codes(qid: str, idx: int, item: dict) -> list[str]:
        m = state["query_map"].get(qkey(qid, idx))
        if m is None:
            unmapped.append((qid, idx))
            return suggest_codes(item.get("value"), ctx.requirement_catalog)
        return [] if m["not_requirement"] else m["codes"]

    card, requirements, events = assemble_card(
        case=ctx.case, exhibits=card_exhibits, validations=validations, requirement_catalog=ctx.requirement_catalog,
        event_catalog=ctx.event_catalog, pipeline_version=ctx.pipeline_version, item_codes=item_codes, validate=False,
    )
    # query items carry their mapping on reviewed cards
    for q in card["facts"]["queries"]:
        for i, it in enumerate(q["items"]):
            m = state["query_map"].get(qkey(q["exhibit_id"], i))
            if m is not None:
                it["requirement_codes"] = m["codes"]
                it["mapping"] = "not_requirement" if m["not_requirement"] else "requirement"
    derived_requirements = copy.deepcopy(requirements)
    for r in requirements:
        o = state["requirements"].get(r["code"])
        r["origin"] = "auto"
        if o:
            if o.get("status"):
                r["status"] = o["status"]
            if o.get("satisfied_by_exhibit_id"):
                r["satisfied_by_exhibit_id"] = o["satisfied_by_exhibit_id"]
            r["notes"] = o.get("notes")
            r["origin"] = "review"
    if state["events"] is not None:
        ex_by_id = {e["exhibit_id"]: e for e in state["exhibits"]}
        typical = {e["code"]: e["typical_order"] for e in ctx.event_catalog}
        rows = [ev for ev in state["events"] if ev.get("exhibit_id") in ex_by_id]
        rows.sort(key=lambda ev: (ev["date"] or "9999-12-31", typical.get(ev["code"], 999)))
        events = [{"code": ev["code"], "date": ev["date"], "exhibit_id": ev["exhibit_id"], "seq": i,
                   "evidence": _event_evidence(ctx, ev, ex_by_id[ev["exhibit_id"]]), "origin": "review"}
                  for i, ev in enumerate(rows, start=1)]
    card["requirements"], card["events"] = requirements, events
    if validate:
        import jsonschema

        from rci.pipeline.card import card_schema

        jsonschema.validate(card, card_schema())
    return Derived(card_exhibits, fields_by_ex, card, requirements, derived_requirements, events,
                   [v.to_json() for v in validations], undecided, unmapped)


def blockers(state: dict, d: Derived) -> list[str]:
    out = []
    if d.undecided:
        out.append(f"{len(d.undecided)} field(s) not yet accepted / edited / marked not present")
    if d.unmapped:
        out.append(f"{len(d.unmapped)} query item(s) not mapped to a requirement code or 'not a requirement'")
    return out
