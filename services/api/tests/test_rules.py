"""Rules layer: stacking, slab resolution, UnverifiedRule / AmbiguousRule, and the 30-Sep-2024 and
31-Dec-2025 boundaries (pure fixtures + the seeded rule rows)."""
import datetime as dt
from decimal import Decimal

import pytest

from rci.rules.resolve import (
    AmbiguousRule,
    CodedRow,
    NoRuleFound,
    RuleSetRow,
    SlabRow,
    UnverifiedRule,
    adjustments_for,
    build_stack,
    document_checks_for,
    evaluate,
    requirements_for,
    resolve_slab,
    select_stack,
)

D = dt.date.fromisoformat
KEY = dict(applicant_category="CHS", holding_basis="govt_lease", land_use="residential")


def _sets(status="verified"):
    return [
        RuleSetRow("BASE", "base", D("2019-03-08"), status=status),
        RuleSetRow("AMEND23", "amendment", D("2023-03-27"), notification_date=D("2023-03-27"), status=status),
        RuleSetRow("AMN24A", "amnesty", D("2024-03-16"), notification_date=D("2024-03-16"), status=status),
        RuleSetRow("AMN24B", "amnesty", D("2024-06-28"), notification_date=D("2024-06-28"), supersedes="AMN24A", status=status),
        RuleSetRow("N25", "amnesty", D("2025-03-04"), notification_date=D("2025-03-04"), status=status),
    ]


def _slabs(status="verified"):
    def s(rs, rate, wf=None, wt=None, **kw):
        return SlabRow(rs, "CHS", "govt_lease", "residential", Decimal(rate), "asr_land_value", wf, wt, status=status,
                       seed_key=f"{rs}.{rate}", **kw)

    return [
        s("BASE", "25"), s("AMEND23", "15"),
        s("AMN24A", "5", D("2024-03-16")),
        s("AMN24B", "5", D("2024-03-16"), D("2024-09-30")),
        s("N25", "7", D("2025-03-04"), D("2025-12-31")),
        SlabRow("BASE", "CHS", "occupancy_class_2", "residential", Decimal("10"), "asr_land_value", status=status),
    ]


def stack(on, status="verified", slab_status="verified"):
    return build_stack(D(on), _sets(status), _slabs(slab_status))


@pytest.mark.parametrize("on,rule_set,rate", [
    ("2019-03-07", None, None),           # before the rules
    ("2019-03-08", "BASE", "25"),
    ("2023-03-26", "BASE", "25"),
    ("2023-03-27", "AMEND23", "15"),
    ("2024-03-16", "AMN24A", "5"),        # first amnesty, before the 28-Jun notification
    ("2024-06-28", "AMN24B", "5"),        # 24A superseded from here
    ("2024-09-30", "AMN24B", "5"),        # last day of the 2024 window (inclusive)
    ("2024-10-01", "AMEND23", "15"),      # window closed → amended base rate again
    ("2025-03-03", "AMEND23", "15"),
    ("2025-03-04", "N25", "7"),
    ("2025-12-31", "N25", "7"),           # last day of the 2025 window (inclusive)
    ("2026-01-01", "AMEND23", "15"),      # no 2026 extension loaded → back to the amended base
])
def test_boundaries(on, rule_set, rate):
    st = stack(on)
    if rule_set is None:
        with pytest.raises(NoRuleFound):
            resolve_slab(st, **KEY)
        return
    res = resolve_slab(st, **KEY)
    assert (res.rule_set.code, str(res.slab.rate_pct)) == (rule_set, rate)
    assert res.verified and res.trace[-1].startswith("selected")


def test_superseded_rule_set_leaves_the_stack():
    assert "AMN24A" in select_stack_codes("2024-06-27")
    assert "AMN24A" not in select_stack_codes("2024-06-28")


def select_stack_codes(on):
    return [r.code for r in select_stack(_sets(), D(on))]


def test_stack_order_is_precedence_then_date():
    assert select_stack_codes("2025-06-01") == ["BASE", "AMEND23", "AMN24B", "N25"]


def test_effective_to_is_inclusive():
    sets = [RuleSetRow("B", "base", D("2020-01-01"), effective_to=D("2020-12-31"))]
    assert [r.code for r in select_stack(sets, D("2020-12-31"))] == ["B"]
    assert select_stack(sets, D("2021-01-01")) == []


@pytest.mark.parametrize("rs_status,slab_status", [("to_verify", "verified"), ("verified", "to_verify"), ("to_verify", "to_verify")])
def test_unverified_rule_raises_with_resolution(rs_status, slab_status):
    with pytest.raises(UnverifiedRule) as e:
        resolve_slab(stack("2024-09-30", rs_status, slab_status), **KEY)
    assert e.value.resolution.rule_set.code == "AMN24B"
    res = resolve_slab(stack("2024-09-30", rs_status, slab_status), **KEY, allow_unverified=True)
    assert not res.verified and res.rule_set.code == "AMN24B"


def test_missing_rate_is_unverified_even_when_rows_are_verified():
    sets = [RuleSetRow("B", "base", D("2019-01-01"), status="verified")]
    slabs = [SlabRow("B", "CHS", "govt_lease", "residential", None, "asr_land_value", status="verified")]
    with pytest.raises(UnverifiedRule, match="rate not filled in"):
        resolve_slab(build_stack(D("2020-01-01"), sets, slabs), **KEY)


def test_ambiguous_same_precedence_different_terms():
    sets = _sets() + [RuleSetRow("OTHER", "amnesty", D("2024-07-01"), notification_date=D("2024-07-01"), status="verified")]
    slabs = _slabs() + [SlabRow("OTHER", "CHS", "govt_lease", "residential", Decimal("3"), "asr_land_value",
                                D("2024-07-01"), D("2024-12-31"), status="verified")]
    with pytest.raises(AmbiguousRule) as e:
        resolve_slab(build_stack(D("2024-08-01"), sets, slabs), **KEY)
    assert len(e.value.candidates) == 2
    # after the 2024 window closes only OTHER matches → no ambiguity
    assert resolve_slab(build_stack(D("2024-10-15"), sets, slabs), **KEY).rule_set.code == "OTHER"


def test_identical_terms_are_not_ambiguous():
    sets = [RuleSetRow("A", "amnesty", D("2024-01-01"), notification_date=D("2024-01-01"), status="verified"),
            RuleSetRow("B", "amnesty", D("2024-02-01"), notification_date=D("2024-02-01"), status="verified")]
    slabs = [SlabRow(c, "CHS", "govt_lease", "residential", Decimal("5"), "asr_land_value", status="verified") for c in "AB"]
    assert resolve_slab(build_stack(D("2024-03-01"), sets, slabs), **KEY).rule_set.code == "B"


def test_conditions_select_and_missing_fact_is_ambiguous():
    sets = [RuleSetRow("B", "base", D("2019-01-01"), status="verified")]
    slabs = [
        SlabRow("B", "CHS", "govt_lease", "residential", Decimal("5"), "asr_land_value", conditions={"area_sqm": {"lte": 1000}}, status="verified"),
        SlabRow("B", "CHS", "govt_lease", "residential", Decimal("10"), "asr_land_value", conditions={"area_sqm": {"gt": 1000}}, status="verified"),
    ]
    st = build_stack(D("2020-01-01"), sets, slabs)
    assert resolve_slab(st, **KEY, facts={"area_sqm": Decimal("800")}).slab.rate_pct == Decimal("5")
    assert resolve_slab(st, **KEY, facts={"area_sqm": 1500}).slab.rate_pct == Decimal("10")
    with pytest.raises(AmbiguousRule) as e:
        resolve_slab(st, **KEY)
    assert e.value.needs == ["area_sqm"]


def test_other_holding_basis_does_not_leak():
    res = resolve_slab(stack("2024-09-30"), applicant_category="CHS", holding_basis="occupancy_class_2", land_use="residential")
    assert res.rule_set.code == "BASE" and res.slab.rate_pct == Decimal("10")


def test_evaluate_operators():
    facts = {"tenure": "govt_lease", "filed_on": D("2024-05-01"), "n": 3}
    assert evaluate({"tenure": "govt_lease"}, facts) == (True, [])
    assert evaluate({"tenure": {"in": ["govt_lease", "occupancy_class_2"]}}, facts) == (True, [])
    assert evaluate({"filed_on": {"lte": "2024-09-30"}}, facts) == (True, [])
    assert evaluate({"n": {"gt": 3}}, facts) == (False, [])
    assert evaluate({"x": 1}, facts) == (None, ["x"])
    with pytest.raises(ValueError):
        evaluate({"n": {"approx": 3}}, facts)


def test_requirements_later_rule_set_overrides():
    reqs = [CodedRow("BASE", "LEASE_DOC", {"tenure": "govt_lease"}, "verified", {"mandatory": True}),
            CodedRow("AMEND23", "LEASE_DOC", {}, "verified", {"mandatory": False})]
    st = build_stack(D("2024-01-01"), _sets(), requirements=reqs)
    [r] = requirements_for(st, {"tenure": "occupancy_class_2"})
    assert (r["rule_set_code"], r["mandatory"], r["applies"]) == ("AMEND23", False, True)
    st = build_stack(D("2020-01-01"), _sets(), requirements=reqs)
    [r] = requirements_for(st, {"tenure": "occupancy_class_2"})
    assert (r["rule_set_code"], r["applies"]) == ("BASE", False)


def test_document_checks_stack_like_requirements():
    def chk(rs, code, slot, text, condition=None):
        return CodedRow(rs, code, condition or {}, "verified", {"slot": slot, "text_en": text, "severity": "problem"})

    rows = [chk("BASE", "SIGNED", "application", "signed by the chairman"),
            chk("AMEND23", "SIGNED", "application", "signed by an office-bearer"),
            chk("BASE", "LEASE_TERMS", "grant", "states the lease period", {"tenure": "govt_lease"}),
            chk("BASE", "MOVED", "application", "x"), chk("AMEND23", "MOVED", "other", "y")]
    st = build_stack(D("2024-01-01"), _sets(), document_checks=rows)
    [signed] = document_checks_for(st, "application")
    assert (signed["rule_set_code"], signed["text_en"]) == ("AMEND23", "signed by an office-bearer")
    assert [c["code"] for c in document_checks_for(st, "other")] == ["MOVED"]  # the later row moved it to another slot
    old = build_stack(D("2020-01-01"), _sets(), document_checks=rows)
    assert {c["text_en"] for c in document_checks_for(old, "application")} == {"signed by the chairman", "x"}
    [lease] = document_checks_for(st, "grant", {"tenure": "occupancy_class_2"})
    assert lease["applies"] is False
    [lease] = document_checks_for(st, "grant")
    assert lease["applies"] is None and lease["needs"] == ["tenure"]
    assert len(document_checks_for(st)) == 3


def test_adjustment_date_window():
    adj = [CodedRow("BASE", "WAIVER", {}, "verified", {"applies_from": D("2024-03-16"), "applies_to": D("2024-09-30")})]
    assert adjustments_for(build_stack(D("2024-09-30"), _sets(), adjustments=adj))
    assert not adjustments_for(build_stack(D("2024-10-01"), _sets(), adjustments=adj))


# ---------------------------------------------------------------------------------------------
# Seeded rows (DB): the 2019 rules are the only seeded rule set, and every slab is unverified.
# ---------------------------------------------------------------------------------------------
@pytest.mark.db
@pytest.mark.parametrize("on", ["2020-06-01", "2024-09-30", "2026-01-01"])
def test_seeded_rules(db_session, on):
    from rci.rules import stack_for

    st = stack_for(db_session, D(on))
    assert st.codes == ["RULES_2019"]
    for holding in ("govt_lease", "occupancy_class_2"):
        with pytest.raises(UnverifiedRule) as e:
            resolve_slab(st, applicant_category="CHS", holding_basis=holding, land_use="residential")
        assert e.value.resolution.rule_set.code == "RULES_2019"
    with pytest.raises(NoRuleFound):
        resolve_slab(stack_for(db_session, D("2019-01-01")), applicant_category="CHS", holding_basis="govt_lease",
                     land_use="residential")


@pytest.mark.db
def test_seeded_intake_rules(db_session):
    from rci.rules import stack_for

    st = stack_for(db_session, D("2025-01-15"))
    slots = {r["code"]: r["intake_slot"] for r in requirements_for(st)}
    assert slots["SATBARA_EXTRACT"] == "satbara" and slots["APPLICATION_FORM"] == "application" and slots["SGM_RESOLUTION"] is None
    checks = {c["code"]: c for c in document_checks_for(st)}
    assert checks["APPLICATION_SIGNED_BY_OFFICE_BEARER"]["slot"] == "application"
    assert checks["LEASE_PERIOD_AND_RENT_STATED"]["condition"] == {"tenure": "govt_lease"}
    assert all(c["status"] == "to_verify" for c in checks.values())
    assert {c["slot"] for c in checks.values()} == {"application", "satbara", "property_card", "grant", "aadhaar",
                                                    "tax_receipts", "index_ii", "other"}


@pytest.mark.db
def test_seed_does_not_overwrite_admin_edits(db_session):
    from sqlalchemy import select

    from rci.auth import create_user
    from rci.db.models import RulePremiumSlab
    from rci.seed import seed_rules

    admin = create_user(db_session, email="seed-guard@example.org", display_name="A", role="admin", password="password-123")
    slab = db_session.scalar(select(RulePremiumSlab).where(RulePremiumSlab.seed_key == "RULES_2019.CHS.OC2.COM"))
    slab.rate_pct, slab.updated_by = Decimal("12.5"), admin.id
    db_session.flush()
    seed_rules(db_session)
    db_session.refresh(slab)
    assert slab.rate_pct == Decimal("12.5")
    slab.rate_pct, slab.updated_by = None, None  # leave the shared test DB as seeded
    db_session.flush()
