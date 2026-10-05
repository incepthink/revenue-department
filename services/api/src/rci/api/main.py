"""HTTP API.

Phase 1: create cases, upload documents, read cases/cards. Phase 2: auth + roles, audit middleware, review
workflow, page viewer (masked images, redacted OCR overlay), rules / ASR admin, reports. Phase 3: case analysis
(precedents, gaps, next steps, fee trace), fee golden check and tuning reports. Phase 4: Marathi drafts (internal / issue
renders, officer uploads, ratings), clause library curation, the holdout backtest with adjudication and relevance labels.

There is no login: requests without a bearer token act as the built-in operator user (see rci.auth). Page images contain PII and are
served PII-masked by default; unmasked images need reviewer/admin and every image request is audited.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
import uuid
import zipfile
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from rci.api.deps import get_session
from rci.auth import CurrentUser, require
from rci.config import get_settings
from rci.db.models import AuditLog, Case, CaseCard, CaseEvent, CaseRequirement, Document, Exhibit, Office, Page, ReviewAssignment
from rci.db.session import session_factory
from rci.intake.slots import AUTO as INTAKE_AUTO, BY_KEY as INTAKE_SLOTS
from rci.pipeline.runner import SUPPORTED, PipelineContext, add_document, get_or_create_case, latest_card, process_case
from rci.review.service import Conflict
from rci.review.state import ReviewError
from rci.rules import RuleError
from rci.storage import get_blob_store

log = logging.getLogger(__name__)

app = FastAPI(title="Revenue Case Intelligence API", version="0.4.0")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"])

ANY = require("reviewer", "officer")  # admin always passes


# ------------------------------------------------------------------------------------------------
# Errors
# ------------------------------------------------------------------------------------------------
@app.exception_handler(ReviewError)
async def _review_error(request: Request, exc: ReviewError):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(Conflict)
async def _conflict(request: Request, exc: Conflict):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(PermissionError)
async def _forbidden(request: Request, exc: PermissionError):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(RuleError)
async def _rule_error(request: Request, exc: RuleError):
    return JSONResponse({"detail": str(exc), "kind": type(exc).__name__}, status_code=422)


# ------------------------------------------------------------------------------------------------
# Audit middleware: every mutating request, every sensitive read, every auth failure.
# ------------------------------------------------------------------------------------------------
SENSITIVE_READS = ("/cases/{case_id}/pages/{seq}/image", "/cases/{case_id}/pages/{seq}/ocr", "/cases/{case_id}/pages/{seq}",
                   "/cases/{case_id}/card", "/cases/{case_id}/review", "/admin/rule-sets/{rule_set_id}/source",
                   "/cases/{case_id}/analysis", "/drafts/{draft_id}", "/drafts/{draft_id}/download", "/cases/{case_id}/drafts",
                   "/cases/{case_id}/intake")
UNAUDITED = ("/health", "/auth/login")  # login audits itself (with the outcome, without the password)


def _audit_row(request: Request, status: int, elapsed_ms: int) -> AuditLog | None:
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    if path in UNAUDITED or request.method == "OPTIONS":
        return None
    mutating = request.method not in ("GET", "HEAD")
    if not (mutating or path in SENSITIVE_READS or status in (401, 403)):
        return None
    user: CurrentUser | None = getattr(request.state, "user", None)
    params = request.scope.get("path_params") or {}
    case_id = params.get("case_id")
    try:
        case_uuid = uuid.UUID(str(case_id)) if case_id else None
    except ValueError:
        case_uuid = None
    action = "auth.denied" if status in (401, 403) else f"http.{request.method.lower()}"
    return AuditLog(
        actor_id=user.id if user else None, actor_kind="user" if user else "anonymous", action=action,
        entity_type="route", entity_id=path[:64], case_id=case_uuid, ip=request.client.host if request.client else None,
        details={"method": request.method, "path": request.url.path, "route": path, "status": status, "ms": elapsed_ms,
                 "query": dict(request.query_params), "role": user.role if user else None,
                 # set by the web proxy; informational only (the socket address above is the trusted one)
                 "forwarded_for": request.headers.get("x-forwarded-for")},
    )


def _write_audit(row: AuditLog) -> None:
    s = session_factory()()
    try:
        s.add(row)
        s.commit()
    except Exception:  # auditing must never break the response, but it must be loud
        log.exception("audit write failed")
        s.rollback()
    finally:
        s.close()


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    row = _audit_row(request, response.status_code, int((time.perf_counter() - t0) * 1000))
    if row is not None:
        await run_in_threadpool(_write_audit, row)
    return response


# ------------------------------------------------------------------------------------------------
# Cases & documents (Phase 1 routes, now authenticated)
# ------------------------------------------------------------------------------------------------
class CaseCreate(BaseModel):
    case_ref: str = Field(min_length=1, max_length=128)
    data_class: Literal["real", "synthetic"]
    office_code: str = "PILOT"
    title: str | None = None


def _case_out(c: Case, card: CaseCard | None = None, review: list[ReviewAssignment] | None = None) -> dict:
    out = {
        "id": str(c.id), "case_ref": c.case_ref, "title": c.title, "data_class": c.data_class, "status": c.status,
        "role": c.role, "society_name": c.society_name, "filed_on": c.filed_on, "decided_on": c.decided_on,
        "last_error": c.last_error, "created_at": c.created_at,
    }
    if card is not None or review is not None:
        out["card_version"] = card.version if card else None
        out["card_status"] = card.status if card else None
        out["review"] = [{"mode": a.mode, "status": a.status, "round": a.round, "reviewer_id": str(a.reviewer_id)} for a in review or []]
    return out


def _get_case(session: Session, case_id: uuid.UUID) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    return case


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "pipeline_mode": s.pipeline_mode, "llm_provider": s.llm_provider, "ocr_primary": s.ocr_primary,
            "embedding_provider": s.embedding_provider}


@app.post("/cases", status_code=201)
def create_case(body: CaseCreate, session: Session = Depends(get_session), user: CurrentUser = Depends(ANY)) -> dict:
    try:
        case, created = get_or_create_case(session, case_ref=body.case_ref, data_class=body.data_class,
                                           office_code=body.office_code, title=body.title, actor_kind="api")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not created:
        raise HTTPException(409, f"case {body.case_ref} already exists: {case.id}")
    return _case_out(case)


@app.get("/cases")
def list_cases(session: Session = Depends(get_session), user: CurrentUser = Depends(ANY)) -> list[dict]:
    cases = session.scalars(select(Case).order_by(Case.created_at.desc())).all()
    out = []
    for c in cases:
        card = latest_card(session, c.id)
        rnd = session.scalars(select(ReviewAssignment).where(ReviewAssignment.case_id == c.id)
                              .order_by(ReviewAssignment.round.desc(), ReviewAssignment.created_at)).all()
        current = [a for a in rnd if rnd and a.round == rnd[0].round]
        out.append(_case_out(c, card, current))
    return out


def _expand_upload(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    if filename.lower().endswith(".zip"):
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in sorted(z.infolist(), key=lambda i: i.filename):
                if not info.is_dir() and any(info.filename.lower().endswith(ext) for ext in SUPPORTED):
                    out.append((info.filename, z.read(info)))
        return out
    return [(filename, data)]


async def _enqueue(case_id: str, force: bool) -> str:
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        job = await pool.enqueue_job("process_case_job", case_id, force)
        return job.job_id if job else "already-queued"
    finally:
        await pool.aclose()


def _dispatch(session: Session, case: Case, force: bool) -> dict:
    if get_settings().pipeline_mode == "queue":
        session.commit()  # the worker must see the new documents
        return {"mode": "queue", "job_id": asyncio.run(_enqueue(str(case.id), force))}
    summary = process_case(session, case.id, PipelineContext.default(), force=force, actor_kind="api")
    return {"mode": "inline", "summary": summary.__dict__}


@app.post("/cases/{case_id}/documents", status_code=201)
def upload_documents(
    case_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    slots: list[str] | None = Form(None, description="intake slot of each file, in the same order (rci.intake.slots); \"auto\" = let the intake identify it"),
    process: bool = Query(True, description="run the pipeline after upload"),
    session: Session = Depends(get_session),
    user: CurrentUser = Depends(ANY),
) -> dict:
    case = _get_case(session, case_id)
    if slots is not None:
        if len(slots) != len(files):
            raise HTTPException(422, f"{len(slots)} slot(s) for {len(files)} file(s)")
        unknown = sorted(set(slots) - set(INTAKE_SLOTS) - {INTAKE_AUTO})
        if unknown:
            raise HTTPException(422, f"unknown slot(s): {', '.join(unknown)}")
    blobs = get_blob_store()
    docs = []
    for i, f in enumerate(files):
        slot = slots[i] if slots else None
        for name, data in _expand_upload(f.filename or "upload.pdf", f.file.read()):
            try:
                doc, created = add_document(session, blobs, case, name, data, slot=slot, actor_kind="api")
            except ValueError as e:
                raise HTTPException(415, str(e))
            docs.append({"id": str(doc.id), "filename": doc.filename, "sha256": doc.sha256, "slot": doc.slot, "created": created})
    result = {"documents": docs}
    if process:
        result["processing"] = _dispatch(session, case, force=False)
    return result


@app.post("/cases/{case_id}/process")
def run_pipeline(case_id: uuid.UUID, force: bool = False, session: Session = Depends(get_session),
                 user: CurrentUser = Depends(ANY)) -> dict:
    return _dispatch(session, _get_case(session, case_id), force)


@app.get("/cases/{case_id}")
def get_case(case_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(ANY)) -> dict:
    case = _get_case(session, case_id)
    card = latest_card(session, case.id)
    office = session.get(Office, case.office_id)
    docs = session.scalars(select(Document).where(Document.case_id == case.id).order_by(Document.seq)).all()
    exhibits = session.scalars(select(Exhibit).where(Exhibit.case_id == case.id).order_by(Exhibit.seq)).all()
    reqs = session.scalars(select(CaseRequirement).where(CaseRequirement.card_id == card.id)).all() if card else []
    events = session.scalars(select(CaseEvent).where(CaseEvent.card_id == card.id).order_by(CaseEvent.seq)).all() if card else []
    versions = session.scalars(select(CaseCard).where(CaseCard.case_id == case.id).order_by(CaseCard.version)).all()
    doc_pages: dict[uuid.UUID, list[dict]] = {}
    for p in session.scalars(select(Page).where(Page.case_id == case.id, Page.seq.is_not(None)).order_by(Page.seq)):
        doc_pages.setdefault(p.document_id, []).append(
            {"seq": p.seq, "page_no": p.page_no, "ocr_status": p.ocr_status, "ocr_engine": p.ocr_engine,
             "ocr_confidence": p.ocr_confidence, "width_px": p.width_px, "height_px": p.height_px})
    return {
        **_case_out(case),
        "office_code": office.code,
        "documents": [{"id": str(d.id), "seq": d.seq, "filename": d.filename, "sha256": d.sha256, "pages": d.page_count,
                       "status": d.status, "error": d.error, "page_list": doc_pages.get(d.id, [])} for d in docs],
        "exhibits": [{"id": str(e.id), "seq": e.seq, "type": e.exhibit_type, "stage": e.stage,
                      "pages": [e.start_page_seq, e.end_page_seq], "confidence": e.confidence,
                      "method": e.classification_method, "doc_date": e.doc_date, "origin": e.origin, "status": e.status}
                     for e in exhibits],
        "card": {"id": str(card.id), "version": card.version, "status": card.status, "locked": card.locked,
                 "approved_at": card.approved_at, "card": card.card} if card else None,
        "card_versions": [{"version": v.version, "status": v.status, "created_at": v.created_at, "approved_at": v.approved_at}
                          for v in versions],
        "requirements": [{"code": r.requirement_code, "status": r.status, "origin": r.origin,
                          "satisfied_by_exhibit_id": str(r.satisfied_by_exhibit_id) if r.satisfied_by_exhibit_id else None,
                          "was_queried_in": str(r.was_queried_in) if r.was_queried_in else None} for r in reqs],
        "events": [{"seq": e.seq, "code": e.event_code, "date": e.event_date, "origin": e.origin,
                    "exhibit_id": str(e.exhibit_id) if e.exhibit_id else None} for e in events],
    }


@app.get("/cases/{case_id}/card")
def get_card(case_id: uuid.UUID, version: int | None = None, session: Session = Depends(get_session),
             user: CurrentUser = Depends(ANY)) -> dict:
    case = _get_case(session, case_id)
    if version is None:
        card = latest_card(session, case.id)
    else:
        card = session.scalar(select(CaseCard).where(CaseCard.case_id == case.id, CaseCard.version == version))
    if card is None:
        raise HTTPException(404, "no card yet")
    return card.card


@app.get("/cases/{case_id}/pages/{seq}")
def get_page(case_id: uuid.UUID, seq: int, session: Session = Depends(get_session), user: CurrentUser = Depends(ANY)) -> dict:
    p = session.scalar(select(Page).where(Page.case_id == case_id, Page.seq == seq))
    if p is None:
        raise HTTPException(404, "page not found")
    return {"seq": p.seq, "page_no": p.page_no, "document_id": str(p.document_id), "text_layer_status": p.text_layer_status,
            "ocr_engine": p.ocr_engine, "ocr_confidence": p.ocr_confidence, "ocr_status": p.ocr_status,
            "pii_counts": p.pii_counts, "predicted_type": p.predicted_type, "classification": p.classification,
            "text_redacted": p.text_redacted, "width_px": p.width_px, "height_px": p.height_px,
            "has_image": p.image_blob_key is not None}


from rci.api import (  # noqa: E402
    routes_admin,
    routes_analysis,
    routes_auth,
    routes_backtest,
    routes_drafts,
    routes_intake,
    routes_reports,
    routes_review,
)

app.include_router(routes_auth.router)
app.include_router(routes_intake.router)
app.include_router(routes_analysis.router)
app.include_router(routes_review.router)
app.include_router(routes_admin.router)
app.include_router(routes_reports.router)
app.include_router(routes_drafts.router)
app.include_router(routes_backtest.router)
