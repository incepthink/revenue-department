"""Case analysis (Phase 3): matched precedents, cited gaps, next steps and the fee trace; fee golden check and
leave-one-out tuning reports."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.api.deps import get_session
from rci.auth import CurrentUser, require
from rci.db.models import Case, CaseAnalysis, EvalRun
from rci.fees import golden
from rci.intel import service
from rci.intel.tuning import report_markdown as tuning_markdown

router = APIRouter()
READ = require("reviewer", "officer")


def _case(session: Session, case_id: uuid.UUID) -> Case:
    c = session.get(Case, case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    return c


class AnalyseBody(BaseModel):
    as_of: dt.date | None = None  # time-travel date for the precedent pool (default: today)
    explain_llm: bool | None = None  # override config: Marathi explanations by the LLM (post-checked) or template


@router.post("/cases/{case_id}/analyse")
def analyse(case_id: uuid.UUID, body: AnalyseBody | None = None, session: Session = Depends(get_session),
            user: CurrentUser = Depends(READ)) -> dict:
    body = body or AnalyseBody()
    try:
        return service.analyse_case(session, _case(session, case_id), as_of=body.as_of, user_id=user.id, explain_llm=body.explain_llm)
    except service.AnalysisError as e:
        raise HTTPException(409, str(e))


@router.get("/cases/{case_id}/analysis")
def latest(case_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    row = service.latest_analysis(session, _case(session, case_id).id)
    if row is None:
        raise HTTPException(404, "not analysed yet")
    return row.result


@router.get("/cases/{case_id}/analyses")
def history(case_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> list[dict]:
    rows = session.scalars(select(CaseAnalysis).where(CaseAnalysis.case_id == case_id).order_by(CaseAnalysis.created_at.desc()).limit(50))
    return [{"id": str(r.id), "created_at": r.created_at, "as_of": r.as_of, "rule_date": r.rule_date, "engine_version": r.engine_version,
             "config_hash": r.config_hash, "precedents": len(r.result.get("precedents", [])), "gaps": len(r.result.get("gaps", [])),
             "fee_status": r.result.get("fee", {}).get("status"), "fee_total": r.result.get("fee", {}).get("total")} for r in rows]


# ------------------------------------------------------------------------------------------------
# Reports
# ------------------------------------------------------------------------------------------------
@router.get("/reports/fee-golden")
def fee_golden(session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    return golden.precedent_report(session)


@router.get("/reports/fee-golden.md", response_class=PlainTextResponse)
def fee_golden_md(session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> str:
    return golden.report_markdown(golden.precedent_report(session))


@router.get("/reports/tuning")
def tuning(session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    run = session.scalar(select(EvalRun).where(EvalRun.kind == "loo_tuning").order_by(EvalRun.created_at.desc()).limit(1))
    if run is None:
        raise HTTPException(404, "no tuning run yet — run `rci tune --save`")
    return {"id": str(run.id), "created_at": run.created_at, "config": run.config, **run.metrics,
            "markdown": tuning_markdown(run.metrics)}
