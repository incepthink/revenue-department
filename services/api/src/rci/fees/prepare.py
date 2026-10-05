"""From a case card to a traced fee: pick the dates, rule slab, area and ASR rate, then call the engine.

Pure: the rule stack comes from a `stack_loader(date)` callable and the ASR rows are passed in, so tests can run
the whole calculation without a database. `service.py` supplies the DB-backed versions.

Nothing is guessed. A value the calculation needs but cannot find (no verified rate, no ASR row, no area) is a
*blocker*: the trace still shows every step it could do and the result has no total. A value that rests on an
unconfirmed rule, ASR row or parameter makes the result `unverified` ("would apply").
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from rci.config import Parameters
from rci.fees.engine import (
    FEE_ENGINE_VERSION,
    Adjustment,
    FeeInputError,
    FeeInputs,
    Rounding,
    TraceStep,
    compute,
    money,
    to_decimal,
)
from rci.rules.resolve import (
    AmbiguousRule,
    NoRuleFound,
    RuleStack,
    SlabResolution,
    UnverifiedRule,
    adjustments_for,
    resolve_slab,
)

APPLICANT_CATEGORY = "CHS"
HOLDING_BASIS = {"occupancy_class_2": "occupancy_class_2", "govt_lease": "govt_lease"}
AREA_SOURCES = {"PROPERTY_CARD": "property_card", "CTSO_REPORT": "ctso_measured", "FIELD_REPORT": "field_measured",
                "APPLICATION": "application", "LEASE_SANAD": "lease"}
PARAM_KEYS = ("rule_date_basis", "asr_year_basis", "asr_rate_column", "area_basis", "rounding", "default_land_use",
              "premium_heads")


@dataclass(frozen=True)
class FeeParams:
    rule_date_basis: str = "application_date"
    asr_year_basis: str = "application_date"
    asr_rate_column: dict = field(default_factory=lambda: {"residential": "land_rate_per_sqm", "commercial": "land_rate_per_sqm"})
    area_basis: tuple[str, ...] = ("property_card", "ctso_measured", "application", "lease")
    rounding: Rounding = Rounding()
    default_land_use: str = "residential"
    premium_heads: tuple[str, ...] = ("0029-00-800",)
    statuses: dict = field(default_factory=dict)  # key -> verified | to_verify

    @classmethod
    def from_parameters(cls, p: Parameters) -> "FeeParams":
        r = p.value("rounding")
        return cls(
            rule_date_basis=p.value("rule_date_basis"), asr_year_basis=p.value("asr_year_basis"),
            asr_rate_column=dict(p.value("asr_rate_column")), area_basis=tuple(p.value("area_basis")),
            rounding=Rounding(r.get("mode", "ROUND_HALF_UP"), Decimal(str(r.get("unit", "1")))),
            default_land_use=p.value("default_land_use"), premium_heads=tuple(p.value("premium_heads")),
            statuses={k: p.get(k).status for k in PARAM_KEYS},
        )

    def status(self, key: str) -> str:
        return self.statuses.get(key, "to_verify")

    def to_json(self) -> dict:
        return {"rule_date_basis": self.rule_date_basis, "asr_year_basis": self.asr_year_basis,
                "asr_rate_column": self.asr_rate_column, "area_basis": list(self.area_basis),
                "rounding": {"mode": self.rounding.mode, "unit": str(self.rounding.unit)},
                "default_land_use": self.default_land_use, "premium_heads": list(self.premium_heads),
                "statuses": dict(self.statuses)}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_json(), sort_keys=True).encode()).hexdigest()[:8]


@dataclass(frozen=True)
class AsrRow:
    year: int
    district: str
    village: str
    land_rate_per_sqm: Decimal
    residential_rate_per_sqm: Decimal | None = None
    commercial_rate_per_sqm: Decimal | None = None
    zone: str | None = None
    status: str = "to_verify"
    source_ref: str | None = None
    id: Any = None

    def rate(self, column: str) -> Decimal | None:
        return getattr(self, column)


@dataclass
class FeeResult:
    status: str  # computed | unverified | blocked
    total: str | None
    components: dict
    trace: list[TraceStep]
    blockers: list[str]
    unverified: list[str]
    inputs: dict
    params: dict
    engine_version: str
    rule_set_code: str | None = None

    @property
    def depends_on_unverified(self) -> bool:
        return bool(self.unverified)

    def to_json(self) -> dict:
        return {"status": self.status, "total": self.total, "components": self.components,
                "trace": [t.to_json() for t in self.trace], "blockers": self.blockers, "unverified": self.unverified,
                "depends_on_unverified": self.depends_on_unverified, "inputs": self.inputs, "params": self.params,
                "engine_version": self.engine_version, "rule_set_code": self.rule_set_code}


# ------------------------------------------------------------------------------------------------
# Card facts
# ------------------------------------------------------------------------------------------------
def _fact(card: dict, path: str) -> dict | None:
    group, _, name = path.partition(".")
    f = (card["facts"].get(group) or {}).get(name)
    return f if isinstance(f, dict) and f.get("value") not in (None, "") else None


def evidence_ref(card: dict, f: dict | None, **extra) -> dict:
    if not f:
        return {}
    return {"kind": "evidence", "case_id": card["case"]["case_id"], "case_ref": card["case"]["case_ref"],
            "exhibit_id": f.get("exhibit_id"), "exhibit_type": f.get("exhibit_type"), "field": f.get("field"),
            "page_seq": f.get("page_seq"), "snippet": f.get("snippet"), "value": f.get("value"), **extra}


def area_observations(card: dict) -> dict[str, dict]:
    """basis -> latest area evidence (from the facts, inspections and the area validator's evidence)."""
    obs: list[dict] = []
    land = _fact(card, "land.area_sqm")
    if land:
        obs.append(land)
    for v in card.get("validations", []):
        if v["code"] == "AREA_TOLERANCE":
            obs += [e for e in v.get("evidence", []) if e.get("value") not in (None, "")]
    for i in card["facts"].get("inspections") or []:
        a = i.get("area_sqm")
        if isinstance(a, dict) and a.get("value") not in (None, ""):
            obs.append({**a, "exhibit_type": a.get("exhibit_type") or i["kind"], "exhibit_id": a.get("exhibit_id") or i["exhibit_id"]})
    out: dict[str, dict] = {}
    for o in obs:
        basis = AREA_SOURCES.get(o.get("exhibit_type") or "")
        if basis:
            out[basis] = o  # evidence lists follow exhibit order, so the last one is the latest
    return out


def premium_payments(card: dict, heads: tuple[str, ...]) -> list[dict]:
    """Challans whose head of account starts with a premium head (or has no head recorded)."""
    out = []
    for p in card["facts"].get("payments") or []:
        amount = p.get("amount") or {}
        if amount.get("value") in (None, ""):
            continue
        head = str((p.get("head_of_account") or {}).get("value") or "")
        if not head or any(head.replace(" ", "").startswith(h.replace(" ", "")) for h in heads):
            out.append(p)
    return out


def case_dates(card: dict, heads: tuple[str, ...]) -> dict[str, dict | None]:
    pays = sorted((p["payment_date"] for p in premium_payments(card, heads) if (p.get("payment_date") or {}).get("value")),
                  key=lambda f: f["value"])
    return {"application_date": _fact(card, "application.date"), "order_date": _fact(card, "order.order_date"),
            "payment_date": pays[0] if pays else None, "demand_date": _fact(card, "demand.letter_date")}


def fiscal_year(d: dt.date) -> int:
    """ASR years start on 1 April: 15-Jan-2025 is in ASR year 2024 (2024-25)."""
    return d.year if d.month >= 4 else d.year - 1


# ------------------------------------------------------------------------------------------------
# The calculation
# ------------------------------------------------------------------------------------------------
def calculate(card: dict, params: FeeParams, stack_loader: Callable[[dt.date], RuleStack], asr_rows: list[AsrRow], *,
              today: dt.date, land_use: str | None = None, days_late: int | None = None) -> FeeResult:
    trace: list[TraceStep] = []
    blockers: list[str] = []
    unverified: list[str] = []
    inputs: dict[str, Any] = {}
    dates = case_dates(card, params.premium_heads)

    def param_status(key: str) -> str:
        if params.status(key) != "verified":
            unverified.append(f"parameter {key} is to_verify")
            return "unverified"
        return "ok"

    # 1. rule date
    f = dates.get(params.rule_date_basis)
    if f:
        rule_date, st, note = dt.date.fromisoformat(f["value"]), param_status("rule_date_basis"), None
    else:
        rule_date, st = today, "assumption"
        note = f"{params.rule_date_basis} not on the card yet; using the analysis date"
        param_status("rule_date_basis")
    inputs["rule_date"] = rule_date.isoformat()
    trace.append(TraceStep("inputs.rule_date", "Date that selects the rules", rule_date.isoformat(), f"basis = {params.rule_date_basis}",
                           {"basis": params.rule_date_basis}, [evidence_ref(card, f)] if f else [], st, note))

    # 2. holding basis and land use
    tenure_f = _fact(card, "land.tenure")
    tenure = tenure_f["value"] if tenure_f else None
    holding = HOLDING_BASIS.get(tenure or "")
    if tenure == "occupancy_class_1":
        blockers.append("land is already Occupancy Class-I; no conversion premium")
    elif holding is None:
        blockers.append("tenure (holding basis) not on the card")
    trace.append(TraceStep("inputs.holding_basis", "Holding basis", holding or tenure, None, {"tenure": tenure},
                           [evidence_ref(card, tenure_f)] if tenure_f else [], "ok" if holding else "blocked"))
    use = land_use or params.default_land_use
    trace.append(TraceStep("inputs.land_use", "Land use", use, None, {},
                           [], "ok" if land_use else "assumption",
                           None if land_use else "no land-use field on the card; parameter default_land_use"))
    if not land_use:
        param_status("default_land_use")
    inputs.update(holding_basis=holding, land_use=use, tenure=tenure)

    # 3. rule stack + slab
    stack = stack_loader(rule_date)
    trace.append(TraceStep("rules.stack", "Rule sets in force", " < ".join(stack.codes) or "(none)", None,
                           {"on": rule_date.isoformat()},
                           [{"kind": "rule_set", "rule_set_code": r.code, "status": r.status} for r in stack.rule_sets]))
    res: SlabResolution | None = None
    rate_pct = None
    facts = {"tenure": tenure, "land_use": use}
    if holding:
        try:
            res = resolve_slab(stack, applicant_category=APPLICANT_CATEGORY, holding_basis=holding, land_use=use, facts=facts)
            slab_status = "ok"
        except UnverifiedRule as e:
            res, slab_status = e.resolution, "unverified"
            unverified.append(f"rule: {e}")
        except AmbiguousRule as e:
            blockers.append(f"ambiguous rule: {e}")
            trace.append(TraceStep("rules.slab", "Premium slab", None, None, {}, [], "blocked", str(e)))
        except NoRuleFound as e:
            blockers.append(f"no rule: {e}")
            trace.append(TraceStep("rules.slab", "Premium slab", None, None, {}, [], "blocked", str(e)))
    if res is not None:
        s = res.slab
        rate_pct = s.rate_pct
        src = [{"kind": "slab", "rule_set_code": res.rule_set.code, "seed_key": s.seed_key, "slab_id": str(s.id) if s.id else None,
                "status": s.status, "rule_set_status": res.rule_set.status, "source_ref": s.source_ref}]
        if rate_pct is None:
            blockers.append(f"rate not filled in on slab {s.seed_key or s.id} ({res.rule_set.code})")
            slab_status = "blocked"
        trace.append(TraceStep("rules.slab", "Premium slab", f"{rate_pct}% of {s.rate_basis}" if rate_pct is not None else None,
                               None, {"window_from": s.window_from.isoformat() if s.window_from else None,
                                      "window_to": s.window_to.isoformat() if s.window_to else None},
                               src, slab_status, "; ".join(res.trace)))
        inputs.update(rule_set_code=res.rule_set.code, slab=s.to_json())

    # 4. area
    areas = area_observations(card)
    area_f = next((areas[b] for b in params.area_basis if b in areas), None)
    area_basis = next((b for b in params.area_basis if b in areas), None)
    area = to_decimal(area_f["value"]) if area_f else None
    if area is None:
        blockers.append("no area on the card for any configured area basis")
    else:
        param_status("area_basis")
    trace.append(TraceStep("inputs.area", "Area charged", f"{area} sq m" if area is not None else None, f"basis = {area_basis}",
                           {"basis_order": list(params.area_basis),
                            "observed": {b: str(o.get("value")) for b, o in areas.items()}},
                           [evidence_ref(card, area_f)] if area_f else [], "ok" if area is not None else "blocked"))
    inputs.update(area_sqm=str(area) if area is not None else None, area_basis=area_basis)

    # 5. ASR year and rate
    yf = dates.get(params.asr_year_basis)
    y_date = dt.date.fromisoformat(yf["value"]) if yf else rule_date
    year = fiscal_year(y_date)
    param_status("asr_year_basis")
    trace.append(TraceStep("inputs.asr_year", "ASR year", f"{year}-{str(year + 1)[-2:]}", f"basis = {params.asr_year_basis}",
                           {"date": y_date.isoformat()}, [evidence_ref(card, yf)] if yf else [],
                           "ok" if yf else "assumption", None if yf else f"{params.asr_year_basis} not on the card; using the rule date"))
    village_f, district_f = _fact(card, "land.village"), _fact(card, "land.district")
    column = params.asr_rate_column.get(use, "land_rate_per_sqm")
    param_status("asr_rate_column")
    asr_rate = None
    if not (village_f and district_f):
        blockers.append("village / district not on the card (needed for the ASR lookup)")
        trace.append(TraceStep("inputs.asr_rate", "ASR rate", None, None, {}, [], "blocked", "village / district missing"))
    else:
        rows = [r for r in asr_rows if r.year == year and r.village == village_f["value"] and r.district == district_f["value"]]
        rates = {r.rate(column) for r in rows if r.rate(column) is not None}
        srcs = [{"kind": "asr", "asr_id": str(r.id) if r.id else None, "year": r.year, "village": r.village, "zone": r.zone,
                 "column": column, "rate": str(r.rate(column)) if r.rate(column) is not None else None,
                 "status": r.status, "source_ref": r.source_ref} for r in rows]
        if not rates:
            blockers.append(f"no ASR {column} for {village_f['value']} ({district_f['value']}) in {year}-{str(year + 1)[-2:]}")
            trace.append(TraceStep("inputs.asr_rate", "ASR rate", None, None, {"column": column}, srcs, "blocked",
                                   "import the ASR table for this year (Admin → ASR rates)"))
        elif len(rates) > 1:
            blockers.append(f"{len(rows)} ASR rows (zones) with different rates for {village_f['value']} in {year}; zone not on the card")
            trace.append(TraceStep("inputs.asr_rate", "ASR rate", None, None, {"column": column}, srcs, "blocked", "ambiguous zone"))
        else:
            asr_rate = rates.pop()
            st = "ok"
            if any(r.status != "verified" for r in rows):
                unverified.append(f"ASR row for {village_f['value']} {year} is to_verify")
                st = "unverified"
            trace.append(TraceStep("inputs.asr_rate", "ASR rate", f"₹{asr_rate}/sq m", None, {"column": column},
                                   [evidence_ref(card, village_f)] + srcs, st))
    inputs.update(asr_year=year, asr_column=column, asr_rate_per_sqm=str(asr_rate) if asr_rate is not None else None)

    # 6. adjustments of the stack
    adjustments: list[Adjustment] = []
    for a in adjustments_for(stack, {**facts, "area_sqm": area}):
        if a["applies"] is False:
            continue
        if a["applies"] is None:
            trace.append(TraceStep(f"adj.{a['code']}", f"{a.get('kind', '').capitalize()} {a['code']}", None, None, {},
                                   [{"kind": "rule", "rule_set_code": a["rule_set_code"], "code": a["code"], "status": a["status"]}],
                                   "skipped", f"condition needs facts: {a['needs']}"))
            continue
        if a["status"] != "verified":
            unverified.append(f"adjustment {a['code']} ({a['rule_set_code']}) is to_verify")
        adjustments.append(Adjustment(code=a["code"], kind=a["kind"], rate_pct=to_decimal(a.get("rate_pct")),
                                      amount=to_decimal(a.get("amount")), days=days_late, status=a["status"],
                                      rule_set_code=a["rule_set_code"], source_ref=a.get("source_ref")))
    param_status("rounding")

    unverified = sorted(set(unverified))
    base = dict(trace=trace, blockers=blockers, unverified=unverified, inputs=inputs, params=params.to_json(),
                engine_version=f"{FEE_ENGINE_VERSION}+{params.fingerprint()}", rule_set_code=inputs.get("rule_set_code"))
    if blockers or res is None or rate_pct is None or area is None or asr_rate is None:
        return FeeResult(status="blocked", total=None, components={}, **base)
    try:
        c = compute(FeeInputs(area_sqm=area, asr_rate_per_sqm=asr_rate, rate_pct=rate_pct, rate_basis=res.slab.rate_basis,
                              min_amount=res.slab.min_amount, max_amount=res.slab.max_amount,
                              adjustments=tuple(adjustments), rounding=params.rounding))
    except FeeInputError as e:
        blockers.append(str(e))
        trace.append(TraceStep("calc", "Calculation", None, None, {}, [], "blocked", str(e)))
        return FeeResult(status="blocked", total=None, components={}, **base)
    trace.extend(c.trace)
    return FeeResult(status="unverified" if unverified else "computed", total=money(c.total), components=c.components, **base)
