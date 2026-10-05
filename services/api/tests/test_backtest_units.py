"""Backtest pieces without a database: metrics, stratified allocation / selection, the CI gate, the report."""
import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rci.backtest import gate as G
from rci.backtest import metrics as M
from rci.backtest.holdouts import allocate, choose, era
from rci.backtest.report import html, markdown


def test_prf_and_kendall():
    assert M.prf(3, 1, 2) == (0.75, 0.6, pytest.approx(2 * 0.75 * 0.6 / 1.35))
    assert M.prf(0, 0, 0) == (None, None, None) and M.prf(0, 2, 3)[2] == 0.0
    assert M.kendall_tau(["A", "B", "C"], ["A", "B", "C"]) == 1.0
    assert M.kendall_tau(["C", "B", "A"], ["A", "B", "C"]) == -1.0
    assert M.kendall_tau(["A", "X", "C", "B"], ["A", "B", "C", "D"]) == pytest.approx(1 / 3)  # common A,B,C: one swap of three
    assert M.kendall_tau(["A"], ["A", "B"]) is None


def test_ranking_metrics():
    assert M.p_at_k([2, 0, None, 1, 0]) == 0.5 and M.p_at_k([None] * 5) is None
    assert M.ndcg_at_k([2, 1, 0], [2, 1, 0]) == 1.0
    assert M.ndcg_at_k([0, 1, 2], [2, 1, 0]) < 1.0 and M.ndcg_at_k([0, 0], []) is None


def outputs(**kw):
    o = {"case_id": "c1", "case_ref": "H-1", "stratum": {"tenure": "govt_lease", "era": "x"},
         "predicted": ["A", "B", "C"], "truth_queried": ["A", "D"], "truth_first_letter": ["A"], "statutory_codes": ["D"],
         "steps_predicted": ["QUERY_ISSUED", "DEMAND_ISSUED", "ORDER_ISSUED"], "steps_truth": ["QUERY_ISSUED", "DEMAND_ISSUED",
                                                                                            "PREMIUM_PAID", "ORDER_ISSUED"],
         "fee": {"predicted": "100.00", "observed": "100.00"}, "top5": [{"case_id": "p1"}, {"case_id": "p2"}],
         "proxy_ndcg_at_5": 0.5, "drafts": {"Q": {"render_ok": True, "leak_ok": True, "complete": True},
                                              "D": {"render_ok": True, "leak_ok": False, "complete": False}}}
    o.update(kw)
    return o


def test_case_metrics_and_adjudication():
    m = M.case_metrics(outputs(), {"fp": {"B": "valid_not_asked"}, "relevance": {"p1": 2, "p2": 0}})
    assert (m["q_tp"], m["q_fp"], m["q_fn"]) == (1, 2, 1)
    assert m["statutory_false_negatives"] == ["D"] and m["first_letter_completeness"] == 0.5
    assert m["office_first_letter_completeness"] == 0.5 and m["complete_first_letter"] is False
    assert m["fp_valid_not_asked"] == 1 and m["fp_pending"] == 1 and m["false_positives"] == ["B", "C"]
    assert (m["s_tp"], m["s_fp"], m["s_fn"]) == (3, 0, 1) and m["kendall_tau"] == 1.0
    assert m["fee_exact"] is True and m["fee_abs_pct_error"] == 0.0
    assert m["p_at_5"] == 0.5 and m["ndcg_at_5"] == 1.0 and m["leak_failures"] == 1 and m["drafts_ok"] == 2
    agg = M.aggregate([m, M.case_metrics(outputs(case_id="c2", predicted=["A", "D"], fee={"predicted": "110", "observed": "100"}), {})])
    assert agg["n_holdouts"] == 2 and agg["query_tp"] == 3 and agg["statutory_false_negatives"] == 1
    assert agg["adjudicated_precision"] == pytest.approx((3 + 1) / 5) and agg["adjudications_pending"] == 1
    assert agg["fee_exact_match_rate"] == 0.5 and agg["fee_mean_abs_pct_error"] == 5.0 and agg["leak_failures"] == 2
    assert M.aggregate([M.case_metrics(outputs(), {})])["adjudicated_precision"] is None  # nothing adjudicated yet
    strata = M.by_stratum([outputs(), outputs(case_id="c2", stratum={"tenure": "occupancy_class_2", "era": "x"})],
                          [m, m], ["tenure"])
    assert set(strata["tenure"]) == {"govt_lease", "occupancy_class_2"}


def test_allocation_examples():
    assert allocate({"a": 20, "b": 11, "c": 19, "d": 10}, 20) == {"a": 6, "b": 4, "c": 6, "d": 4}
    assert allocate({"a": 1, "b": 50}, 5) == {"a": 1, "b": 4}  # every non-empty stratum represented
    assert allocate({"a": 2, "b": 0}, 5) == {"a": 2, "b": 0}  # never more than available
    assert sum(allocate({"a": 3, "b": 3, "c": 3}, 2).values()) == 2


@given(st.dictionaries(st.sampled_from("abcdefgh"), st.integers(min_value=0, max_value=40), min_size=1), st.integers(0, 60))
def test_allocation_properties(counts, n):
    a = allocate(counts, n)
    total = sum(counts.values())
    assert sum(a.values()) == min(n, total)
    assert all(0 <= a[k] <= counts[k] for k in counts)
    nonempty = [k for k, v in counts.items() if v]
    if n >= len(nonempty):
        assert all(a[k] >= 1 for k in nonempty)
    assert a == allocate(dict(reversed(list(counts.items()))), n)  # order-independent


def test_choose_is_reproducible_and_seeded():
    elig = [(f"C-{i:03d}", "x" if i % 3 else "y") for i in range(30)]
    a, b = choose(elig, 10, 7), choose(list(reversed(elig)), 10, 7)
    assert a == b and len(a) == 10 and choose(elig, 10, 8) != a
    assert era(None, []) == "unknown"


def result(**metrics):
    base = {"n_holdouts": 20, "query_f1": 0.8, "query_recall": 0.85, "query_precision": 0.75, "statutory_false_negatives": 0,
            "steps_f1": 0.9, "kendall_tau": 0.9, "fee_exact_match_rate": 1.0, "fee_mean_abs_pct_error": 0.0, "fee_coverage": 1.0,
            "first_letter_completeness": 0.8, "p_at_5": None, "ndcg_at_5": None, "adjudicated_precision": None, "draft_rating_mean": None,
            "leak_failures": 0, "draft_render_ok_rate": 1.0, "holdout_leaks": 0, "tuning_leak": 0, "errors": 0}
    base.update(metrics)
    return {"run_id": "r", "code_version": "c", "config": {"data_class": "synthetic", "holdouts": ["H-1"], "holdouts_hash": "h1",
                                                           "intel": {"source": "default", "fingerprint": "f"}, "fee_world": "synthetic_5pct",
                                                           "drafts": "check"}, "metrics": base, "strata": {},
            "cases": [], "warnings": [], "errors": []}


GATE = {"min_holdouts": 20, "fail_on_tuning_leak": True,
        "absolute": {"leak_failures": {"max": 0}, "draft_render_ok_rate": {"min": 1.0}, "holdout_leaks": {"max": 0}},
        "regression": {"query_f1": {"direction": "higher", "tolerance": 0.02}, "statutory_false_negatives": {"direction": "lower", "tolerance": 0},
                       "fee_mean_abs_pct_error": {"direction": "lower", "tolerance": 0.5}, "p_at_5": {"direction": "higher", "tolerance": 0.05}}}


def test_gate(tmp_path):
    cfg = {**GATE, "baseline_file": str(tmp_path / "baseline.json")}
    r = result()
    assert not G.evaluate(r, None, cfg)["passed"]  # no baseline → fail, never a silent pass
    G.write_baseline(r, cfg)
    base = G.load_baseline(cfg)
    ok = G.evaluate(r, base, cfg)
    assert ok["passed"] and {c["check"]: c["status"] for c in ok["checks"]}["p_at_5"] == "skipped"
    assert G.evaluate(result(query_f1=0.79), base, cfg)["passed"]  # within tolerance
    worse = G.evaluate(result(query_f1=0.7, statutory_false_negatives=1), base, cfg)
    assert not worse["passed"] and set(worse["failed"]) == {"query_f1", "statutory_false_negatives"}
    assert G.evaluate(result(fee_mean_abs_pct_error=0.4), base, cfg)["passed"]
    assert G.evaluate(result(leak_failures=1), base, cfg)["failed"] == ["leak_failures"]
    assert G.evaluate(result(tuning_leak=1), base, cfg)["failed"] == ["tuning_leak"]
    assert G.evaluate(result(n_holdouts=19), base, cfg)["failed"] == ["min_holdouts"]
    other = copy.deepcopy(r)
    other["config"]["holdouts_hash"] = "h2"
    assert G.evaluate(other, base, cfg)["failed"] == ["baseline"]


def test_report_renders():
    r = result()
    r["cases"] = [{**outputs(), "metrics": M.case_metrics(outputs(), {})}]
    r["strata"] = M.by_stratum([outputs()], [r["cases"][0]["metrics"]], ["tenure"])
    g = G.evaluate(r, None, GATE)
    md, page = markdown(r, g), html(r, g)
    assert "## Gate: FAIL" in md and "H-1" in md and "| govt_lease |" in md
    assert page.startswith("<!doctype html>") and "H-1" in page and "<script" not in page
