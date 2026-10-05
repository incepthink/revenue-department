"""Golden checks of the fee engine.

1. Fixtures (`eval/fees/golden.yaml`): explicit engine inputs with the amount a precedent challan / demand letter
   shows. Run by pytest (`tests/test_fees.py`); they must match to the paisa.
2. Precedents (`rci fee-golden`): every approved case with a premium challan (else the order / demand amount) is
   recomputed from the rules layer + ASR table. Each difference gets a class, so "every mismatch is classified"
   can be checked: blocked results are classified by their blocker (no verified rate, no ASR row, ...), computed
   ones are re-run with each alternative parameter value to find the one that explains the difference.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.db.models import Case, CaseCard
from rci.fees.engine import Adjustment, FeeInputs, Rounding, compute, money, to_decimal
from rci.fees.prepare import FeeParams, FeeResult, area_observations, premium_payments
from rci.fees.service import calculate_for_card, load_params, stack_loader

BLOCKER_CLASSES = [  # (substring of the blocker, class) — first match wins
    ("rate not filled in", "unverified_rule_rate_missing"),
    ("ambiguous rule", "ambiguous_rule"),
    ("no rule", "no_rule"),
    ("no ASR", "missing_asr"),
    ("ASR rows (zones)", "ambiguous_asr_zone"),
    ("no area", "missing_area"),
    ("already Occupancy Class-I", "not_applicable_class1"),
    ("tenure", "missing_tenure"),
    ("village / district", "missing_location"),
]
ALTERNATIVES = {
    "rule_date_basis": ["application_date", "order_date", "payment_date"],
    "asr_year_basis": ["application_date", "order_date", "demand_date"],
}


# ------------------------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------------------------
def load_fixtures(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]


def fixture_inputs(case: dict) -> FeeInputs:
    i = case["inputs"]
    r = i.get("rounding") or {}
    return FeeInputs(
        area_sqm=to_decimal(i["area_sqm"]), asr_rate_per_sqm=to_decimal(i["asr_rate_per_sqm"]), rate_pct=to_decimal(i["rate_pct"]),
        rate_basis=i.get("rate_basis", "asr_land_value"), min_amount=to_decimal(i.get("min_amount")),
        max_amount=to_decimal(i.get("max_amount")),
        adjustments=tuple(Adjustment(code=a["code"], kind=a["kind"], rate_pct=to_decimal(a.get("rate_pct")),
                                     amount=to_decimal(a.get("amount")), days=a.get("days"), status="verified")
                          for a in i.get("adjustments", [])),
        rounding=Rounding(r.get("mode", "ROUND_HALF_UP"), Decimal(str(r.get("unit", "1")))),
    )


def run_fixture(case: dict) -> tuple[str, str]:
    """(computed total, expected total) as 2-decimal strings."""
    return money(compute(fixture_inputs(case)).total), money(Decimal(str(case["expected_total"])))


# ------------------------------------------------------------------------------------------------
# Precedents
# ------------------------------------------------------------------------------------------------
def observed_premium(card: dict, params: FeeParams) -> tuple[Decimal | None, str | None, list[dict]]:
    """(amount, source, evidence): premium challans, else the order's premium, else the demand letter amount."""
    pays = premium_payments(card, params.premium_heads)
    if pays:
        total = sum((to_decimal(p["amount"]["value"]) for p in pays), Decimal(0))
        return total, "challan", [p["amount"] for p in pays]
    for path, source in (("order.premium_amount", "order"), ("demand.amount", "demand_letter")):
        group, _, name = path.partition(".")
        f = (card["facts"].get(group) or {}).get(name)
        if isinstance(f, dict) and f.get("value") not in (None, ""):
            return to_decimal(f["value"]), source, [f]
    return None, None, []


def classify_blocked(res: FeeResult) -> str:
    for needle, cls in BLOCKER_CLASSES:
        if any(needle in b for b in res.blockers):
            return cls
    return "unexplained"


def _alt_params(params: FeeParams, card: dict) -> list[tuple[str, FeeParams]]:
    alts: list[tuple[str, FeeParams]] = []
    for key, values in ALTERNATIVES.items():
        for v in values:
            if v != getattr(params, key):
                alts.append((f"{key}={v}", dataclasses.replace(params, **{key: v})))
    for basis in area_observations(card):
        if params.area_basis and basis != params.area_basis[0]:
            alts.append((f"area_basis={basis}", dataclasses.replace(params, area_basis=(basis,))))
    for unit in ("10", "100", "1000"):
        alts.append((f"rounding.unit={unit}", dataclasses.replace(params, rounding=Rounding(params.rounding.mode, Decimal(unit)))))
    return alts


def check_card(session: Session, card: dict, params: FeeParams, loader, today: dt.date) -> dict:
    observed, source, ev = observed_premium(card, params)
    out = {"case_id": card["case"]["case_id"], "case_ref": card["case"]["case_ref"], "observed": money(observed) if observed is not None else None,
           "observed_source": source, "observed_evidence": [{"page_seq": e.get("page_seq"), "snippet": e.get("snippet"),
                                                             "exhibit_id": e.get("exhibit_id")} for e in ev]}
    if observed is None:
        return {**out, "class": "no_observed_amount", "computed": None, "status": None, "detail": "no challan / order / demand amount"}
    res = calculate_for_card(session, card, params=params, today=today, loader=loader)
    out.update(computed=res.total, status=res.status, engine_version=res.engine_version)
    if res.total is None:
        return {**out, "class": classify_blocked(res), "detail": "; ".join(res.blockers)}
    diff = Decimal(res.total) - observed
    out["diff"] = money(diff)
    if diff == 0:
        return {**out, "class": "match", "detail": None}
    if abs(diff) <= params.rounding.unit:
        return {**out, "class": "rounding", "detail": f"within one rounding unit (₹{params.rounding.unit})"}
    for label, alt in _alt_params(params, card):
        r = calculate_for_card(session, card, params=alt, today=today, loader=loader)
        if r.total is not None and Decimal(r.total) == observed:
            return {**out, "class": f"parameter:{label}", "detail": f"matches with {label}"}
    return {**out, "class": "unexplained", "detail": f"computed {res.total} vs observed {money(observed)}"}


def precedent_report(session: Session, *, today: dt.date | None = None) -> dict:
    params = load_params()
    loader = stack_loader(session)
    today = today or dt.date.today()
    rows = session.execute(
        select(Case, CaseCard).join(CaseCard, CaseCard.case_id == Case.id)
        .where(CaseCard.status == "approved", CaseCard.locked, Case.role != "holdout").order_by(Case.case_ref)
    ).all()
    cases = [check_card(session, cc.card, params, loader, today) for _, cc in rows]
    by_class: dict[str, int] = {}
    for c in cases:
        by_class[c["class"]] = by_class.get(c["class"], 0) + 1
    checked = [c for c in cases if c["class"] != "no_observed_amount"]
    return {"engine_params": params.to_json(), "precedents": len(cases), "checked": len(checked),
            "matches": by_class.get("match", 0), "unexplained": by_class.get("unexplained", 0),
            "by_class": dict(sorted(by_class.items())), "cases": cases}


def report_markdown(r: dict) -> str:
    lines = ["# Fee golden check (precedent challans)", "",
             f"Precedents: {r['precedents']} · with an observed amount: {r['checked']} · exact matches: {r['matches']} · "
             f"**unexplained: {r['unexplained']}**", "", "| class | cases |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in r["by_class"].items()]
    lines += ["", "| case | observed (source) | computed | status | class | detail |", "|---|---|---|---|---|---|"]
    for c in r["cases"]:
        lines.append(f"| {c['case_ref']} | {c['observed'] or '—'} ({c['observed_source'] or '—'}) | {c.get('computed') or '—'} | "
                     f"{c.get('status') or '—'} | {c['class']} | {(c.get('detail') or '').replace('|', '/')} |")
    return "\n".join(lines) + "\n"
