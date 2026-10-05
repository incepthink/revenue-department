"""Intake of a newly filed case: start the run, poll its progress, read the results (verdict, findings, per-document
scrutiny). The run happens after the response — a background task in inline mode, the worker in queue mode."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.adapters.llm import get_llm_client
from rci.api.deps import get_session
from rci.auth import CurrentUser, require
from rci.config import get_settings
from rci.db.models import Case, Document
from rci.intake.service import RUNNING, intake_view, queue_intake, run_intake
from rci.intake.slots import slot_catalog
from rci.storage import get_blob_store

router = APIRouter()
READ = require("reviewer", "officer")


def _case(session: Session, case_id: uuid.UUID) -> Case:
    c = session.get(Case, case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    return c


def _llm() -> tuple[str, bool]:
    llm = get_llm_client()
    return llm.provider, llm.available


async def _enqueue(case_id: str) -> str:
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        job = await pool.enqueue_job("intake_job", case_id, _job_id=f"intake:{case_id}")
        return job.job_id if job else "already-queued"
    finally:
        await pool.aclose()


@router.get("/intake/slots")
def slots(user: CurrentUser = Depends(READ)) -> list[dict]:
    return slot_catalog()


@router.post("/cases/{case_id}/intake", status_code=202)
def start(case_id: uuid.UUID, background: BackgroundTasks, restart: bool = False, session: Session = Depends(get_session),
          user: CurrentUser = Depends(READ)) -> dict:
    """`restart` re-queues a run that looks stuck (e.g. the API restarted mid-run); runs are serialised per case anyway."""
    case = _case(session, case_id)
    if case.status in RUNNING and not restart:
        raise HTTPException(409, "the intake for this case is already running")
    if not session.scalar(select(Document.id).where(Document.case_id == case.id, Document.slot.is_not(None)).limit(1)):
        raise HTTPException(422, "no documents were uploaded for the intake slots")
    queue_intake(session, case)
    session.commit()  # the run reads the case in its own sessions
    if get_settings().pipeline_mode == "queue":
        return {"mode": "queue", "job_id": asyncio.run(_enqueue(str(case.id)))}
    background.add_task(run_intake, case.id)
    return {"mode": "background"}


@router.get("/cases/{case_id}/intake/status")
def status(case_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    return intake_view(session, _case(session, case_id), *_llm(), full=False)


@router.get("/cases/{case_id}/intake")
def results(case_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    return intake_view(session, _case(session, case_id), *_llm(), blobs=get_blob_store())
