"""Drafting (Phase 4): the four Marathi drafts per case (internal + issue renders), officer uploads and ratings; the clause
library (mining, clusters, curation)."""
from __future__ import annotations

import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.api.deps import get_session
from rci.auth import CurrentUser, require
from rci.db.models import Case, Clause, Draft, EvalLabel
from rci.drafting import clauses as cl
from rci.drafting import service
from rci.drafting.context import TEMPLATES
from rci.drafting.render import DOCX_MIME

router = APIRouter()
OFFICER = require("officer")  # + admin: drafts carry case facts and, on download, detokenised PII
READ = require("reviewer", "officer")
ADMIN = require("admin")


def _case(session: Session, case_id: uuid.UUID) -> Case:
    c = session.get(Case, case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    return c


def _draft(session: Session, draft_id: uuid.UUID) -> Draft:
    d = session.get(Draft, draft_id)
    if d is None:
        raise HTTPException(404, "draft not found")
    return d


# ------------------------------------------------------------------------------------------------
# Drafts
# ------------------------------------------------------------------------------------------------
@router.get("/drafting/templates")
def templates(user: CurrentUser = Depends(READ)) -> list[dict]:
    return [{"code": c, **v} for c, v in TEMPLATES.items()]


class GenerateBody(BaseModel):
    templates: list[Literal["SCRUTINY_NOTE", "QUERY_LETTER", "DEMAND_LETTER", "COLLECTOR_ORDER"]] | None = None


@router.post("/cases/{case_id}/drafts", status_code=201)
def generate(case_id: uuid.UUID, body: GenerateBody | None = None, session: Session = Depends(get_session),
             user: CurrentUser = Depends(OFFICER)) -> list[dict]:
    try:
        return service.generate(session, _case(session, case_id), templates=(body.templates if body else None), user_id=user.id)
    except service.DraftError as e:
        raise HTTPException(409, str(e))


@router.get("/cases/{case_id}/drafts")
def list_drafts(case_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(OFFICER)) -> dict:
    _case(session, case_id)
    out = service.list_drafts(session, case_id)
    ratings = session.scalars(select(EvalLabel).where(EvalLabel.case_id == case_id, EvalLabel.label_kind == "draft_rating",
                                                      EvalLabel.labeller_id == user.id)).all()
    out["my_ratings"] = {r.label_key: r.value for r in ratings}
    return out


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(OFFICER)) -> dict:
    return service.draft_json(_draft(session, draft_id), full=True)


@router.get("/drafts/{draft_id}/download")
def download(draft_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(OFFICER)) -> Response:
    try:
        data, name = service.download(session, _draft(session, draft_id), user)
    except service.DraftError as e:
        raise HTTPException(409, str(e))
    return Response(data, media_type=DOCX_MIME, headers={"content-disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.post("/drafts/{draft_id}/upload")
def upload(draft_id: uuid.UUID, file: UploadFile = File(...), session: Session = Depends(get_session),
           user: CurrentUser = Depends(OFFICER)) -> dict:
    try:
        return service.upload(session, _draft(session, draft_id), file.file.read(), file.filename or "edited.docx", user)
    except service.DraftError as e:
        raise HTTPException(422, str(e))


class RatingBody(BaseModel):
    rating: int = Field(ge=1, le=5)
    usability: Literal["as_is", "minor_edits", "major_edits", "unusable"]
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/drafts/{draft_id}/rating")
def rate(draft_id: uuid.UUID, body: RatingBody, session: Session = Depends(get_session), user: CurrentUser = Depends(OFFICER)) -> dict:
    return service.rate(session, _draft(session, draft_id), user, rating=body.rating, usability=body.usability, comment=body.comment)


# ------------------------------------------------------------------------------------------------
# Clause library
# ------------------------------------------------------------------------------------------------
def _clause_json(c: Clause) -> dict:
    return {"id": str(c.id), "clause_key": c.clause_key, "kind": c.kind, "text_mr": c.text_mr, "template": c.template, "slots": c.slots,
            "rule_set_codes": c.rule_set_codes, "conditions": c.conditions, "status": c.status, "origin": c.origin,
            "data_class": c.data_class, "verification": c.verification, "cluster_id": c.cluster_id, "member_count": c.member_count,
            "flags": c.flags, "notes": c.notes, "source_refs": [{k: r.get(k) for k in ("case_id", "case_ref", "page_seq")}
                                                               for r in (c.source_refs or [])[:12]],
            "n_sources": len(c.source_case_ids or []), "curated_at": c.curated_at, "updated_at": c.updated_at,
            "problems": cl.validate_template(c.template or "", key=c.clause_key) if c.template else ["no template"]}


@router.get("/clauses/meta")
def clause_meta(user: CurrentUser = Depends(READ)) -> dict:
    return {"kinds": list(cl.KINDS), "condition_keys": list(cl.CONDITION_KEYS),
            "slots": [{"name": k, "description": v[0], "example": v[1]} for k, v in cl.SLOTS.items()],
            "item_slots": [{"name": k, "description": v[0], "example": v[1]} for k, v in cl.ITEM_SLOTS.items()],
            "filters": ["mr_num", "inr", "mr_words", "mr_rupees", "mr_date", "fy_label"]}


@router.get("/clauses")
def list_clauses(kind: str | None = None, status: str | None = None, data_class: str | None = None, key: str | None = None,
                 session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> list[dict]:
    q = select(Clause).order_by(Clause.kind, Clause.cluster_id.nulls_first(), Clause.member_count.desc(), Clause.clause_key)
    if kind:
        q = q.where(Clause.kind == kind)
    if status:
        q = q.where(Clause.status == status)
    if data_class:
        q = q.where(Clause.data_class == data_class)
    if key:
        q = q.where(Clause.clause_key == key)
    return [_clause_json(c) for c in session.scalars(q)]


class MineBody(BaseModel):
    data_class: Literal["real", "synthetic"] | None = None


@router.post("/clauses/mine")
def mine(body: MineBody | None = None, session: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)) -> dict:
    return cl.mine(session, data_class=body.data_class if body else None)


class ClausePatch(BaseModel):
    clause_key: str | None = Field(default=None, max_length=96)
    kind: str | None = None
    template: str | None = None
    rule_set_codes: list[str] | None = None
    conditions: dict | None = None
    status: Literal["candidate", "canonical", "rejected"] | None = None
    verification: Literal["verified", "to_verify"] | None = None
    notes: str | None = None


@router.patch("/clauses/{clause_id}")
def patch_clause(clause_id: uuid.UUID, body: ClausePatch, session: Session = Depends(get_session),
                 user: CurrentUser = Depends(OFFICER)) -> dict:
    c = session.get(Clause, clause_id)
    if c is None:
        raise HTTPException(404, "clause not found")
    try:
        cl.update_clause(session, c, body.model_dump(exclude_unset=True), user_id=user.id, is_admin=user.role == "admin")
    except cl.ClauseError as e:
        raise HTTPException(422, {"errors": e.errors})
    return _clause_json(c)


class ClauseCreate(ClausePatch):
    clause_key: str = Field(max_length=96)
    kind: str
    template: str
    data_class: Literal["real", "synthetic", "any"]


@router.post("/clauses", status_code=201)
def create_clause(body: ClauseCreate, session: Session = Depends(get_session), user: CurrentUser = Depends(OFFICER)) -> dict:
    try:
        c = cl.create_clause(session, body.model_dump(exclude_unset=True), user_id=user.id, is_admin=user.role == "admin")
    except cl.ClauseError as e:
        raise HTTPException(422, {"errors": e.errors})
    return _clause_json(c)


class PreviewBody(BaseModel):
    template: str
    clause_key: str = ""
    case_id: uuid.UUID | None = None


@router.post("/clauses/preview")
def preview(body: PreviewBody, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    problems = cl.validate_template(body.template, key=body.clause_key)
    slots = cl.example_slots(body.clause_key)
    source = "examples"
    if body.case_id:
        from rci.drafting.context import slot_values

        try:
            inputs, _ = service.load_inputs(session, _case(session, body.case_id), llm=False)
            slots = {**slots, **{k: v for k, v in slot_values(inputs).items() if v not in (None, "")}}
            source = "case"
        except service.DraftError:
            source = "examples (case not analysed)"
    try:
        text, missing = cl.render(body.template, slots)
    except Exception as e:  # noqa: BLE001 — syntax errors are reported, not raised
        return {"text": None, "missing": [], "problems": problems or [str(e)], "slots_from": source}
    return {"text": text, "missing": missing, "problems": problems, "slots_from": source}
