"""Arq worker: runs the ingestion pipeline for cases enqueued by the API (RCI_PIPELINE_MODE=queue).

    cd services/worker && arq worker.WorkerSettings
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from arq.connections import RedisSettings

from rci.config import get_settings
from rci.db.session import session_scope
from rci.intake.service import run_intake
from rci.pipeline.runner import PipelineContext, process_case

log = logging.getLogger("rci.worker")


def _run(case_id: str, force: bool) -> dict:
    with session_scope() as s:
        summary = process_case(s, uuid.UUID(case_id), PipelineContext.default(), force=force, actor_kind="worker")
    return summary.__dict__


async def process_case_job(ctx, case_id: str, force: bool = False) -> dict:
    # The pipeline is synchronous (PyMuPDF, SQLAlchemy); keep the event loop free for arq heartbeats.
    return await asyncio.to_thread(_run, case_id, force)


async def intake_job(ctx, case_id: str) -> dict | None:
    """Intake of a newly filed case (POST /cases/{id}/intake): read, scrutinise each document, report, assemble."""
    return await asyncio.to_thread(run_intake, uuid.UUID(case_id), actor_kind="worker")


class WorkerSettings:
    functions = [process_case_job, intake_job]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 2
    job_timeout = 3600
    keep_result = 24 * 3600
