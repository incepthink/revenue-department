"""Case card assembly (card.v1) + draft case_requirements and case_events.

The card is deterministic for a given set of inputs (no timestamps, stable exhibit ids), so its
sha256 decides whether a re-run creates a new card version.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

import jsonschema

from rci.config import get_settings
from rci.pipeline.validators import ExhibitFacts, ValidationResult

CARD_SCHEMA_VERSION = "card.v1"

DOC_DATE_FIELD = {
    "APPLICATION": "application_date", "PROPERTY_CARD": "issue_date", "LEASE_SANAD": "lease_date",
    "SGM_RESOLUTION": "meeting_date", "QUERY_LETTER": "letter_date", "QUERY_REPLY": "letter_date",
    "FIELD_REPORT": "inspection_date", "CTSO_REPORT": "inspection_date", "CHALLAN": "payment_date",
    "DEMAND_LETTER": "letter_date", "COLLECTOR_ORDER": "order_date",
}

# (fact path, [(exhibit_type, field, pick)]) — first match wins; pick = first | latest
FACT_SOURCES: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("society.name", [("APPLICATION", "society_name", "first"), ("SGM_RESOLUTION", "society_name", "first"), ("LEASE_SANAD", "lessee", "first")]),
    ("society.registration_no", [("APPLICATION", "registration_no", "first")]),
    ("land.cts_no", [("PROPERTY_CARD", "cts_no", "latest"), ("APPLICATION", "cts_no", "first"), ("LEASE_SANAD", "cts_no", "first")]),
    ("land.village", [("PROPERTY_CARD", "village", "latest"), ("APPLICATION", "village", "first")]),
    ("land.taluka", [("APPLICATION", "taluka", "first")]),
    ("land.district", [("APPLICATION", "district", "first")]),
    ("land.area_sqm", [("PROPERTY_CARD", "area_sqm", "latest"), ("APPLICATION", "area_sqm", "first"), ("LEASE_SANAD", "area_sqm", "first")]),
    ("land.tenure", [("PROPERTY_CARD", "tenure", "latest"), ("APPLICATION", "tenure", "first")]),
    ("application.date", [("APPLICATION", "application_date", "first")]),
    ("property_card.issue_date", [("PROPERTY_CARD", "issue_date", "latest")]),
    ("property_card.holder_name", [("PROPERTY_CARD", "holder_name", "latest")]),
    ("lease.lease_date", [("LEASE_SANAD", "lease_date", "first")]),
    ("lease.lease_period_years", [("LEASE_SANAD", "lease_period_years", "first")]),
    ("lease.annual_rent", [("LEASE_SANAD", "annual_rent", "first")]),
    ("sgm.meeting_date", [("SGM_RESOLUTION", "meeting_date", "latest")]),
    ("sgm.resolution_no", [("SGM_RESOLUTION", "resolution_no", "latest")]),
    ("sgm.members_present", [("SGM_RESOLUTION", "members_present", "latest")]),
    ("sgm.members_total", [("SGM_RESOLUTION", "members_total", "latest")]),
    ("demand.letter_date", [("DEMAND_LETTER", "letter_date", "latest")]),
    ("demand.amount", [("DEMAND_LETTER", "amount", "latest")]),
    ("order.order_no", [("COLLECTOR_ORDER", "order_no", "latest")]),
    ("order.order_date", [("COLLECTOR_ORDER", "order_date", "latest")]),
    ("order.decision", [("COLLECTOR_ORDER", "decision", "latest")]),
    ("order.premium_amount", [("COLLECTOR_ORDER", "premium_amount", "latest")]),
]

VALIDATOR_REQUIREMENT = {
    "PROPERTY_CARD_RECENCY": "PROPERTY_CARD_RECENT",
    "HOLDER_IS_SOCIETY": "PROPERTY_CARD_RECENT",
    "SGM_DATE_ORDER": "SGM_RESOLUTION",
}
LOW_CONFIDENCE = 0.7


@dataclass
class CardExhibit:
    facts: ExhibitFacts
    page_seqs: list[int]
    confidence: float
    classification_method: str
    doc_date: dt.date | None


@lru_cache
def card_schema() -> dict:
    return json.loads((get_settings().schemas_dir / "card.v1.json").read_text(encoding="utf-8"))


def doc_date_of(exhibit_type: str, fields: dict) -> dt.date | None:
    ev = fields.get(DOC_DATE_FIELD.get(exhibit_type, ""))
    if isinstance(ev, dict) and ev.get("value"):
        return dt.date.fromisoformat(ev["value"])
    return None


def _fact(ex: ExhibitFacts, name: str) -> dict | None:
    ev = ex.get(name)
    if not ev:
        return None
    return {**ev, "exhibit_id": ex.exhibit_id, "exhibit_type": ex.exhibit_type, "field": name}


def _page_range(seqs: list[int]) -> str:
    return f"{seqs[0]}" if len(seqs) == 1 else f"{seqs[0]}-{seqs[-1]}"


def _set(d: dict, path: str, value: Any) -> None:
    head, _, tail = path.partition(".")
    d.setdefault(head, {})[tail] = value


def build_facts(exs: list[ExhibitFacts]) -> dict:
    facts: dict = {}
    for path, sources in FACT_SOURCES:
        chosen = None
        for t, field, pick in sources:
            cands = [e for e in exs if e.exhibit_type == t and e.get(field)]
            if cands:
                chosen = _fact(cands[-1] if pick == "latest" else cands[0], field)
                break
        _set(facts, path, chosen)
    facts["queries"] = [
        {"exhibit_id": e.exhibit_id, "letter_date": _fact(e, "letter_date"), "ref_no": _fact(e, "ref_no"),
         "items": [{**it, "exhibit_id": e.exhibit_id, "exhibit_type": e.exhibit_type, "field": "query_items"}
                   for it in (e.fields.get("query_items") or [])]}
        for e in exs if e.exhibit_type == "QUERY_LETTER"
    ]
    facts["inspections"] = [
        {"exhibit_id": e.exhibit_id, "kind": e.exhibit_type, "inspection_date": _fact(e, "inspection_date"),
         "area_sqm": _fact(e, "area_sqm"), "cts_no": _fact(e, "cts_no"), "encroachment": _fact(e, "encroachment")}
        for e in exs if e.exhibit_type in ("FIELD_REPORT", "CTSO_REPORT")
    ]
    facts["payments"] = [
        {"exhibit_id": e.exhibit_id, "grn": _fact(e, "grn"), "payment_date": _fact(e, "payment_date"),
         "amount": _fact(e, "amount"), "head_of_account": _fact(e, "head_of_account")}
        for e in exs if e.exhibit_type == "CHALLAN"
    ]
    return facts


# (query exhibit id, item index, item fact) -> requirement codes the item asks for
ItemCodes = Callable[[str, int, dict], list[str]]


def suggest_codes(item_text: str | None, catalog: list[dict]) -> list[str]:
    """Requirement codes whose query keywords occur in a query-letter item (the pre-review heuristic)."""
    t = (item_text or "").casefold()
    return [r["code"] for r in catalog if any(k.casefold() in t for k in r.get("query_keywords", []))]


def draft_requirements(exs: list[ExhibitFacts], facts: dict, validations: list[ValidationResult], catalog: list[dict],
                       item_codes: ItemCodes | None = None) -> list[dict]:
    """`item_codes` maps each query item to requirement codes (reviewed mapping); default = keyword heuristic."""
    failed = {v.code for v in validations if v.status == "fail"}
    tenure = (facts["land"].get("tenure") or {}).get("value")
    codes_for = item_codes or (lambda qid, idx, it: suggest_codes(it.get("value"), catalog))
    query_items = [(q["exhibit_id"], it, set(codes_for(q["exhibit_id"], i, it)))
                   for q in facts["queries"] for i, it in enumerate(q["items"])]
    out = []
    for req in catalog:
        code = req["code"]
        cond = req.get("applies_when") or {}
        present = [e for e in exs if e.exhibit_type in req.get("satisfied_by", [])]
        queried = next(((qid, it) for qid, it, codes in query_items if code in codes), None)
        evidence = [{"exhibit_id": e.exhibit_id, "exhibit_type": e.exhibit_type, "stage": e.stage} for e in present]
        if queried:
            evidence.append({"exhibit_id": queried[0], "field": "query_items", "raw": queried[1].get("raw"),
                             "page_seq": queried[1].get("page_seq"), "snippet": queried[1].get("snippet")})
        if "tenure" in cond and tenure and tenure != cond["tenure"] and not present:
            status = "not_applicable"
        elif "validator_failed" in cond and cond["validator_failed"] not in failed and not present:
            status = "not_applicable"
        elif present:
            status = "invalid" if any(VALIDATOR_REQUIREMENT.get(v) == code for v in failed) else "present"
        elif queried:
            status = "queried"
        else:
            status = "missing"
        out.append({
            "code": code, "status": status,
            "satisfied_by_exhibit_id": present[-1].exhibit_id if present else None,
            "was_queried_in": queried[0] if queried else None,
            "evidence": evidence,
            "catalog_status": req.get("status", "to_verify"),
        })
    return out


def draft_events(exs: list[ExhibitFacts], catalog: list[dict]) -> list[dict]:
    events = []
    for ev in catalog:
        for e in exs:
            if e.exhibit_type in ev.get("source_exhibit_types", []):
                f = _fact(e, ev["date_field"]) if ev.get("date_field") else None
                events.append({"code": ev["code"], "date": (f or {}).get("value"), "exhibit_id": e.exhibit_id,
                               "evidence": f, "typical_order": ev["typical_order"]})
    events.sort(key=lambda x: (x["date"] or "9999-12-31", x["typical_order"]))
    for i, e in enumerate(events, 1):
        e["seq"] = i
        del e["typical_order"]
    return events


def open_issues(exs: list[ExhibitFacts], card_exhibits: list[CardExhibit], validations: list[ValidationResult]) -> list[dict]:
    issues = []
    for ce in card_exhibits:
        if ce.confidence < LOW_CONFIDENCE:
            issues.append({"kind": "low_confidence_exhibit", "exhibit_id": ce.facts.exhibit_id, "confidence": ce.confidence})
    for e in exs:
        for name, ev in e.fields.items():
            if isinstance(ev, dict) and ev.get("confidence", 1) < LOW_CONFIDENCE:
                issues.append({"kind": "low_confidence_field", "exhibit_id": e.exhibit_id, "field": name})
    for v in validations:
        if v.status == "fail":
            issues.append({"kind": "validation_failed", "code": v.code})
        if v.depends_on_unverified:
            issues.append({"kind": "unverified_parameter", "code": v.code,
                           "parameters": [p["key"] for p in v.parameters if p["status"] != "verified"]})
    return issues


def assemble_card(
    *,
    case: dict,
    exhibits: list[CardExhibit],
    validations: list[ValidationResult],
    requirement_catalog: list[dict],
    event_catalog: list[dict],
    pipeline_version: str,
    item_codes: ItemCodes | None = None,
    validate: bool = True,
) -> tuple[dict, list[dict], list[dict]]:
    exs = [ce.facts for ce in exhibits]
    facts = build_facts(exs)
    requirements = draft_requirements(exs, facts, validations, requirement_catalog, item_codes)
    events = draft_events(exs, event_catalog)
    card = {
        "schema_version": CARD_SCHEMA_VERSION,
        "case": case,
        "pipeline_version": pipeline_version,
        "status": "draft",
        "facts": facts,
        "exhibits": [
            {
                "exhibit_id": ce.facts.exhibit_id, "seq": ce.facts.seq, "type": ce.facts.exhibit_type,
                "stage": ce.facts.stage, "pages": ce.page_seqs, "page_range": _page_range(ce.page_seqs),
                "confidence": ce.confidence, "classification_method": ce.classification_method,
                "doc_date": ce.doc_date.isoformat() if ce.doc_date else None,
            }
            for ce in exhibits
        ],
        "validations": [v.to_json() for v in validations],
        "requirements": requirements,
        "events": events,
        "review": {"open_issues": open_issues(exs, exhibits, validations)},
    }
    if validate:
        jsonschema.validate(card, card_schema())
    return card, requirements, events


def content_hash(card: dict) -> str:
    return hashlib.sha256(json.dumps(card, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
