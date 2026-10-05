"""CaseProfile: the structured view of a case card that matching, gap analysis and next steps work on.

Built from the card alone (approved precedent or draft incoming), so the engine needs no page text. Every
fact keeps a citation (case + exhibit + page + snippet) for the officer screen.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from rci.pipeline.card import VALIDATOR_REQUIREMENT
from rci.pipeline.extract.common import society_key

OPEN = ("missing", "invalid", "queried")
NEEDED = ("present", "invalid", "queried")


def _v(card: dict, path: str):
    group, _, name = path.partition(".")
    f = (card["facts"].get(group) or {}).get(name)
    return f.get("value") if isinstance(f, dict) else None


def _f(card: dict, path: str) -> dict | None:
    group, _, name = path.partition(".")
    f = (card["facts"].get(group) or {}).get(name)
    return f if isinstance(f, dict) else None


def _date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def _decimal(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except InvalidOperation:
        return None


def cite(card: dict, *, exhibit_id: str | None, page_seq: int | None = None, snippet: str | None = None,
         exhibit_type: str | None = None, **extra) -> dict:
    """A link target for the officer screen: case + exhibit + page (+ the snippet to highlight)."""
    ex = next((e for e in card["exhibits"] if e["exhibit_id"] == exhibit_id), None) if exhibit_id else None
    if page_seq is None and ex:
        page_seq = ex["pages"][0]
    return {"case_id": card["case"]["case_id"], "case_ref": card["case"]["case_ref"], "exhibit_id": exhibit_id,
            "exhibit_type": exhibit_type or (ex["type"] if ex else None), "page_seq": page_seq, "snippet": snippet, **extra}


def cite_fact(card: dict, f: dict | None, **extra) -> dict | None:
    if not f:
        return None
    return cite(card, exhibit_id=f.get("exhibit_id"), page_seq=f.get("page_seq"), snippet=f.get("snippet"),
                exhibit_type=f.get("exhibit_type"), field=f.get("field"), value=f.get("value"), **extra)


@dataclass
class CaseProfile:
    case_id: str
    case_ref: str
    data_class: str
    case_type: str
    card_status: str
    society_name: str | None
    society_key: str
    registration_no: str | None
    tenure: str | None
    village: str | None
    taluka: str | None
    district: str | None
    area_sqm: Decimal | None
    application_date: dt.date | None
    order_date: dt.date | None
    decision: str | None
    as_of: dt.date | None  # order date, else last event date, else application date (= case_embeddings.as_of)
    encroachment: bool | None
    failed_validations: dict[str, dict] = field(default_factory=dict)  # code -> validation json
    req_status: dict[str, str] = field(default_factory=dict)
    req_queried: dict[str, dict] = field(default_factory=dict)  # code -> citation of the query item
    req_satisfied: dict[str, dict] = field(default_factory=dict)  # code -> citation of the satisfying exhibit
    events: list[dict] = field(default_factory=list)  # [{code, date, seq, cite}]
    facts: dict = field(default_factory=dict)  # raw card facts (for evidence lookups)
    card: dict = field(default_factory=dict)

    @property
    def open_codes(self) -> set[str]:
        """Requirement codes that were a problem: open now (query) / queried or left open (precedent)."""
        return {c for c, s in self.req_status.items() if s in OPEN} | set(self.req_queried)

    def rule_date(self, basis: str = "application_date") -> dt.date | None:
        order = {"application_date": [self.application_date, self.order_date, self.as_of],
                 "order_date": [self.order_date, self.as_of, self.application_date]}.get(basis, [self.application_date, self.as_of])
        return next((d for d in order if d), None)

    def validator_evidence(self, code: str) -> list[dict]:
        v = self.failed_validations.get(code)
        if not v:
            return []
        return [c for c in (cite_fact(self.card, e) for e in v.get("evidence", []) if e.get("exhibit_id")) if c]


def build_profile(card: dict) -> CaseProfile:
    facts = card["facts"]
    events = card.get("events") or []
    dates = [e["date"] for e in events if e.get("date")]
    order_date = _date(_v(card, "order.order_date"))
    app_date = _date(_v(card, "application.date"))
    as_of = order_date or (_date(max(dates)) if dates else None) or app_date
    soc = _v(card, "society.name")
    enc = [i.get("encroachment") for i in facts.get("inspections") or [] if isinstance(i.get("encroachment"), dict)]
    p = CaseProfile(
        case_id=card["case"]["case_id"], case_ref=card["case"]["case_ref"], data_class=card["case"]["data_class"],
        case_type=card["case"]["case_type"], card_status=card.get("status", "draft"),
        society_name=soc, society_key=society_key(soc), registration_no=_v(card, "society.registration_no"),
        tenure=_v(card, "land.tenure"), village=_v(card, "land.village"), taluka=_v(card, "land.taluka"),
        district=_v(card, "land.district"), area_sqm=_decimal(_v(card, "land.area_sqm")),
        application_date=app_date, order_date=order_date, decision=_v(card, "order.decision"), as_of=as_of,
        encroachment=any(e.get("value") is True for e in enc) if enc else None,
        failed_validations={v["code"]: v for v in card.get("validations", []) if v["status"] == "fail"},
        facts=facts, card=card,
    )
    for r in card.get("requirements", []):
        p.req_status[r["code"]] = r["status"]
        q_ev = next((e for e in r.get("evidence", []) if e.get("field") == "query_items"), None)
        if r.get("was_queried_in"):
            p.req_queried[r["code"]] = cite(card, exhibit_id=r["was_queried_in"], page_seq=(q_ev or {}).get("page_seq"),
                                            snippet=(q_ev or {}).get("snippet") or (q_ev or {}).get("raw"))
        if r.get("satisfied_by_exhibit_id"):
            p.req_satisfied[r["code"]] = cite(card, exhibit_id=r["satisfied_by_exhibit_id"])
    for e in sorted(events, key=lambda e: e["seq"]):
        ev = e.get("evidence") if isinstance(e.get("evidence"), dict) else None
        p.events.append({"code": e["code"], "date": _date(e.get("date")), "seq": e["seq"],
                         "cite": cite(card, exhibit_id=e.get("exhibit_id"), page_seq=(ev or {}).get("page_seq"),
                                      snippet=(ev or {}).get("snippet"))})
    return p


def requirement_validators(code: str, catalog_row: dict | None) -> list[str]:
    """Validator codes whose failure makes `code` invalid or needed."""
    out = [v for v, r in VALIDATOR_REQUIREMENT.items() if r == code]
    when = (catalog_row or {}).get("applies_when") or {}
    if when.get("validator_failed"):
        out.append(when["validator_failed"])
    return out
