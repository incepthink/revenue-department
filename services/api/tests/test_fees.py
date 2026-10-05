"""Fee engine: golden fixtures (eval/fees/golden.yaml), input preparation from a card with in-memory rule stacks
and ASR rows (verified / unverified / blocked paths), and Hypothesis properties of the arithmetic."""
import dataclasses
import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rci.config import REPO_ROOT
from rci.fees.engine import Adjustment, FeeInputError, FeeInputs, Rounding, compute
from rci.fees.golden import classify_blocked, load_fixtures, run_fixture
from rci.fees.prepare import AsrRow, FeeParams, area_observations, calculate, fiscal_year
from rci.rules.resolve import CodedRow, RuleSetRow, SlabRow, build_stack

D = dt.date.fromisoformat
GOLDEN = load_fixtures(REPO_ROOT / "eval" / "fees" / "golden.yaml")
VERIFIED = {k: "verified" for k in ("rule_date_basis", "asr_year_basis", "asr_rate_column", "area_basis", "rounding",
                                    "default_land_use", "premium_heads")}


# ------------------------------------------------------------------------------------------------
# Golden fixtures
# ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", GOLDEN, ids=[c["id"] for c in GOLDEN])
def test_golden_fixture(case):
    computed, expected = run_fixture(case)
    assert computed == expected, case["source"]


def test_golden_covers_the_sample_demand_letters():
    ids = {c["id"] for c in GOLDEN}
    assert {"DKT-001-demand", "DKT-003-demand"} <= ids


# ------------------------------------------------------------------------------------------------
# Engine units
# ------------------------------------------------------------------------------------------------
def test_trace_is_complete_and_consistent():
    c = compute(FeeInputs(Decimal("1250.50"), Decimal("73000"), Decimal("5")))
    steps = [t.step for t in c.trace]
    assert steps == ["calc.market_value", "calc.premium", "calc.rounding"]
    assert c.trace[0].value == "91286500.00" and c.trace[-1].value == "4564325.00" == c.components["total"]
    assert c.subtotal == Decimal("4564325.0000")


@pytest.mark.parametrize("kw,msg", [
    (dict(area_sqm=Decimal(0)), "area"), (dict(asr_rate_per_sqm=Decimal(-1)), "ASR"), (dict(rate_pct=Decimal(-1)), "negative"),
    (dict(rate_basis="built_up_value"), "unsupported"), (dict(min_amount=Decimal(10), max_amount=Decimal(5)), "minimum"),
])
def test_engine_refuses_bad_inputs(kw, msg):
    base = dict(area_sqm=Decimal(100), asr_rate_per_sqm=Decimal(1000), rate_pct=Decimal(5))
    with pytest.raises(FeeInputError, match=msg):
        compute(FeeInputs(**{**base, **kw}))


def test_adjustment_without_rate_or_days_is_skipped_not_guessed():
    c = compute(FeeInputs(Decimal(100), Decimal(1000), Decimal(5), adjustments=(
        Adjustment("LATE_INTEREST", "interest", rate_pct=Decimal(12)), Adjustment("X", "surcharge"))))
    assert c.total == Decimal("5000.00")
    assert [t.status for t in c.trace if t.step.startswith("adj.")] == ["skipped", "skipped"]


def test_rounding_rejects_unknown_mode():
    with pytest.raises(FeeInputError):
        Rounding("ROUND_NEAREST")


# ------------------------------------------------------------------------------------------------
# Preparation from a card (pure: in-memory stack + ASR rows)
# ------------------------------------------------------------------------------------------------
def fact(value, exhibit_type="APPLICATION", field="x", page=1):
    return {"value": value, "raw": str(value), "page_seq": page, "doc_page_no": page, "document": "d.pdf", "snippet": f"… {value} …",
            "method": "manual", "confidence": 1.0, "exhibit_id": f"ex-{exhibit_type}", "exhibit_type": exhibit_type, "field": field}


def mk_card(*, tenure="govt_lease", area="800.00", village="एरंडवणे", district="पुणे", app="2025-01-15", order=None,
            payments=(), ctso_area=None):
    card = {
        "case": {"case_id": "c-1", "case_ref": "T-1", "data_class": "synthetic", "case_type": "CHS_CLASS2_TO_CLASS1", "office_code": "PILOT"},
        "facts": {
            "land": {"tenure": fact(tenure, "PROPERTY_CARD", "tenure", 2) if tenure else None,
                     "area_sqm": fact(area, "PROPERTY_CARD", "area_sqm", 2) if area else None,
                     "village": fact(village, "PROPERTY_CARD", "village", 2), "district": fact(district, "APPLICATION", "district")},
            "application": {"date": fact(app, "APPLICATION", "application_date") if app else None},
            "order": {"order_date": fact(order, "COLLECTOR_ORDER", "order_date", 9) if order else None},
            "demand": {}, "payments": list(payments),
            "inspections": ([{"exhibit_id": "ex-CTSO_REPORT", "kind": "CTSO_REPORT",
                              "area_sqm": fact(ctso_area, "CTSO_REPORT", "area_sqm", 8)}] if ctso_area else []),
        },
        "exhibits": [], "validations": [],
    }
    return card


def stack_loader(rate="5", status="verified", slab_status="verified", holding="govt_lease", extra_slabs=(), adjustments=()):
    sets = [RuleSetRow("SYN_RULES", "base", D("2019-03-08"), status=status)]
    slabs = [SlabRow("SYN_RULES", "CHS", holding, "residential", Decimal(rate) if rate is not None else None, "asr_land_value",
                     status=slab_status, seed_key="SYN.SLAB"), *extra_slabs]
    return lambda on: build_stack(on, sets, slabs, adjustments=list(adjustments))


ASR = [AsrRow(2024, "पुणे", "एरंडवणे", Decimal("52300"), status="verified", source_ref="test")]
PARAMS = FeeParams(statuses=VERIFIED)


def test_verified_everything_is_computed():
    r = calculate(mk_card(), PARAMS, stack_loader(), ASR, today=D("2026-01-01"))
    assert r.status == "computed" and r.total == "2092000.00" and not r.blockers and not r.unverified
    steps = {t.step: t for t in r.trace}
    assert steps["inputs.rule_date"].value == "2025-01-15"
    assert steps["inputs.asr_year"].value == "2024-25"  # January 2025 is in ASR year 2024-25
    assert steps["inputs.area"].sources[0]["page_seq"] == 2
    assert steps["rules.slab"].sources[0]["seed_key"] == "SYN.SLAB"


def test_unverified_rule_still_computes_would_apply_amount():
    r = calculate(mk_card(), PARAMS, stack_loader(status="to_verify"), ASR, today=D("2026-01-01"))
    assert r.status == "unverified" and r.total == "2092000.00"
    assert any("to_verify" in u for u in r.unverified)


def test_to_verify_parameters_make_the_result_unverified():
    r = calculate(mk_card(), FeeParams(), stack_loader(), ASR, today=D("2026-01-01"))
    assert r.status == "unverified" and any(u.startswith("parameter ") for u in r.unverified)


@pytest.mark.parametrize("card_kw,loader_kw,asr,needle,cls", [
    ({}, dict(rate=None), ASR, "rate not filled in", "unverified_rule_rate_missing"),
    ({}, {}, [], "no ASR", "missing_asr"),
    ({"area": None}, {}, ASR, "no area", "missing_area"),
    ({"tenure": "occupancy_class_1"}, {}, ASR, "already Occupancy Class-I", "not_applicable_class1"),
    ({"tenure": None}, {}, ASR, "tenure", "missing_tenure"),
    ({}, dict(holding="occupancy_class_2"), ASR, "no rule", "no_rule"),
    ({}, {}, ASR + [AsrRow(2024, "पुणे", "एरंडवणे", Decimal("60000"), zone="15")], "zones", "ambiguous_asr_zone"),
])
def test_blocked_results_name_their_blocker(card_kw, loader_kw, asr, needle, cls):
    r = calculate(mk_card(**card_kw), PARAMS, stack_loader(**loader_kw), asr, today=D("2026-01-01"))
    assert r.status == "blocked" and r.total is None
    assert any(needle in b for b in r.blockers), r.blockers
    assert classify_blocked(r) == cls
    assert r.trace  # still shows the steps it could do


def test_ambiguous_slabs_block():
    dup = SlabRow("SYN_RULES", "CHS", "govt_lease", "residential", Decimal("7"), "asr_land_value", status="verified")
    r = calculate(mk_card(), PARAMS, stack_loader(extra_slabs=[dup]), ASR, today=D("2026-01-01"))
    assert r.status == "blocked" and any("ambiguous rule" in b for b in r.blockers)


def test_rule_date_falls_back_to_analysis_date():
    params = dataclasses.replace(PARAMS, rule_date_basis="order_date")
    r = calculate(mk_card(), params, stack_loader(), ASR, today=D("2026-01-01"))
    step = next(t for t in r.trace if t.step == "inputs.rule_date")
    assert step.value == "2026-01-01" and step.status == "assumption"


def test_area_basis_order_and_ctso():
    card = mk_card(ctso_area="842.00")
    assert set(area_observations(card)) == {"property_card", "ctso_measured"}
    r = calculate(card, dataclasses.replace(PARAMS, area_basis=("ctso_measured", "property_card")), stack_loader(), ASR,
                  today=D("2026-01-01"))
    assert r.inputs["area_basis"] == "ctso_measured" and r.total == "2201830.00"  # 842 × 52,300 × 5 %


def test_rule_adjustments_apply_with_their_status():
    rebate = CodedRow("SYN_RULES", "CONCESSION", {"tenure": "govt_lease"}, "verified",
                      {"kind": "rebate", "rate_pct": Decimal("50"), "amount": None, "applies_from": None, "applies_to": None})
    other = CodedRow("SYN_RULES", "OC2_ONLY", {"tenure": "occupancy_class_2"}, "verified",
                     {"kind": "surcharge", "rate_pct": Decimal("10"), "amount": None, "applies_from": None, "applies_to": None})
    r = calculate(mk_card(), PARAMS, stack_loader(adjustments=[rebate, other]), ASR, today=D("2026-01-01"))
    assert r.total == "1046000.00" and "adj.CONCESSION" in r.components and "adj.OC2_ONLY" not in r.components


def test_fiscal_year():
    assert fiscal_year(D("2025-03-31")) == 2024 and fiscal_year(D("2025-04-01")) == 2025


# ------------------------------------------------------------------------------------------------
# Hypothesis properties
# ------------------------------------------------------------------------------------------------
money = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("100000"), places=2)
rate = st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), places=4)
units = st.sampled_from([Decimal("1"), Decimal("10"), Decimal("100")])
modes = st.sampled_from(["ROUND_HALF_UP", "ROUND_HALF_EVEN", "ROUND_UP", "ROUND_DOWN", "ROUND_CEILING", "ROUND_FLOOR"])


@settings(max_examples=300, deadline=None)
@given(money, money, rate, units, modes)
def test_total_is_a_rounded_non_negative_multiple_of_the_unit(area, asr, pct, unit, mode):
    c = compute(FeeInputs(area, asr, pct, rounding=Rounding(mode, unit)))
    assert c.total >= 0
    assert c.total % unit == 0
    assert abs(c.total - c.subtotal) <= unit
    assert c.subtotal == area * asr * pct / 100  # exact: nothing rounded before the total


@settings(max_examples=300, deadline=None)
@given(money, money, money, rate)
def test_monotonic_in_area_and_symmetric(a1, a2, asr, pct):
    lo, hi = sorted((a1, a2))
    assert compute(FeeInputs(lo, asr, pct)).total <= compute(FeeInputs(hi, asr, pct)).total
    assert compute(FeeInputs(a1, asr, pct)).total == compute(FeeInputs(asr, a1, pct)).total


@settings(max_examples=200, deadline=None)
@given(money, money, rate, money, money)
def test_limits_are_respected(area, asr, pct, x, y):
    lo, hi = sorted((x * 10, y * 10))
    c = compute(FeeInputs(area, asr, pct, min_amount=lo, max_amount=hi, rounding=Rounding("ROUND_HALF_UP", Decimal("0.01"))))
    assert lo <= c.total <= hi


@settings(max_examples=200, deadline=None)
@given(money, money, rate, st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), places=2))
def test_rebates_never_increase_and_surcharges_never_decrease(area, asr, pct, adj):
    base = compute(FeeInputs(area, asr, pct)).subtotal
    down = compute(FeeInputs(area, asr, pct, adjustments=(Adjustment("R", "rebate", rate_pct=adj),))).subtotal
    up = compute(FeeInputs(area, asr, pct, adjustments=(Adjustment("S", "surcharge", rate_pct=adj),))).subtotal
    assert down <= base <= up
    assert down >= 0


@settings(max_examples=200, deadline=None)
@given(money, money, rate)
def test_premium_is_linear_in_the_rate(area, asr, pct):
    assert compute(FeeInputs(area, asr, pct * 2)).subtotal == 2 * compute(FeeInputs(area, asr, pct)).subtotal


@settings(max_examples=100, deadline=None)
@given(money, money, rate)
def test_deterministic_and_trace_ends_with_the_total(area, asr, pct):
    a, b = compute(FeeInputs(area, asr, pct)), compute(FeeInputs(area, asr, pct))
    assert a.total == b.total and [t.to_json() for t in a.trace] == [t.to_json() for t in b.trace]
    assert a.trace[-1].value == a.components["total"]
