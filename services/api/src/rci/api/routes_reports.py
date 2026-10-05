"""Reports: field-level extraction accuracy (machine draft vs approved) and double-review agreement."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.api.deps import get_session
from rci.auth import CurrentUser, require
from rci.db.models import EvalRun
from rci.review import service

router = APIRouter(prefix="/reports")
READ = require("reviewer", "officer")
Kind = Literal["extraction_accuracy", "agreement"]


def _compute(session: Session, kind: str) -> dict:
    return service.extraction_accuracy(session) if kind == "extraction_accuracy" else service.agreement_report(session)


@router.get("/extraction-accuracy")
def extraction_accuracy(session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    return service.extraction_accuracy(session)


@router.get("/agreement")
def agreement(session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    return service.agreement_report(session)


@router.get("/{kind}.md", response_class=PlainTextResponse)
def markdown(kind: Kind, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> str:
    return service.report_markdown(kind, _compute(session, kind))


@router.post("/{kind}/snapshot", status_code=201)
def snapshot(kind: Kind, session: Session = Depends(get_session), user: CurrentUser = Depends(require("reviewer"))) -> dict:
    run = service.save_report(session, kind, _compute(session, kind), {"by": user.email})
    return {"id": str(run.id), "kind": run.kind, "created_at": run.created_at}


@router.get("/{kind}/history")
def history(kind: Kind, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> list[dict]:
    runs = session.scalars(select(EvalRun).where(EvalRun.kind == kind).order_by(EvalRun.created_at.desc()).limit(50)).all()
    key = "overall" if kind == "extraction_accuracy" else None
    return [{"id": str(r.id), "created_at": r.created_at, "config": r.config,
             "headline": (r.metrics.get(key) if key else {k: r.metrics.get(k) for k in ("cases", "segmentation", "fields",
                                                                                          "requirements_kappa")})} for r in runs]
