"""Review workflow, page viewer and requirement catalog routes."""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.api.deps import get_session
from rci.auth import CurrentUser, require
from rci.db.models import Case, Page, RequirementCatalog
from rci.pipeline.runner import audit
from rci.review import service
from rci.review.pageview import load_ocr, redacted_lines, render_page
from rci.storage import get_blob_store

router = APIRouter()
READ = require("reviewer", "officer")
REVIEW = require("reviewer")


def _case(session: Session, case_id: uuid.UUID) -> Case:
    c = session.get(Case, case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    return c


# ------------------------------------------------------------------------------------------------
# Review
# ------------------------------------------------------------------------------------------------
@router.get("/cases/{case_id}/review")
def get_review(case_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    return service.view(session, _case(session, case_id), user)


class StartBody(BaseModel):
    new_round: bool = False


@router.post("/cases/{case_id}/review/start")
def start(case_id: uuid.UUID, body: StartBody | None = None, session: Session = Depends(get_session),
          user: CurrentUser = Depends(REVIEW)) -> dict:
    case = _case(session, case_id)
    service.start_review(session, case, user, new_round=bool(body and body.new_round))
    return service.view(session, case, user)


class OpsBody(BaseModel):
    ops: list[dict] = Field(min_length=1, max_length=200)
    rev: str | None = None


@router.post("/cases/{case_id}/review/ops")
def ops(case_id: uuid.UUID, body: OpsBody, session: Session = Depends(get_session), user: CurrentUser = Depends(REVIEW)) -> dict:
    case = _case(session, case_id)
    service.apply(session, case, user, body.ops, rev=body.rev)
    return service.view(session, case, user)


@router.post("/cases/{case_id}/review/submit")
def submit(case_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(REVIEW)) -> dict:
    case = _case(session, case_id)
    service.submit(session, case, user)
    return service.view(session, case, user)


@router.post("/cases/{case_id}/review/approve")
def approve(case_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(REVIEW)) -> dict:
    case = _case(session, case_id)
    card = service.approve(session, case, user)
    return {"card": {"id": str(card.id), "version": card.version, "status": card.status, "locked": card.locked},
            "review": service.view(session, case, user)}


# ------------------------------------------------------------------------------------------------
# Page viewer
# ------------------------------------------------------------------------------------------------
def _page(session: Session, case_id: uuid.UUID, seq: int) -> Page:
    p = session.scalar(select(Page).where(Page.case_id == case_id, Page.seq == seq))
    if p is None:
        raise HTTPException(404, "page not found")
    return p


@router.get("/cases/{case_id}/pages/{seq}/ocr")
def page_ocr(case_id: uuid.UUID, seq: int, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    """Redacted OCR lines with normalised bounding boxes (0..1) for the overlay."""
    p = _page(session, case_id, seq)
    lines, aligned = redacted_lines(load_ocr(get_blob_store(), p.ocr_blob_key), p.text_redacted)
    return {"seq": p.seq, "width_px": p.width_px, "height_px": p.height_px, "ocr_engine": p.ocr_engine,
            "ocr_confidence": p.ocr_confidence, "ocr_status": p.ocr_status, "text_layer_status": p.text_layer_status,
            "aligned": aligned, "lines": [{k: v for k, v in l.items() if not (k == "text" and v is None)} for l in lines],
            "text_redacted": p.text_redacted}


@router.get("/cases/{case_id}/pages/{seq}/image")
def page_image(
    request: Request,
    case_id: uuid.UUID,
    seq: int,
    masked: bool = Query(True, description="black out lines that contain PII"),
    w: int = Query(1400, ge=200, le=4000, description="output width in px"),
    session: Session = Depends(get_session),
    user: CurrentUser = Depends(READ),
) -> Response:
    p = _page(session, case_id, seq)
    if not masked and not user.has("reviewer"):
        raise HTTPException(403, "unmasked page images need the reviewer or admin role")
    if not p.image_blob_key:
        raise HTTPException(404, "page image not rendered")
    blobs = get_blob_store()
    lines = None
    if masked:
        ocr = load_ocr(blobs, p.ocr_blob_key)
        lines = redacted_lines(ocr, p.text_redacted)[0] if ocr else None
    data = render_page(blobs.get("pages", p.image_blob_key), lines, masked=masked, width=w)
    mask_state = "none" if not masked else ("lines" if lines else "unavailable")
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store", "X-RCI-Mask": mask_state})


# ------------------------------------------------------------------------------------------------
# Requirement catalog (type-ahead + "create new code" from the query mapper)
# ------------------------------------------------------------------------------------------------
@router.get("/catalog/requirements")
def list_requirements(q: str | None = None, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> list[dict]:
    rows = session.scalars(select(RequirementCatalog).where(RequirementCatalog.active).order_by(RequirementCatalog.code)).all()
    out = [{"code": r.code, "name_en": r.name_en, "name_mr": r.name_mr, "category": r.category, "status": r.status,
            "satisfied_by": r.satisfied_by, "query_keywords": r.query_keywords} for r in rows]
    if q:
        k = q.casefold()
        out = [r for r in out if k in r["code"].casefold() or k in r["name_en"].casefold() or k in r["name_mr"].casefold()]
    return out


class RequirementCreate(BaseModel):
    code: str = Field(min_length=3, max_length=64)
    name_en: str = Field(min_length=2)
    name_mr: str = Field(min_length=2)
    category: str = "applicant_document"
    satisfied_by: list[str] = []
    query_keywords: list[str] = []
    description: str | None = None


CATEGORIES = ("applicant_document", "office_process", "payment", "legal_condition", "other")


@router.post("/catalog/requirements", status_code=201)
def create_requirement(body: RequirementCreate, session: Session = Depends(get_session), user: CurrentUser = Depends(REVIEW)) -> dict:
    code = body.code.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code):
        raise HTTPException(422, "code must be UPPER_SNAKE_CASE (letters, digits, underscore)")
    if body.category not in CATEGORIES:
        raise HTTPException(422, f"category must be one of {CATEGORIES}")
    if session.get(RequirementCatalog, code):
        raise HTTPException(409, f"requirement {code} exists")
    r = RequirementCatalog(code=code, name_en=body.name_en.strip(), name_mr=body.name_mr.strip(), category=body.category,
                           satisfied_by=body.satisfied_by, query_keywords=[k for k in body.query_keywords if k.strip()],
                           applies_when={}, description=body.description, status="to_verify", created_by=user.id)
    session.add(r)
    session.flush()
    audit(session, "catalog.requirement_create", actor_kind="user", entity_type="requirement_catalog", entity_id=code,
          actor=str(user.id))
    return {"code": r.code, "name_en": r.name_en, "name_mr": r.name_mr, "category": r.category, "status": r.status}
