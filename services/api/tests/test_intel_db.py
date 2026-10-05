"""Case analysis end-to-end on the test DB: a synthetic precedent pool, sample docket DKT-002 ingested as an incoming
case → precedents, gaps, next steps and fee trace, every citation resolving to a stored page; hard filters (same
society, holdout, time travel, data class); stored analysis + fee rows; API roles; fee golden check against the
precedent challans; leave-one-out tuning (never holdouts)."""
import datetime as dt
from decimal import Decimal

import pytest
import yaml
from sqlalchemy import select

from conftest import ingest_case
from rci.config import get_settings
from rci.db.models import AuditLog, Case, CaseAnalysis, CaseCard, EvalRun, FeeCalculation, Page
from rci.fees import golden
from rci.fees.prepare import AsrRow, FeeParams, calculate
from rci.intel import service, synthetic, tuning
from rci.intel.config import IntelConfig
from rci.intel.synthetic import VILLAGES, asr_rate
from rci.rules.resolve import RuleSetRow, SlabRow, build_stack

pytestmark = pytest.mark.db
PREFIX = "TST-P"
N = 16


@pytest.fixture(scope="module")
def world(db_url):
    from rci.db.session import session_scope

    with session_scope(db_url) as s:
        out = synthetic.generate_pool(s, N, seed=11, prefix=PREFIX)
    assert len(out["created"]) + len(out["skipped"]) == N
    from rci.db.session import session_factory

    s = session_factory(db_url)()
    incoming = ingest_case(s, "INC-DKT-002", "DKT-002")
    s.close()
    return {"incoming_id": incoming.id}


@pytest.fixture
def incoming(world, db_session):
    return db_session.get(Case, world["incoming_id"])


def cites_of(result: dict) -> list[dict]:
    out = [p["link"] for p in result["precedents"] if p["link"]]
    for g in result["gaps"]:
        out += g["citations"] + g["was_queried_in"] + g["query_evidence"] + [e for v in g["validators"] for e in v["evidence"]]
    for st in result["steps"]["next"] + result["steps"]["done"]:
        out += st["citations"] + [e for t in st["triggers"] for e in t.get("evidence", [])]
        if st.get("own_evidence"):
            out.append(st["own_evidence"])
    for t in result["fee"]["trace"]:
        out += [x for x in t["sources"] if x.get("kind") == "evidence"]
    return out


def test_analyse_incoming_docket(db_session, incoming):
    s = db_session
    r = service.analyse_case(s, incoming, explain_llm=False)
    s.commit()
    # 1. precedents: ranked, explained, time-travelled, not the same society
    assert r["pool"]["pool"] >= N - 1 and r["precedents"]
    top = r["precedents"][0]
    assert top["score"] >= r["precedents"][-1]["score"] and top["reasons"] and set(top["features"]) >= {"sim_facts", "tenure", "gaps"}
    assert all(p["case_ref"] != f"{PREFIX}-003" for p in r["precedents"])  # same society as DKT-002
    assert r["pool"]["same_society"] >= 1 and r["engine"]["embedding_model"] == "local-hash-v1"
    # 2. gaps: labelled, supported, cited
    codes = {g["code"]: g for g in r["gaps"]}
    assert {"PROPERTY_CARD_RECENT", "SGM_RESOLUTION", "NO_DUES_LEASE_RENT"} <= set(codes)
    assert codes["PROPERTY_CARD_RECENT"]["kind"] == "invalid" and codes["PROPERTY_CARD_RECENT"]["validators"]
    assert all(g["basis"] in ("STATUTORY", "PRACTICE", "PRACTICE_OLD_RULES", "SUPERSEDED") for g in r["gaps"])
    assert all(g["explanation_mr"] and g["explanation_en"] for g in r["gaps"]) and r["explanations"]["source"] == "template"
    assert any(g["was_queried_in"] for g in r["gaps"])
    assert "FIELD_INSPECTION" not in codes and "PREMIUM_PAYMENT" not in codes
    # 3. next steps: DKT-002 has a query letter → the reply, demand, payment and order are next; lease → dues check
    nxt = [st["code"] for st in r["steps"]["next"]]
    assert {"QUERY_REPLIED", "DEMAND_ISSUED", "PREMIUM_PAID", "ORDER_ISSUED", "LEASE_RENT_DUES_CHECK"} <= set(nxt)
    assert nxt.index("DEMAND_ISSUED") < nxt.index("PREMIUM_PAID") < nxt.index("ORDER_ISSUED")
    assert "QUERY_ISSUED" in [st["code"] for st in r["steps"]["done"]]
    # 4. fee trace: blocked (no verified rate, no ASR) but every step it could do is shown
    fee = r["fee"]
    assert fee["status"] == "blocked" and fee["total"] is None and fee["engine_version"].startswith("fee.1.0+")
    assert any("rate not filled in" in b for b in fee["blockers"])
    assert {"inputs.rule_date", "rules.stack", "rules.slab", "inputs.area", "inputs.asr_year"} <= {t["step"] for t in fee["trace"]}
    # every citation opens a stored page of the right case, showing the cited snippet
    cites = cites_of(r)
    assert len(cites) > 20
    for c in cites:
        page = s.scalar(select(Page).where(Page.case_id == c["case_id"], Page.seq == c["page_seq"]))
        assert page is not None, c
        if c.get("snippet") and c["case_id"] != str(incoming.id):
            assert c["snippet"].split(" : ")[-1][:20] in page.text_redacted
    # stored
    row = s.get(CaseAnalysis, r["analysis_id"])
    fee_row = s.get(FeeCalculation, row.fee_calculation_id)
    assert row.result["gaps"] == r["gaps"] and row.engine_version == "intel.1.0" and row.config_hash == r["engine"]["config_hash"]
    assert fee_row.status == "blocked" and fee_row.depends_on_unverified and fee_row.trace and fee_row.issues["blockers"]
    assert s.scalar(select(AuditLog).where(AuditLog.action == "case.analyse", AuditLog.case_id == incoming.id))


def test_hard_filters(db_session, incoming):
    s = db_session
    p1 = s.scalar(select(Case).where(Case.case_ref == f"{PREFIX}-001"))
    p1.role = "holdout"
    s.flush()
    try:
        r = service.analyse_case(s, incoming, store=False, explain_llm=False)
        assert all(p["case_ref"] != p1.case_ref for p in r["precedents"]) and r["pool"]["role"] >= 1
        early = service.analyse_case(s, incoming, as_of=dt.date(2019, 1, 1), store=False, explain_llm=False)
        assert early["pool"]["pool"] == 0 and not early["precedents"] and early["pool"]["after_as_of"] >= N - 1
        assert all(set(g["reported_because"]) <= {"statutory", "validator"} for g in early["gaps"])
        assert early["warnings"]
        incoming.data_class = "real"
        s.flush()
        real = service.analyse_case(s, incoming, store=False, explain_llm=False)
        assert real["pool"]["pool"] == 0 and real["pool"]["data_class"] >= N - 1
    finally:
        s.rollback()


def test_as_of_limits_the_pool_to_earlier_decisions(db_session, incoming):
    s = db_session
    cut = dt.date(2023, 1, 1)
    r = service.analyse_case(s, incoming, as_of=cut, store=False, explain_llm=False)
    assert r["precedents"] and all(dt.date.fromisoformat(p["as_of"]) <= cut for p in r["precedents"])
    s.rollback()


def test_precedent_challans_match_with_a_verified_rule(db_session, world):
    """Golden check against precedent challans: with the synthetic 5 % slab verified and the synthetic ASR rates,
    the engine reproduces every challan of the pool to the paisa."""
    s = db_session
    sets = [RuleSetRow("SYN", "base", dt.date(2019, 1, 1), status="verified")]
    slabs = [SlabRow("SYN", "CHS", h, "residential", Decimal("5"), "asr_land_value", status="verified")
             for h in ("occupancy_class_2", "govt_lease")]
    asr = [AsrRow(y, d, v, asr_rate(r, y), status="verified") for v, _, d, r in VILLAGES for y in range(2018, 2027)]
    params = FeeParams(statuses={k: "verified" for k in ("rule_date_basis", "asr_year_basis", "asr_rate_column", "area_basis",
                                                         "rounding", "default_land_use", "premium_heads")})
    checked = 0
    for cc in s.scalars(select(CaseCard).join(Case).where(Case.case_ref.like(f"{PREFIX}-%"), CaseCard.status == "approved")):
        observed, source, _ = golden.observed_premium(cc.card, params)
        if source != "challan":
            continue
        r = calculate(cc.card, params, lambda on: build_stack(on, sets, slabs), asr, today=dt.date(2026, 9, 1))
        assert r.status == "computed" and Decimal(r.total) == observed, (cc.card["case"]["case_ref"], r.total, observed)
        checked += 1
    assert checked >= N // 2


def test_fee_golden_report_classifies_every_case(db_session, world):
    r = golden.precedent_report(db_session)
    assert r["checked"] >= N // 2 and r["unexplained"] == 0
    assert r["by_class"].get("unverified_rule_rate_missing", 0) >= 1
    assert "unexplained: 0" in golden.report_markdown(r)


def test_filing_card_masks_to_filing_stage(db_session, world):
    s = db_session
    case = s.scalar(select(Case).where(Case.case_ref == f"{PREFIX}-002"))
    card = s.scalar(select(CaseCard).where(CaseCard.case_id == case.id)).card
    masked = tuning.filing_card(s, case, card)
    assert {e["stage"] for e in masked["exhibits"]} == {"filing"} and not masked["facts"]["queries"]
    assert all(r["was_queried_in"] is None for r in masked["requirements"])
    assert any(r["was_queried_in"] for r in card["requirements"])


def test_loo_tuning_never_uses_holdouts(db_session, world, tmp_path, monkeypatch):
    s = db_session
    held = s.scalar(select(Case).where(Case.case_ref == f"{PREFIX}-016"))
    held.role = "holdout"
    s.flush()
    monkeypatch.setattr(get_settings(), "intel_tuned_file", tmp_path / "intel_tuned.yaml")
    try:
        r = tuning.tune(s, data_class="synthetic", passes=1)
        assert r["n_queries"] >= 5 and held.case_ref not in r["cases"]
        assert r["tuned"]["metrics"]["f1"] >= r["default"]["metrics"]["f1"]
        run = tuning.save(s, r, by="test")
        saved = yaml.safe_load((tmp_path / "intel_tuned.yaml").read_text(encoding="utf-8"))
        assert saved["tuned"]["eval_run_id"] == str(run.id) and s.get(EvalRun, run.id).kind == "loo_tuning"
        cfg = IntelConfig.load(tuned_path=tmp_path / "intel_tuned.yaml")
        assert cfg.source() == "tuned" and cfg.support_threshold == r["tuned"]["support_threshold"]
        leaky = IntelConfig.load(tuned_path=tmp_path / "none.yaml")
        leaky.raw["filters"]["roles"] = ["precedent", "holdout"]
        with pytest.raises(tuning.HoldoutLeak):
            tuning.precedents(s, leaky)
    finally:
        s.rollback()


def test_api_analyse(api, world, db_session):
    officer, reviewer, anon = api("officer"), api("reviewer1"), api(None)
    cid = str(world["incoming_id"])
    assert anon.post(f"/cases/{cid}/analyse").status_code == 401
    r = officer.post(f"/cases/{cid}/analyse", json={"explain_llm": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["analysis_id"] and body["precedents"] and body["gaps"] and body["steps"]["next"] and body["fee"]["trace"]
    got = reviewer.get(f"/cases/{cid}/analysis")
    assert got.status_code == 200 and got.json()["analysis_id"] == body["analysis_id"]
    hist = reviewer.get(f"/cases/{cid}/analyses").json()
    assert hist[0]["id"] == body["analysis_id"] and hist[0]["fee_status"] == "blocked"
    old = officer.post(f"/cases/{cid}/analyse", json={"as_of": "2019-01-01", "explain_llm": False}).json()
    assert old["pool"]["pool"] == 0
    empty = officer.post("/cases", json={"case_ref": "NO-CARD-1", "data_class": "synthetic"}).json()
    assert officer.post(f"/cases/{empty['id']}/analyse").status_code == 409
    assert officer.get(f"/cases/{empty['id']}/analysis").status_code == 404
    rep = reviewer.get("/reports/fee-golden").json()
    assert rep["unexplained"] == 0
    assert reviewer.get("/reports/fee-golden.md").text.startswith("# Fee golden check")
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action == "http.post", AuditLog.case_id == world["incoming_id"])).all()
    assert any(a.details["route"] == "/cases/{case_id}/analyse" for a in rows)
