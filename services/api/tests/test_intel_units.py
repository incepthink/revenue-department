"""Intelligence engine, pure parts: rule-compatibility labels, rerank features / reasons, gap support and citations,
next steps (frequency, medians, rule and fact triggers), the constrained explanation post-check, config overlay."""
import datetime as dt
from decimal import Decimal

import pytest
import yaml

from rci.adapters.llm import FakeLLMClient
from rci.config import REPO_ROOT
from rci.intel.config import FEATURES, IntelConfig
from rci.intel.explain import check, explain
from rci.intel.gaps import GapSettings, analyse_gaps
from rci.intel.profile import build_profile
from rci.intel.rank import Candidate, features, jaccard, rerank, score
from rci.intel.rulecompat import PRACTICE, PRACTICE_OLD_RULES, STATUTORY, SUPERSEDED, Support, label
from rci.intel.steps import next_steps, weighted_median

D = dt.date.fromisoformat
CATALOG = [
    {"code": "PROPERTY_CARD_RECENT", "name_en": "Recent property card", "name_mr": "अद्ययावत मिळकत पत्रिका", "category": "applicant_document", "status": "to_verify", "applies_when": {}},
    {"code": "MEMBER_LIST", "name_en": "Member list", "name_mr": "सभासद यादी", "category": "applicant_document", "status": "to_verify", "applies_when": {}},
    {"code": "NO_DUES_LEASE_RENT", "name_en": "No dues", "name_mr": "थकबाकी नसल्याचा दाखला", "category": "applicant_document", "status": "to_verify", "applies_when": {"tenure": "govt_lease"}},
    {"code": "CONVEYANCE_DEED", "name_en": "Conveyance", "name_mr": "अभिहस्तांतरण", "category": "applicant_document", "status": "to_verify", "applies_when": {}},
    {"code": "FIELD_INSPECTION", "name_en": "Inspection", "name_mr": "स्थळ पाहणी", "category": "office_process", "status": "to_verify", "applies_when": {}},
    {"code": "PREMIUM_PAYMENT", "name_en": "Premium", "name_mr": "नजराणा", "category": "payment", "status": "to_verify", "applies_when": {}},
]
EVENTS = [{"code": c, "name_en": c, "name_mr": c, "actor": "office", "typical_order": o}
          for c, o in (("APPLICATION_FILED", 10), ("QUERY_ISSUED", 20), ("QUERY_REPLIED", 30), ("FIELD_INSPECTION_DONE", 40),
                       ("CTSO_MEASUREMENT_DONE", 50), ("DEMAND_ISSUED", 60), ("PREMIUM_PAID", 70), ("ORDER_ISSUED", 80))]


def f(value, et="APPLICATION", field="x", page=1, ex=None):
    return {"value": value, "raw": str(value), "page_seq": page, "doc_page_no": page, "document": "d.pdf", "snippet": f"«{value}»",
            "method": "manual", "confidence": 1.0, "exhibit_id": ex or f"{et}-1", "exhibit_type": et, "field": field}


def mk_card(ref, *, tenure="govt_lease", village="एरंडवणे", area="800.00", app="2025-01-15", order=None, society=None,
            reqs=None, events=(), failed=(), encroachment=None):
    """reqs: code -> status, or (status, queried: bool)."""
    exhibits = [{"exhibit_id": "APPLICATION-1", "seq": 1, "type": "APPLICATION", "stage": "filing", "pages": [1], "page_range": "1",
                 "confidence": 1, "classification_method": "manual"},
                {"exhibit_id": "QUERY_LETTER-1", "seq": 2, "type": "QUERY_LETTER", "stage": "internal", "pages": [5], "page_range": "5",
                 "confidence": 1, "classification_method": "manual"}]
    requirements = []
    for code, spec in (reqs or {}).items():
        status, queried = spec if isinstance(spec, tuple) else (spec, False)
        ev = [{"exhibit_id": "QUERY_LETTER-1", "field": "query_items", "page_seq": 5, "snippet": f"{code} सादर करावे", "raw": code}] if queried else []
        requirements.append({"code": code, "status": status, "was_queried_in": "QUERY_LETTER-1" if queried else None,
                             "satisfied_by_exhibit_id": "APPLICATION-1" if status in ("present", "invalid") else None,
                             "evidence": ev, "catalog_status": "to_verify"})
    return {
        "case": {"case_id": f"id-{ref}", "case_ref": ref, "data_class": "synthetic", "case_type": "CHS_CLASS2_TO_CLASS1", "office_code": "PILOT"},
        "status": "approved" if order else "draft",
        "facts": {"society": {"name": f(society or f"{ref} सहकारी गृहनिर्माण संस्था", field="society_name")},
                  "land": {"tenure": f(tenure, "PROPERTY_CARD", "tenure", 2), "village": f(village, "PROPERTY_CARD", "village", 2),
                           "taluka": f("हवेली"), "district": f("पुणे"), "area_sqm": f(area, "PROPERTY_CARD", "area_sqm", 2)},
                  "application": {"date": f(app, field="application_date")},
                  "order": {"order_date": f(order, "COLLECTOR_ORDER", "order_date", 9) if order else None},
                  "queries": [], "payments": [],
                  "inspections": ([{"exhibit_id": "FIELD_REPORT-1", "kind": "FIELD_REPORT",
                                    "encroachment": f(encroachment, "FIELD_REPORT", "encroachment", 7)}] if encroachment is not None else [])},
        "exhibits": exhibits,
        "validations": [{"code": v, "status": "fail", "message": f"{v} failed", "parameters": [], "depends_on_unverified": False,
                         "evidence": [f("800.00", "PROPERTY_CARD", "area_sqm", 2)]} for v in failed],
        "requirements": requirements,
        "events": [{"seq": i, "code": c, "date": d, "exhibit_id": "APPLICATION-1", "evidence": f(d, page=i)} for i, (c, d) in enumerate(events, 1)],
    }


def cand(card, score=1.0, regime=("R19",)):
    return Candidate(profile=build_profile(card), card_id=f"card-{card['case']['case_ref']}", regime=regime, proc_regime=regime, score=score)


# ------------------------------------------------------------------------------------------------
# Rule compatibility
# ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("statutory,supports,expected", [
    (True, [], STATUTORY),
    (False, [Support(1, True, False), Support(1, True, False)], PRACTICE),
    (False, [Support(1, False, False), Support(0.5, True, False)], PRACTICE_OLD_RULES),
    (False, [Support(1, False, True), Support(0.2, True, False)], SUPERSEDED),
    (False, [], PRACTICE),
])
def test_rule_compatibility_label(statutory, supports, expected):
    assert label(statutory, supports) == expected


# ------------------------------------------------------------------------------------------------
# Rerank
# ------------------------------------------------------------------------------------------------
def test_features_and_reasons():
    q = build_profile(mk_card("Q", reqs={"PROPERTY_CARD_RECENT": "missing"}, failed=["AREA_TOLERANCE"]))
    same = cand(mk_card("P1", area="820.00", reqs={"PROPERTY_CARD_RECENT": ("present", True)}, failed=["AREA_TOLERANCE"], order="2024-12-01"))
    other = cand(mk_card("P2", tenure="occupancy_class_2", village="बाणेर", area="4000.00", order="2018-01-01"), regime=("OLD",))
    defaults = IntelConfig.load(tuned_path=REPO_ROOT / "config" / "__no_tuned__.yaml")  # not whatever the last `rci tune` wrote
    ranked = rerank(q, [other, same], defaults.weights, ("R19",), q.application_date)
    assert [c.profile.case_ref for c in ranked] == ["P1", "P2"]
    top = ranked[0]
    assert top.features["tenure"] == 1.0 and top.features["location"] == 1.0 and top.features["gaps"] == 1.0
    assert top.features["validations"] == 1.0 and top.features["regime"] == 1.0
    assert sum(b["contribution"] for b in top.breakdown.values()) == pytest.approx(top.score, abs=1e-3)
    texts = " | ".join(r["en"] for r in top.reasons)
    assert "Same holding basis" in texts and "Same village" in texts and "AREA_TOLERANCE" in texts
    assert all(r["mr"] for r in top.reasons)
    assert any(r["kind"] == "difference" for r in ranked[1].reasons)


def test_unknown_features_are_neutral_and_zero_weights_ignored():
    q = build_profile(mk_card("Q"))
    c = cand(mk_card("P"))
    c.sims = {"facts": None, "documents": None, "issues": None}
    fs = features(q, c, ("R19",), q.application_date)
    assert fs["sim_facts"] == 0.5 and fs["gaps"] == 0.5  # both have no open requirements
    s, br = score(fs, {**{k: 0.0 for k in FEATURES}, "tenure": 1.0})
    assert s == 1.0 and br["sim_facts"]["contribution"] == 0


def test_jaccard():
    assert jaccard(set(), set()) == 0.5 and jaccard({1}, {1, 2}) == 0.5 and jaccard({1}, {2}) == 0


# ------------------------------------------------------------------------------------------------
# Gaps
# ------------------------------------------------------------------------------------------------
def gap_setup(threshold=0.5):
    q = build_profile(mk_card("Q", reqs={"PROPERTY_CARD_RECENT": "invalid", "MEMBER_LIST": "missing", "NO_DUES_LEASE_RENT": "missing",
                                         "CONVEYANCE_DEED": "missing", "FIELD_INSPECTION": "missing", "PREMIUM_PAYMENT": "missing"},
                             failed=["PROPERTY_CARD_RECENCY"]))
    precs = [
        cand(mk_card("P1", reqs={"MEMBER_LIST": ("present", True), "CONVEYANCE_DEED": "missing", "NO_DUES_LEASE_RENT": "not_applicable"}), 0.9),
        cand(mk_card("P2", reqs={"MEMBER_LIST": ("present", True), "CONVEYANCE_DEED": "missing", "NO_DUES_LEASE_RENT": "not_applicable"}), 0.8),
        cand(mk_card("P3", reqs={"MEMBER_LIST": "missing", "CONVEYANCE_DEED": "present", "NO_DUES_LEASE_RENT": ("present", True)},
                     tenure="govt_lease"), 0.3, regime=("OLD",)),
    ]
    statutory_now = {"NO_DUES_LEASE_RENT": {"code": "NO_DUES_LEASE_RENT", "rule_set_code": "R19", "status": "to_verify",
                                            "mandatory": True, "applies": True, "source_ref": None}}
    gaps = analyse_gaps(q, precs, CATALOG, statutory_now, {}, ("R19",), GapSettings(threshold, 2))
    return {g["code"]: g for g in gaps}


def test_gap_support_basis_and_citations():
    g = gap_setup()
    assert set(g) == {"PROPERTY_CARD_RECENT", "MEMBER_LIST", "NO_DUES_LEASE_RENT"}
    ml = g["MEMBER_LIST"]
    assert ml["support"] == pytest.approx(1.7 / 2.0, abs=1e-3) and ml["n_applicable"] == 3 and ml["n_queried"] == 2
    assert ml["basis"] == PRACTICE and ml["reported_because"] == ["precedent_support"]
    assert [c["case_ref"] for c in ml["was_queried_in"]] == ["P1", "P2"]
    assert all(c["page_seq"] == 5 and c["exhibit_id"] == "QUERY_LETTER-1" and c["snippet"] for c in ml["was_queried_in"])
    nd = g["NO_DUES_LEASE_RENT"]
    assert nd["basis"] == STATUTORY and nd["rules"][0]["rule_set_code"] == "R19" and nd["n_applicable"] == 1
    pc = g["PROPERTY_CARD_RECENT"]
    assert pc["kind"] == "invalid" and pc["validators"][0]["code"] == "PROPERTY_CARD_RECENCY"
    assert pc["validators"][0]["evidence"][0]["page_seq"] == 2


def test_gap_threshold_and_what_is_never_a_gap():
    g = gap_setup(threshold=0.9)
    assert "MEMBER_LIST" not in g  # 0.85 < 0.9
    assert "CONVEYANCE_DEED" not in gap_setup()  # support 0.15
    assert "FIELD_INSPECTION" not in g and "PREMIUM_PAYMENT" not in g  # office process / payment before the demand


def test_old_rule_support_is_labelled():
    q = build_profile(mk_card("Q", reqs={"MEMBER_LIST": "missing"}))
    precs = [cand(mk_card(f"P{i}", reqs={"MEMBER_LIST": ("present", True)}), 1.0, regime=("OLD",)) for i in range(3)]
    old = analyse_gaps(q, precs, CATALOG, {}, {}, ("R19",), GapSettings(0.5, 2))
    assert old[0]["basis"] == PRACTICE_OLD_RULES
    sup = analyse_gaps(q, precs, CATALOG, {}, {p.profile.case_id: {"MEMBER_LIST"} for p in precs}, ("R19",), GapSettings(0.5, 2))
    assert sup[0]["basis"] == SUPERSEDED


# ------------------------------------------------------------------------------------------------
# Next steps
# ------------------------------------------------------------------------------------------------
def test_next_steps_statistics_and_triggers():
    q = build_profile(mk_card("Q", failed=["AREA_TOLERANCE"], events=[("APPLICATION_FILED", "2025-01-15"), ("QUERY_ISSUED", "2025-02-10")]))
    seqs = [("2024-01-10", "2024-02-01", "2024-03-01", "2024-04-01"), ("2024-02-10", "2024-03-01", "2024-04-15", "2024-05-01")]
    precs = [cand(mk_card(f"P{i}", app=a, events=[("APPLICATION_FILED", a), ("QUERY_ISSUED", qd), ("DEMAND_ISSUED", dd),
                                                  ("ORDER_ISSUED", od)]), 1.0) for i, (a, qd, dd, od) in enumerate(seqs)]
    obligations = [{"code": "PAY_PREMIUM_BEFORE_ORDER", "rule_set_code": "R19", "status": "to_verify", "applies": True},
                   {"code": "CLEAR_LEASE_RENT_ARREARS", "rule_set_code": "R19", "status": "to_verify", "applies": False}]
    cfg = IntelConfig.load().raw
    out = next_steps(q, precs, EVENTS, cfg["extra_steps"], obligations, {}, cfg["obligation_steps"], cfg["fact_triggers"],
                     ("R19",), 0.4)
    done = [s["code"] for s in out["done"]]
    nxt = {s["code"]: s for s in out["next"]}
    assert done == ["APPLICATION_FILED", "QUERY_ISSUED"]
    # unseen steps slot in by catalog order between steps with a precedent position
    assert list(nxt) == ["CTSO_MEASUREMENT_DONE", "LEASE_RENT_DUES_CHECK", "DEMAND_ISSUED", "PREMIUM_PAID", "ORDER_ISSUED"]
    d = nxt["DEMAND_ISSUED"]
    assert d["frequency"] == 1.0 and d["basis"] == STATUTORY and d["median_days_from_filing"] == 51
    assert d["expected_by"] == "2025-03-07" and d["citations"][0]["page_seq"] == 3
    assert nxt["PREMIUM_PAID"]["frequency"] == 0 and nxt["PREMIUM_PAID"]["triggers"][0]["kind"] == "rule"
    ctso = nxt["CTSO_MEASUREMENT_DONE"]
    assert ctso["triggers"][0]["code"] == "AREA_MISMATCH" and ctso["triggers"][0]["evidence"][0]["page_seq"] == 2
    lease = nxt["LEASE_RENT_DUES_CHECK"]  # fact trigger fires (leasehold) though the rule obligation does not apply
    assert [t["code"] for t in lease["triggers"]] == ["LEASEHOLD"] and lease["kind"] == "check"


def test_weighted_median():
    assert weighted_median([]) is None
    assert weighted_median([(10, 1), (20, 1), (30, 1)]) == 20
    assert weighted_median([(10, 5), (20, 1), (30, 1)]) == 10


# ------------------------------------------------------------------------------------------------
# Explanations
# ------------------------------------------------------------------------------------------------
def _gaps():
    g = gap_setup()
    return [g["MEMBER_LIST"], g["NO_DUES_LEASE_RENT"]]


KNOWN = {c["code"] for c in CATALOG} | {"AREA_TOLERANCE"}


def test_template_explanations_without_llm():
    gaps = _gaps()
    meta = explain(gaps, llm=None, data_class="synthetic", known_codes=KNOWN)
    assert meta["source"] == "template"
    assert gaps[0]["explanation_mr"].startswith("सभासद यादी") and "३ पैकी २" in gaps[0]["explanation_mr"]
    assert "MEMBER_LIST" in gaps[0]["explanation_en"]


def _fake(items):
    return FakeLLMClient(responder=lambda task, user, schema: {"items": items})


def test_llm_explanation_accepted_when_codes_match():
    gaps = _gaps()
    llm = _fake([{"code": "MEMBER_LIST", "text_mr": "३ पैकी २ प्रकरणांत सभासद यादी मागवली गेली."},
                 {"code": "NO_DUES_LEASE_RENT", "text_mr": "नियमानुसार थकबाकी नसल्याचा दाखला आवश्यक आहे."}])
    meta = explain(gaps, llm=llm, data_class="synthetic", known_codes=KNOWN)
    assert meta == {"source": "llm", "check": "passed"} and gaps[0]["explanation_source"] == "llm"
    assert llm.calls[0]["data_class"] == "synthetic"


@pytest.mark.parametrize("items,reason", [
    ([{"code": "NO_DUES_LEASE_RENT", "text_mr": "x"}, {"code": "MEMBER_LIST", "text_mr": "y"}], "codes differ"),
    ([{"code": "MEMBER_LIST", "text_mr": "x"}], "codes differ"),
    ([{"code": "MEMBER_LIST", "text_mr": "CONVEYANCE_DEED पण आवश्यक"}, {"code": "NO_DUES_LEASE_RENT", "text_mr": "y"}], "other codes"),
    ([{"code": "MEMBER_LIST", "text_mr": "१७ प्रकरणांत"}, {"code": "NO_DUES_LEASE_RENT", "text_mr": "y"}], "numbers"),
])
def test_llm_explanation_rejected_falls_back_to_template(items, reason):
    gaps = _gaps()
    meta = explain(gaps, llm=_fake(items), data_class="synthetic", known_codes=KNOWN)
    assert meta["source"] == "template" and reason in meta["check"]
    assert all(g["explanation_source"] == "template" for g in gaps)
    assert check(gaps, {"items": items}, KNOWN) is not None


def test_llm_refused_for_real_data_when_client_disallows_it():
    gaps = _gaps()
    meta = explain(gaps, llm=_fake([]), data_class="real", known_codes=KNOWN)
    assert meta["source"] == "template" and "llm error" in meta["check"]


# ------------------------------------------------------------------------------------------------
# Config overlay
# ------------------------------------------------------------------------------------------------
def test_tuned_file_overrides_defaults(tmp_path):
    tuned = tmp_path / "tuned.yaml"
    tuned.write_text(yaml.safe_dump({"tuned": {"eval_run_id": "x"}, "support_threshold": 0.65, "weights": {"area": 0.0, "bogus": 9}}))
    base = IntelConfig.load(tuned_path=tmp_path / "missing.yaml")
    cfg = IntelConfig.load(tuned_path=tuned)
    assert base.source() == "default" and cfg.source() == "tuned"
    assert cfg.support_threshold == 0.65 and cfg.weights["area"] == 0.0 and "bogus" not in cfg.weights
    assert cfg.weights["tenure"] == base.weights["tenure"] and cfg.fingerprint() != base.fingerprint()
    assert Decimal(str(cfg.with_overrides(support_threshold=0.3).support_threshold)) == Decimal("0.3")


def test_tuned_weights_only_apply_to_their_data_class(tmp_path):
    tuned = tmp_path / "tuned.yaml"
    tuned.write_text(yaml.safe_dump({"tuned": {"data_class": "synthetic"}, "support_threshold": 0.65}))
    assert IntelConfig.load(tuned_path=tuned, data_class="synthetic").source() == "tuned"
    real = IntelConfig.load(tuned_path=tuned, data_class="real")
    assert real.source() == "default" and real.support_threshold == 0.5
