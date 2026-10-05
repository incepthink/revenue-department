"""Cross-document validators over extracted exhibit fields. Every result names the evidence it used and
the parameters (with verification status) it depended on."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from rci.config import Parameters
from rci.pipeline.extract.common import society_key

CTS_SOURCES = ("APPLICATION", "PROPERTY_CARD", "LEASE_SANAD", "FIELD_REPORT", "CTSO_REPORT", "COLLECTOR_ORDER")


@dataclass
class ExhibitFacts:
    exhibit_id: str
    seq: int
    exhibit_type: str
    stage: str
    fields: dict[str, Any]  # name -> evidence json | list | None

    def get(self, name: str) -> dict | None:
        v = self.fields.get(name)
        return v if isinstance(v, dict) else None


@dataclass
class ValidationResult:
    code: str
    status: str  # pass | fail | not_evaluable
    message: str
    evidence: list[dict] = field(default_factory=list)
    parameters: list[dict] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    @property
    def depends_on_unverified(self) -> bool:
        return any(p["status"] != "verified" for p in self.parameters)

    def to_json(self) -> dict:
        d = asdict(self)
        d["depends_on_unverified"] = self.depends_on_unverified
        return d


def _ref(ex: ExhibitFacts, name: str) -> dict:
    ev = ex.get(name) or {}
    return {"exhibit_id": ex.exhibit_id, "exhibit_type": ex.exhibit_type, "field": name, "value": ev.get("value"),
            "raw": ev.get("raw"), "page_seq": ev.get("page_seq"), "snippet": ev.get("snippet")}


def _param(params: Parameters, key: str) -> dict:
    p = params.get(key)
    return {"key": key, "value": p.value, "status": p.status}


def _date(ev: dict | None) -> dt.date | None:
    return dt.date.fromisoformat(ev["value"]) if ev and ev.get("value") else None


def _first(exs: list[ExhibitFacts], t: str, latest: bool = False) -> ExhibitFacts | None:
    c = [e for e in exs if e.exhibit_type == t]
    return (c[-1] if latest else c[0]) if c else None


def _base_cts(v: str) -> str:
    return v.removesuffix("PT")


def cts_match(exs: list[ExhibitFacts], params: Parameters) -> ValidationResult:
    obs = [e for e in exs if e.exhibit_type in CTS_SOURCES and e.get("cts_no")]
    refs = [_ref(e, "cts_no") for e in obs]
    if len(obs) < 2:
        return ValidationResult("CTS_MATCH", "not_evaluable", "CTS number found on fewer than two documents", refs)
    values = {_base_cts(str(r["value"])) for r in refs}
    if len(values) == 1:
        return ValidationResult("CTS_MATCH", "pass", f"CTS {values.pop()} consistent across {len(obs)} documents", refs)
    return ValidationResult("CTS_MATCH", "fail", f"CTS numbers differ across documents: {sorted(values)}", refs)


def area_tolerance(exs: list[ExhibitFacts], params: Parameters) -> ValidationResult:
    ps = [_param(params, "area_tolerance_pct"), _param(params, "area_tolerance_abs_sqm")]
    obs = [e for e in exs if e.exhibit_type in CTS_SOURCES and e.get("area_sqm")]
    refs = [_ref(e, "area_sqm") for e in obs]
    ref_ex = _first(obs, "PROPERTY_CARD", latest=True) or _first(obs, "APPLICATION")
    if ref_ex is None or len(obs) < 2:
        return ValidationResult("AREA_TOLERANCE", "not_evaluable", "area found on fewer than two documents", refs, ps)
    ref = Decimal(str(ref_ex.get("area_sqm")["value"]))
    tol = max(ref * Decimal(str(params.value("area_tolerance_pct"))) / 100, Decimal(str(params.value("area_tolerance_abs_sqm"))))
    diffs = []
    for e in obs:
        a = Decimal(str(e.get("area_sqm")["value"]))
        if abs(a - ref) > tol:
            diffs.append({"exhibit_id": e.exhibit_id, "exhibit_type": e.exhibit_type, "area_sqm": str(a), "diff_sqm": str(a - ref)})
    details = {"reference_exhibit_id": ref_ex.exhibit_id, "reference_area_sqm": str(ref), "tolerance_sqm": str(tol.quantize(Decimal('0.01'))), "outside_tolerance": diffs}
    if diffs:
        return ValidationResult("AREA_TOLERANCE", "fail",
                                f"{len(diffs)} document(s) differ from the property-card area {ref} sq m by more than {tol:.2f} sq m",
                                refs, ps, details)
    return ValidationResult("AREA_TOLERANCE", "pass", f"areas within {tol:.2f} sq m of {ref} sq m", refs, ps, details)


def holder_is_society(exs: list[ExhibitFacts], params: Parameters) -> ValidationResult:
    card = _first(exs, "PROPERTY_CARD", latest=True)
    soc_src = next((e for e in exs if e.exhibit_type in ("APPLICATION", "SGM_RESOLUTION", "LEASE_SANAD")
                    and (e.get("society_name") or e.get("lessee"))), None)
    if card is None or not card.get("holder_name"):
        return ValidationResult("HOLDER_IS_SOCIETY", "not_evaluable", "no holder name on a property card")
    holder = str(card.get("holder_name")["value"])
    refs = [_ref(card, "holder_name")]
    if soc_src is None:
        ok = "सहकारी गृहनिर्माण संस्था" in holder or "housing society" in holder.lower()
        return ValidationResult("HOLDER_IS_SOCIETY", "pass" if ok else "fail",
                                "holder is a housing society" if ok else "holder does not look like a housing society", refs)
    fname = "society_name" if soc_src.get("society_name") else "lessee"
    society = str(soc_src.get(fname)["value"])
    refs.append(_ref(soc_src, fname))
    hk, sk = society_key(holder), society_key(society)
    if sk and (sk in hk or hk in sk):
        return ValidationResult("HOLDER_IS_SOCIETY", "pass", "property-card holder is the applicant society", refs)
    return ValidationResult("HOLDER_IS_SOCIETY", "fail", "property-card holder is not the applicant society", refs,
                            details={"holder": holder, "society": society})


def sgm_date_order(exs: list[ExhibitFacts], params: Parameters) -> ValidationResult:
    ps = [_param(params, "sgm_must_precede_application"), _param(params, "sgm_resolution_max_age_days")]
    sgm, app = _first(exs, "SGM_RESOLUTION", latest=True), _first(exs, "APPLICATION")
    sd = _date(sgm.get("meeting_date")) if sgm else None
    ad = _date(app.get("application_date")) if app else None
    if not (sd and ad):
        return ValidationResult("SGM_DATE_ORDER", "not_evaluable", "SGM date or application date not found", [], ps)
    refs = [_ref(sgm, "meeting_date"), _ref(app, "application_date")]
    gap = (ad - sd).days
    details = {"sgm_date": sd.isoformat(), "application_date": ad.isoformat(), "gap_days": gap}
    if params.value("sgm_must_precede_application") and gap < 0:
        return ValidationResult("SGM_DATE_ORDER", "fail", f"SGM held {-gap} day(s) after the application", refs, ps, details)
    if gap > int(params.value("sgm_resolution_max_age_days")):
        return ValidationResult("SGM_DATE_ORDER", "fail", f"SGM resolution is {gap} days older than the application", refs, ps, details)
    return ValidationResult("SGM_DATE_ORDER", "pass", f"SGM held {gap} day(s) before the application", refs, ps, details)


def property_card_recency(exs: list[ExhibitFacts], params: Parameters) -> ValidationResult:
    ps = [_param(params, "property_card_max_age_days")]
    card = _first(exs, "PROPERTY_CARD", latest=True)
    if card is None or not card.get("issue_date"):
        return ValidationResult("PROPERTY_CARD_RECENCY", "not_evaluable", "no dated property card", [], ps)
    issued = _date(card.get("issue_date"))
    refs = [_ref(card, "issue_date")]
    if card.stage == "query_response":
        ref_ex = _first(exs, "QUERY_REPLY", latest=True) or _first(exs, "QUERY_LETTER", latest=True)
        ref_field = "letter_date"
    else:
        ref_ex, ref_field = _first(exs, "APPLICATION"), "application_date"
    ref_date = _date(ref_ex.get(ref_field)) if ref_ex else None
    if ref_date is None:
        return ValidationResult("PROPERTY_CARD_RECENCY", "not_evaluable", "no reference date for the property card", refs, ps)
    refs.append(_ref(ref_ex, ref_field))
    age = (ref_date - issued).days
    limit = int(params.value("property_card_max_age_days"))
    details = {"issue_date": issued.isoformat(), "reference_date": ref_date.isoformat(), "age_days": age, "max_age_days": limit}
    if age < 0:
        return ValidationResult("PROPERTY_CARD_RECENCY", "pass", "property card issued after the reference date", refs, ps, details)
    if age > limit:
        return ValidationResult("PROPERTY_CARD_RECENCY", "fail", f"property card is {age} days old (limit {limit})", refs, ps, details)
    return ValidationResult("PROPERTY_CARD_RECENCY", "pass", f"property card is {age} days old (limit {limit})", refs, ps, details)


VALIDATORS = [cts_match, area_tolerance, holder_is_society, sgm_date_order, property_card_recency]


def run_validators(exs: list[ExhibitFacts], params: Parameters) -> list[ValidationResult]:
    return [v(exs, params) for v in VALIDATORS]
