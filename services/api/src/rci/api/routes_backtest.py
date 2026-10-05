"""Backtest (Phase 4): holdout selection, runs and reports, officer adjudication of false positives, precedent relevance
labels. Label-based metrics are re-scored with the current labels on every read."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.api.deps import get_session
from rci.auth import CurrentUser, require
from rci.backtest import gate as G
from rci.backtest import holdouts as H
from rci.backtest.harness import BacktestError, load_run, run_backtest, write_reports
from rci.backtest.report import html, markdown
from rci.db.models import Case, EvalLabel, EvalRun
from rci.drafting.service import upsert_label
from rci.pipeline.runner import audit

router = APIRouter(prefix="/backtest")
READ = require("reviewer", "officer")
LABEL = require("officer")
ADMIN = require("admin")


@router.get("/holdouts")
def holdouts(data_class: Literal["real", "synthetic"] = "real", prefix: str | None = None, session: Session = Depends(get_session),
             user: CurrentUser = Depends(READ)) -> dict:
    rows = H.holdout_cases(session, data_class, prefix)
    cfg = H.BacktestConfig.load()
    refs = [c.case_ref for c, _ in rows]
    sel = session.scalar(select(EvalRun).where(EvalRun.kind == "holdout_selection").order_by(EvalRun.created_at.desc()).limit(1))
    return {"data_class": data_class, "holdouts_hash": H.holdouts_hash(refs), "n_target": cfg.holdouts.get("n", 20),
            "holdouts": [{"case_id": str(c.id), "case_ref": c.case_ref, "society_name": c.society_name, "locked": c.role_locked,
                          "stratum": H.stratum_of(cc.card, cfg)} for c, cc in rows],
            "selection": {"id": str(sel.id), "created_at": sel.created_at, "config": sel.config} if sel else None}


class SelectBody(BaseModel):
    data_class: Literal["real", "synthetic"]
    n: int | None = Field(default=None, ge=1, le=200)
    seed: int | None = None
    prefix: str | None = None


@router.post("/holdouts/select")
def select_holdouts(body: SelectBody, session: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)) -> dict:
    return H.select_holdouts(session, data_class=body.data_class, n=body.n, seed=body.seed, prefix=body.prefix, by=user.email)


class RunBody(BaseModel):
    data_class: Literal["real", "synthetic"]
    prefix: str | None = None
    drafts: Literal["check", "store", "off"] | None = None


def _with_gate(result: dict) -> dict:
    cfg = H.BacktestConfig.load()
    return {**result, "gate": G.evaluate(result, G.load_baseline(cfg.gate), cfg.gate)}


@router.post("/runs", status_code=201)
def run(body: RunBody, session: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)) -> dict:
    try:
        result = run_backtest(session, data_class=body.data_class, prefix=body.prefix, drafts=body.drafts, by=user.email)
    except BacktestError as e:
        raise HTTPException(409, str(e))
    result = _with_gate(result)
    write_reports(session, result, None, result["gate"])
    return {k: result[k] for k in ("run_id", "created_at", "config", "metrics", "gate", "warnings", "errors")}


@router.get("/runs")
def runs(session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> list[dict]:
    rows = session.scalars(select(EvalRun).where(EvalRun.kind == "backtest").order_by(EvalRun.created_at.desc()).limit(50)).all()
    return [{"id": str(r.id), "created_at": r.created_at, "status": r.status, "data_class": r.config.get("data_class"),
             "holdouts": len(r.config.get("holdouts", [])), "holdouts_hash": r.config.get("holdouts_hash"),
             "gate": ((r.metrics or {}).get("gate") or {}).get("passed"),
             "headline": {k: (r.metrics or {}).get("metrics", {}).get(k) for k in ("query_f1", "statutory_false_negatives", "steps_f1",
                                                                               "fee_exact_match_rate", "draft_render_ok_rate")}}
            for r in rows]


def _run(session: Session, run_id: uuid.UUID) -> dict:
    try:
        return _with_gate(load_run(session, run_id))
    except BacktestError as e:
        raise HTTPException(404, str(e))


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    return _run(session, run_id)


@router.get("/runs/{run_id}/report.html", response_class=HTMLResponse)
def report_html(run_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> str:
    r = _run(session, run_id)
    return html(r, r["gate"])


@router.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
def report_md(run_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> str:
    r = _run(session, run_id)
    return markdown(r, r["gate"])


@router.get("/runs/{run_id}/adjudication")
def adjudication(run_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> list[dict]:
    """Every false positive of the run (predicted, not queried by the office) with the current verdicts."""
    r = _run(session, run_id)
    labels = {(str(lb.case_id), lb.label_key): lb for lb in session.scalars(select(EvalLabel).where(
        EvalLabel.label_kind == "fp_adjudication", EvalLabel.case_id.in_([uuid.UUID(c["case_id"]) for c in r["cases"]])))}
    out = []
    for c in r["cases"]:
        detail = {g["code"]: g for g in c.get("predicted_detail", [])}
        for code in c["metrics"]["false_positives"]:
            lb = labels.get((c["case_id"], code))
            out.append({"case_id": c["case_id"], "case_ref": c["case_ref"], "code": code, **{k: detail.get(code, {}).get(k) for k in (
                "name_mr", "name_en", "basis", "support", "n_applicable", "n_supporting", "reported_because", "query_status")},
                "office_queried": c["truth_queried"], "verdict": (lb.value or {}).get("verdict") if lb else None,
                "note": (lb.value or {}).get("note") if lb else None})
    return out


class AdjudicationBody(BaseModel):
    case_id: uuid.UUID
    code: str = Field(max_length=64)
    verdict: Literal["truly_unnecessary", "valid_not_asked"]
    note: str | None = Field(default=None, max_length=2000)
    run_id: uuid.UUID | None = None


@router.post("/adjudications")
def adjudicate(body: AdjudicationBody, session: Session = Depends(get_session), user: CurrentUser = Depends(LABEL)) -> dict:
    case = session.get(Case, body.case_id)
    if case is None or case.role != "holdout":
        raise HTTPException(422, "adjudications are recorded on holdout cases")
    row = upsert_label(session, case_id=case.id, kind="fp_adjudication", key=body.code, user_id=user.id, run_id=body.run_id,
                       value={"verdict": body.verdict, "note": body.note, "run_id": str(body.run_id) if body.run_id else None})
    audit(session, "label.fp_adjudication", actor_kind="user", case_id=case.id, entity_type="eval_label", entity_id=row.id,
          actor=str(user.id), code=body.code, verdict=body.verdict)
    return {"id": str(row.id), "case_id": str(case.id), "code": body.code, "verdict": body.verdict}


class RelevanceBody(BaseModel):
    case_id: uuid.UUID  # the holdout
    target_case_id: uuid.UUID  # the precedent shown in its top five
    relevance: int = Field(ge=0, le=2)  # 0 not relevant · 1 somewhat · 2 highly
    run_id: uuid.UUID | None = None


@router.post("/relevance")
def relevance(body: RelevanceBody, session: Session = Depends(get_session), user: CurrentUser = Depends(LABEL)) -> dict:
    case, target = session.get(Case, body.case_id), session.get(Case, body.target_case_id)
    if case is None or target is None or case.role != "holdout":
        raise HTTPException(422, "relevance labels are for a holdout and one of its precedents")
    row = upsert_label(session, case_id=case.id, kind="precedent_relevance", key=str(target.id), target_case_id=target.id,
                       user_id=user.id, run_id=body.run_id, value={"relevance": body.relevance})
    audit(session, "label.precedent_relevance", actor_kind="user", case_id=case.id, entity_type="eval_label", entity_id=row.id,
          actor=str(user.id), target=target.case_ref, relevance=body.relevance)
    return {"id": str(row.id), "relevance": body.relevance}


@router.get("/runs/{run_id}/relevance")
def relevance_items(run_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> list[dict]:
    r = _run(session, run_id)
    mine = {(str(lb.case_id), lb.label_key): lb.value.get("relevance") for lb in session.scalars(select(EvalLabel).where(
        EvalLabel.label_kind == "precedent_relevance", EvalLabel.labeller_id == user.id))}
    return [{"case_id": c["case_id"], "case_ref": c["case_ref"], "truth_queried": c["truth_queried"],
             "top5": [{**t, "relevance": mine.get((c["case_id"], t["case_id"]))} for t in c["top5"]]} for c in r["cases"]]
