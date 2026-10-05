"""Backtest on the test DB: stratified holdout selection (locked, final), the replay (filing-stage mask, time travel,
same-society and holdout exclusion, fee, all four drafts), stored runs, re-scoring with officer labels through the API,
tuning-leak detection, holdouts kept out of clause mining, and the CI gate against a baseline."""
import copy
import uuid

import pytest
import yaml
from sqlalchemy import func, select

from rci.backtest import gate as G
from rci.backtest import holdouts as H
from rci.backtest.harness import intel_config, load_run, run_backtest, write_reports
from rci.config import get_settings
from rci.db.models import Case, Clause, EvalCaseResult, EvalRun
from rci.drafting import clauses as cl
from rci.intel import synthetic

pytestmark = pytest.mark.db
PREFIX = "BT-P"
N = 24
HOLD = 8


def bcfg(tmp_path=None) -> H.BacktestConfig:
    cfg = H.BacktestConfig.load()
    raw = copy.deepcopy(cfg.raw)
    raw["gate"]["min_holdouts"] = HOLD
    if tmp_path is not None:
        raw["gate"]["baseline_file"] = str(tmp_path / "baseline.json")
    return H.BacktestConfig(raw)


@pytest.fixture(scope="module")
def world(db_url):
    from rci.db.session import session_scope

    with session_scope(db_url) as s:
        synthetic.generate_pool(s, N, seed=3, prefix=PREFIX)
    with session_scope(db_url) as s:
        sel = H.select_holdouts(s, data_class="synthetic", n=HOLD, seed=3, prefix=PREFIX)
    return sel


def test_selection_is_stratified_locked_and_final(db_session, world):
    assert world["selected"] and len(world["holdouts"]) == HOLD
    assert sum(v["selected"] for v in world["strata"].values()) == HOLD
    assert all(v["selected"] >= 1 for v in world["strata"].values())
    rows = db_session.scalars(select(Case).where(Case.case_ref.in_(world["holdouts"]))).all()
    assert all(c.role == "holdout" and c.role_locked for c in rows)
    again = H.select_holdouts(db_session, data_class="synthetic", n=HOLD, seed=99, prefix=PREFIX)
    assert not again["selected"] and again["holdouts"] == world["holdouts"] and again["holdouts_hash"] == world["holdouts_hash"]
    assert db_session.scalar(select(func.count()).select_from(EvalRun).where(EvalRun.kind == "holdout_selection")) >= 1


def test_replay_and_metrics(db_session, world, tmp_path):
    cfg = bcfg(tmp_path)
    r = run_backtest(db_session, data_class="synthetic", prefix=PREFIX, bcfg=cfg)
    db_session.commit()
    m = r["metrics"]
    assert m["n_holdouts"] == HOLD and m["errors"] == 0 and not r["errors"]
    assert m["holdout_leaks"] == 0 and m["tuning_leak"] == 0
    assert m["draft_render_ok_rate"] == 1.0 and m["leak_failures"] == 0
    assert m["fee_coverage"] == 1.0 and m["fee_exact_match_rate"] == 1.0  # synthetic fee world reproduces every premium
    assert m["query_f1"] is not None and m["query_f1"] > 0.5 and m["steps_f1"] > 0.5
    holdouts = set(world["holdouts"])
    for c in r["cases"]:
        assert not {t["case_ref"] for t in c["top5"]} & holdouts
        assert set(c["predicted"]) <= {g["code"] for g in c["predicted_detail"]}
        assert c["filing_date"] <= c["rule_date"] or c["rule_date"] == c["filing_date"]
        assert set(c["drafts"]) == {"SCRUTINY_NOTE", "QUERY_LETTER", "DEMAND_LETTER", "COLLECTOR_ORDER"}
        assert c["pool"]["role"] >= HOLD - 1  # the other holdouts were filtered out of the pool
    assert r["strata"]["tenure"] and r["strata"]["era"]
    run = db_session.get(EvalRun, uuid.UUID(r["run_id"]))
    assert run.kind == "backtest" and run.status == "done" and run.config["holdouts_hash"] == world["holdouts_hash"]
    assert db_session.scalar(select(func.count()).select_from(EvalCaseResult).where(EvalCaseResult.run_id == run.id)) == HOLD
    # gate: passes against itself, fails on a better baseline or another holdout set
    G.write_baseline(r, cfg.gate)
    assert G.evaluate(r, G.load_baseline(cfg.gate), cfg.gate)["passed"]
    better = G.load_baseline(cfg.gate)
    better["metrics"]["query_f1"] = m["query_f1"] + 0.1
    assert G.evaluate(r, better, cfg.gate)["failed"] == ["query_f1"]
    other = copy.deepcopy(r)
    other["config"]["holdouts_hash"] = "different"
    assert "baseline" in G.evaluate(other, G.load_baseline(cfg.gate), cfg.gate)["failed"]
    paths = write_reports(db_session, r, tmp_path, G.evaluate(r, G.load_baseline(cfg.gate), cfg.gate))
    assert "## Gate: PASS" in open(paths["md"], encoding="utf-8").read()
    assert run.report_blob_key and run.metrics["gate"]["passed"]


def test_tuning_leak_is_detected(world, tmp_path, monkeypatch):
    path = tmp_path / "intel_tuned.yaml"
    monkeypatch.setattr(get_settings(), "intel_tuned_file", path)
    path.write_text(yaml.safe_dump({"tuned": {"data_class": "synthetic", "cases": [world["holdouts"][0], "X"]},
                                    "support_threshold": 0.9}), encoding="utf-8")
    cfg, warnings, leak = intel_config("synthetic", set(world["holdouts"]))
    assert leak and cfg.source() == "default" and cfg.support_threshold == 0.5 and warnings
    path.write_text(yaml.safe_dump({"tuned": {"data_class": "synthetic", "cases": ["X"]}, "support_threshold": 0.9}), encoding="utf-8")
    cfg, warnings, leak = intel_config("synthetic", set(world["holdouts"]))
    assert not leak and cfg.source() == "tuned" and cfg.support_threshold == 0.9 and not warnings
    path.write_text(yaml.safe_dump({"tuned": {"data_class": "synthetic"}, "support_threshold": 0.9}), encoding="utf-8")
    cfg, warnings, leak = intel_config("synthetic", set(world["holdouts"]))
    assert not leak and cfg.source() == "default" and "do not record" in warnings[0]


def test_holdouts_never_feed_the_clause_library(db_session, world):
    cl.mine(db_session, data_class="synthetic")
    ids = {str(c.id) for c in db_session.scalars(select(Case).where(Case.case_ref.in_(world["holdouts"])))}
    for c in db_session.scalars(select(Clause).where(Clause.origin == "mined")):
        assert not set(c.source_case_ids or []) & ids
    db_session.rollback()


def test_api_runs_labels_and_rescoring(api, world, db_session):
    admin, officer, reviewer = api("admin"), api("officer"), api("reviewer1")
    assert officer.post("/backtest/runs", json={"data_class": "synthetic", "prefix": PREFIX}).status_code == 403
    r = admin.post("/backtest/runs", json={"data_class": "synthetic", "prefix": PREFIX, "drafts": "off"})
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]
    assert r.json()["gate"]["checks"] and r.json()["metrics"]["draft_render_ok_rate"] is None  # drafts off → not measured
    listing = reviewer.get("/backtest/runs").json()
    assert any(x["id"] == run_id for x in listing)
    hs = reviewer.get("/backtest/holdouts", params={"data_class": "synthetic", "prefix": PREFIX}).json()
    assert {h["case_ref"] for h in hs["holdouts"]} == set(world["holdouts"]) and all(h["locked"] for h in hs["holdouts"])
    fps = reviewer.get(f"/backtest/runs/{run_id}/adjudication").json()
    base = reviewer.get(f"/backtest/runs/{run_id}").json()["metrics"]
    assert base["adjudicated_precision"] is None and base["adjudications_pending"] == len(fps)
    if fps:
        fp = fps[0]
        assert reviewer.post("/backtest/adjudications", json={"case_id": fp["case_id"], "code": fp["code"],
                                                              "verdict": "valid_not_asked"}).status_code == 403
        ok = officer.post("/backtest/adjudications", json={"case_id": fp["case_id"], "code": fp["code"], "verdict": "valid_not_asked",
                                                           "run_id": run_id})
        assert ok.status_code == 200
        after = reviewer.get(f"/backtest/runs/{run_id}").json()["metrics"]
        assert after["adjudications_pending"] == base["adjudications_pending"] - 1
        assert after["adjudicated_precision"] > base["query_precision"]
    items = reviewer.get(f"/backtest/runs/{run_id}/relevance").json()
    first = items[0]
    for rel, t in zip((2, 0), first["top5"][:2]):
        assert officer.post("/backtest/relevance", json={"case_id": first["case_id"], "target_case_id": t["case_id"],
                                                         "relevance": rel}).status_code == 200
    labelled = reviewer.get(f"/backtest/runs/{run_id}").json()["metrics"]
    assert labelled["p_at_5"] == 0.5 and labelled["ndcg_at_5"] == 1.0 and labelled["relevance_label_coverage"] > 0
    not_holdout = db_session.scalar(select(Case).where(Case.case_ref.like(f"{PREFIX}-%"), Case.role == "precedent").limit(1))
    assert officer.post("/backtest/adjudications", json={"case_id": str(not_holdout.id), "code": "X",
                                                         "verdict": "truly_unnecessary"}).status_code == 422
    page = reviewer.get(f"/backtest/runs/{run_id}/report.html")
    assert page.status_code == 200 and "Backtest" in page.text and "Gate" in page.text
    assert reviewer.get(f"/backtest/runs/{run_id}/report.md").text.startswith("# Backtest")
    stored = load_run(db_session, uuid.UUID(run_id), rescore=False)
    assert stored["metrics"]["p_at_5"] is None  # what the run saw; the live view re-scores with the new labels
