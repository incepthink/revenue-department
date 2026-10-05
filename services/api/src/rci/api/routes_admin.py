"""Admin: rule sets and their rows — premium slabs, requirements, obligations, adjustments and the document checks the
intake applies (with verification), notification source uploads, rule resolution tool,
ASR import. Every change records updated_by / updated_at (so re-seeding leaves it alone) and is audited.

Verification rules (a row can only be marked `verified` when):
  rule set   a source document (the notification / gazette text) is uploaded and notification_ref is set
  child row  its rule set has a source document, the row has source_ref (page / clause), and a slab has its rate
"""
from __future__ import annotations

import datetime as dt
import hashlib
import mimetypes
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rci.api.deps import get_session
from rci.asr import import_asr
from rci.auth import CurrentUser, require
from rci.db.models import (
    AsrRate,
    RequirementCatalog,
    RuleAdjustment,
    RuleDocumentCheck,
    RuleObligation,
    RulePremiumSlab,
    RuleRequirement,
    RuleSet,
)
from rci.intake.slots import BY_KEY as INTAKE_SLOTS, OTHER as INTAKE_OTHER, SLOTS as INTAKE_SLOT_LIST
from rci.pipeline.runner import audit
from rci.rules import AmbiguousRule, NoRuleFound, UnverifiedRule, resolve_slab, stack_for, verification_gaps
from rci.storage import get_blob_store

router = APIRouter(prefix="/admin")
ADMIN = require("admin")
READ = require("reviewer", "officer")

TABLES: dict[str, tuple[type, tuple[str, ...]]] = {
    "slabs": (RulePremiumSlab, ("applicant_category", "holding_basis", "land_use", "window_from", "window_to", "rate_pct",
                                "rate_basis", "min_amount", "max_amount", "conditions", "source_ref", "notes")),
    "requirements": (RuleRequirement, ("requirement_code", "mandatory", "condition", "source_ref", "notes")),
    "obligations": (RuleObligation, ("code", "description_mr", "description_en", "condition", "source_ref", "notes")),
    "adjustments": (RuleAdjustment, ("code", "kind", "rate_pct", "amount", "applies_from", "applies_to", "condition",
                                     "source_ref", "notes")),
    "document_checks": (RuleDocumentCheck, ("code", "slot", "text_en", "text_mr", "severity", "condition", "source_ref",
                                            "notes")),
}
SEVERITIES = ("problem", "warning", "info")
RULE_SET_FIELDS = ("name", "kind", "notification_ref", "notification_date", "effective_from", "effective_to", "notes", "supersedes_code")
KINDS = ("base", "amendment", "circular", "amnesty")
SOURCE_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".txt": "text/plain"}


def _j(v: Any) -> Any:
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def _row_out(obj, cols) -> dict:
    return {"id": str(obj.id), "seed_key": getattr(obj, "seed_key", None), "status": obj.status,
            "verified_by": _j(obj.verified_by), "verified_at": _j(obj.verified_at), "updated_by": _j(obj.updated_by),
            "updated_at": _j(obj.updated_at), **{c: _j(getattr(obj, c)) for c in cols}}


def _rule_set_out(session: Session, rs: RuleSet, children: bool = False) -> dict:
    sup = session.get(RuleSet, rs.supersedes_id) if rs.supersedes_id else None
    out = {"id": str(rs.id), "code": rs.code, "name": rs.name, "kind": rs.kind, "notification_ref": rs.notification_ref,
           "notification_date": _j(rs.notification_date), "effective_from": _j(rs.effective_from),
           "effective_to": _j(rs.effective_to), "supersedes_code": sup.code if sup else None, "status": rs.status,
           "notes": rs.notes, "source_filename": rs.source_filename, "has_source": bool(rs.source_blob_key),
           "verified_by": _j(rs.verified_by), "verified_at": _j(rs.verified_at), "updated_at": _j(rs.updated_at)}
    counts = {}
    for name, (model, cols) in TABLES.items():
        rows = session.scalars(select(model).where(model.rule_set_id == rs.id).order_by(model.seed_key, model.id)).all()
        counts[name] = {"total": len(rows), "verified": sum(1 for r in rows if r.status == "verified")}
        if children:
            out[name] = [_row_out(r, cols) for r in rows]
    out["counts"] = counts
    if children:  # what the editor needs to show rows by name: the documents a requirement can name, the upload slots
        out["catalog"] = [{"code": c.code, "name_en": c.name_en, "name_mr": c.name_mr, "category": c.category,
                           "intake_slot": c.intake_slot}
                          for c in session.scalars(select(RequirementCatalog).order_by(RequirementCatalog.code))]
        out["slots"] = [{"key": s.key, "label": s.label, "label_mr": s.label_mr} for s in (*INTAKE_SLOT_LIST, INTAKE_OTHER)]
    return out


def _parse(col: str, v: Any) -> Any:
    if v in ("", None):
        return None
    try:
        if col.endswith(("_date", "_from", "_to")):
            return dt.date.fromisoformat(str(v))
        if col in ("rate_pct", "min_amount", "max_amount", "amount"):
            d = Decimal(str(v))
            if d < 0:
                raise HTTPException(422, f"{col} must be >= 0")
            if col == "rate_pct" and d > 100:
                raise HTTPException(422, "rate_pct is a percentage (0-100)")
            return d
        if col in ("conditions", "condition"):
            if not isinstance(v, dict):
                raise HTTPException(422, f"{col} must be an object")
            return v
        if col == "mandatory":
            return bool(v)
    except (ValueError, InvalidOperation):
        raise HTTPException(422, f"invalid value for {col}: {v!r}")
    return v


def _touch(obj, user: CurrentUser) -> None:
    obj.updated_by, obj.updated_at = user.id, dt.datetime.now(dt.timezone.utc)


def _set_status(session: Session, obj, status: str, user: CurrentUser, rule_set: RuleSet, is_slab: bool = False) -> None:
    if status not in ("verified", "to_verify"):
        raise HTTPException(422, "status must be verified or to_verify")
    if status == "verified":
        missing = []
        if not rule_set.source_blob_key:
            missing.append("upload the notification text to the rule set")
        if obj is rule_set:
            if not rule_set.notification_ref:
                missing.append("notification_ref")
        elif not obj.source_ref:
            missing.append("source_ref (clause / page of the notification)")
        if is_slab and obj.rate_pct is None:
            missing.append("rate_pct")
        if missing:
            raise HTTPException(422, "cannot verify: " + "; ".join(missing))
        obj.verified_by, obj.verified_at = user.id, dt.datetime.now(dt.timezone.utc)
    else:
        obj.verified_by = obj.verified_at = None
    obj.status = status


def _rule_set(session: Session, rule_set_id: uuid.UUID) -> RuleSet:
    rs = session.get(RuleSet, rule_set_id)
    if rs is None:
        raise HTTPException(404, "rule set not found")
    return rs


# ------------------------------------------------------------------------------------------------
# Rule sets
# ------------------------------------------------------------------------------------------------
@router.get("/rule-sets")
def list_rule_sets(session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> list[dict]:
    rows = session.scalars(select(RuleSet).order_by(RuleSet.effective_from, RuleSet.code)).all()
    return [_rule_set_out(session, r) for r in rows]


@router.get("/rule-sets/{rule_set_id}")
def get_rule_set(rule_set_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    return _rule_set_out(session, _rule_set(session, rule_set_id), children=True)


class RuleSetBody(BaseModel):
    code: str | None = None
    name: str | None = None
    kind: str | None = None
    notification_ref: str | None = None
    notification_date: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    supersedes_code: str | None = None
    notes: str | None = None
    status: str | None = None


def _apply_rule_set(session: Session, rs: RuleSet, body: RuleSetBody, user: CurrentUser) -> list[str]:
    data = body.model_dump(exclude_unset=True)
    changed = []
    for k in RULE_SET_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k == "kind" and v not in KINDS:
            raise HTTPException(422, f"kind must be one of {KINDS}")
        if k == "supersedes_code":
            if v:
                sup = session.scalar(select(RuleSet).where(RuleSet.code == v))
                if sup is None or sup.id == rs.id:
                    raise HTTPException(422, f"unknown rule set {v!r}")
                rs.supersedes_id = sup.id
            else:
                rs.supersedes_id = None
        else:
            setattr(rs, k, _parse(k, v) if k.endswith(("_date", "_from", "_to")) else v)
        changed.append(k)
    if rs.effective_from is None:
        raise HTTPException(422, "effective_from is required")
    if rs.effective_to and rs.effective_to < rs.effective_from:
        raise HTTPException(422, "effective_to is before effective_from")
    if "status" in data and data["status"] and data["status"] != rs.status:
        _set_status(session, rs, data["status"], user, rs)
        changed.append("status")
    elif changed and rs.status == "verified":
        _set_status(session, rs, "to_verify", user, rs)  # an edit to a verified row needs re-verification
        changed.append("status→to_verify")
    _touch(rs, user)
    return changed


@router.post("/rule-sets", status_code=201)
def create_rule_set(body: RuleSetBody, session: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)) -> dict:
    if not body.code or not body.name:
        raise HTTPException(422, "code and name are required")
    if session.scalar(select(RuleSet).where(RuleSet.code == body.code)):
        raise HTTPException(409, f"rule set {body.code} exists")
    rs = RuleSet(code=body.code.strip().upper(), name=body.name, kind=body.kind or "amendment", status="to_verify")
    session.add(rs)
    _apply_rule_set(session, rs, body.model_copy(update={"status": None}), user)
    session.flush()
    audit(session, "rules.rule_set_create", actor_kind="user", entity_type="rule_set", entity_id=rs.id, actor=str(user.id), code=rs.code)
    return _rule_set_out(session, rs, children=True)


@router.patch("/rule-sets/{rule_set_id}")
def patch_rule_set(rule_set_id: uuid.UUID, body: RuleSetBody, session: Session = Depends(get_session),
                   user: CurrentUser = Depends(ADMIN)) -> dict:
    rs = _rule_set(session, rule_set_id)
    changed = _apply_rule_set(session, rs, body, user)
    audit(session, "rules.rule_set_update", actor_kind="user", entity_type="rule_set", entity_id=rs.id, actor=str(user.id),
          fields=changed, status=rs.status)
    return _rule_set_out(session, rs, children=True)


@router.post("/rule-sets/{rule_set_id}/source")
def upload_source(rule_set_id: uuid.UUID, file: UploadFile = File(...), session: Session = Depends(get_session),
                  user: CurrentUser = Depends(ADMIN)) -> dict:
    rs = _rule_set(session, rule_set_id)
    name = file.filename or "source.pdf"
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in SOURCE_TYPES:
        raise HTTPException(415, f"source must be one of {sorted(SOURCE_TYPES)}")
    data = file.file.read()
    if not data:
        raise HTTPException(422, "empty file")
    digest = hashlib.sha256(data).hexdigest()
    key = f"rules/{rs.code}/{digest}{ext}"
    get_blob_store().put("sources", key, data, SOURCE_TYPES[ext])
    rs.source_blob_key, rs.source_filename = key, name
    _touch(rs, user)
    audit(session, "rules.source_upload", actor_kind="user", entity_type="rule_set", entity_id=rs.id, actor=str(user.id),
          filename=name, sha256=digest, size=len(data))
    return _rule_set_out(session, rs, children=True)


@router.get("/rule-sets/{rule_set_id}/source")
def get_source(rule_set_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> Response:
    rs = _rule_set(session, rule_set_id)
    if not rs.source_blob_key:
        raise HTTPException(404, "no source uploaded")
    media = mimetypes.guess_type(rs.source_filename or rs.source_blob_key)[0] or "application/octet-stream"
    return Response(get_blob_store().get("sources", rs.source_blob_key), media_type=media,
                    headers={"Content-Disposition": f'inline; filename="{rs.code}{("." + rs.source_blob_key.rsplit(".", 1)[-1])}"'})


# ------------------------------------------------------------------------------------------------
# Rule rows (slabs / requirements / obligations / adjustments)
# ------------------------------------------------------------------------------------------------
def _table(table: str):
    if table not in TABLES:
        raise HTTPException(404, f"unknown table {table!r}")
    return TABLES[table]


def _validate_row(session: Session, table: str, obj) -> None:
    if table == "slabs":
        for c in ("applicant_category", "holding_basis", "land_use", "rate_basis"):
            if not getattr(obj, c):
                raise HTTPException(422, f"{c} is required")
        if obj.window_from and obj.window_to and obj.window_to < obj.window_from:
            raise HTTPException(422, "window_to is before window_from")
    if table == "requirements" and not session.get(RequirementCatalog, obj.requirement_code or ""):
        raise HTTPException(422, f"unknown requirement code {obj.requirement_code!r}")
    if table in ("obligations", "adjustments", "document_checks") and not obj.code:
        raise HTTPException(422, "code is required")
    if table == "document_checks":
        if obj.slot not in INTAKE_SLOTS:
            raise HTTPException(422, f"slot must be one of {sorted(INTAKE_SLOTS)}")
        if not (obj.text_en or "").strip():
            raise HTTPException(422, "text_en is required (what the document must show)")
        if obj.severity not in SEVERITIES:
            raise HTTPException(422, f"severity must be one of {SEVERITIES}")
    if table == "adjustments" and obj.kind not in ("discount", "surcharge", "interest", "penalty", "rebate", "waiver"):
        raise HTTPException(422, "kind must be discount | surcharge | interest | penalty | rebate | waiver")


@router.post("/rule-sets/{rule_set_id}/{table}", status_code=201)
def create_row(rule_set_id: uuid.UUID, table: str, body: dict, session: Session = Depends(get_session),
               user: CurrentUser = Depends(ADMIN)) -> dict:
    rs = _rule_set(session, rule_set_id)
    model, cols = _table(table)
    obj = model(rule_set_id=rs.id, status="to_verify")
    for c in cols:
        if c in body:
            setattr(obj, c, _parse(c, body[c]))
    for c in ("conditions", "condition"):
        if c in cols and getattr(obj, c) is None:
            setattr(obj, c, {})
    if "mandatory" in cols and obj.mandatory is None:
        obj.mandatory = True
    if "severity" in cols and obj.severity is None:
        obj.severity = "problem"
    _validate_row(session, table, obj)
    _touch(obj, user)
    session.add(obj)
    session.flush()
    if body.get("status") == "verified":
        _set_status(session, obj, "verified", user, rs, table == "slabs")
    audit(session, "rules.row_create", actor_kind="user", entity_type=model.__tablename__, entity_id=obj.id, actor=str(user.id),
          rule_set=rs.code)
    return _row_out(obj, cols)


@router.patch("/rules/{table}/{row_id}")
def patch_row(table: str, row_id: uuid.UUID, body: dict, session: Session = Depends(get_session),
              user: CurrentUser = Depends(ADMIN)) -> dict:
    model, cols = _table(table)
    obj = session.get(model, row_id)
    if obj is None:
        raise HTTPException(404, "row not found")
    rs = session.get(RuleSet, obj.rule_set_id)
    changed = [c for c in cols if c in body]
    for c in changed:
        setattr(obj, c, _parse(c, body[c]))
    _validate_row(session, table, obj)
    if "status" in body and body["status"] != obj.status:
        _set_status(session, obj, body["status"], user, rs, table == "slabs")
        changed.append("status")
    elif changed and obj.status == "verified":
        _set_status(session, obj, "to_verify", user, rs)
        changed.append("status→to_verify")
    _touch(obj, user)
    audit(session, "rules.row_update", actor_kind="user", entity_type=model.__tablename__, entity_id=obj.id, actor=str(user.id),
          rule_set=rs.code, fields=changed, status=obj.status)
    return _row_out(obj, cols)


@router.delete("/rules/{table}/{row_id}", status_code=204)
def delete_row(table: str, row_id: uuid.UUID, session: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)) -> Response:
    model, _ = _table(table)
    obj = session.get(model, row_id)
    if obj is None:
        raise HTTPException(404, "row not found")
    if obj.seed_key:
        raise HTTPException(409, "seeded rows cannot be deleted (re-seeding would bring them back); set them to_verify and "
                                 "leave a note instead")
    audit(session, "rules.row_delete", actor_kind="user", entity_type=model.__tablename__, entity_id=obj.id, actor=str(user.id))
    session.delete(obj)
    return Response(status_code=204)


# ------------------------------------------------------------------------------------------------
# Resolution tool & verification gaps
# ------------------------------------------------------------------------------------------------
@router.get("/rules/resolve")
def resolve(on: dt.date, holding_basis: str = "govt_lease", land_use: str = "residential", applicant_category: str = "CHS",
            session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    st = stack_for(session, on)
    out: dict[str, Any] = {"stack": st.to_json()}
    try:
        out["result"] = resolve_slab(st, applicant_category=applicant_category, holding_basis=holding_basis,
                                     land_use=land_use).to_json()
        out["outcome"] = "resolved"
    except UnverifiedRule as e:
        out.update(outcome="unverified", message=str(e), result=e.resolution.to_json())
    except AmbiguousRule as e:
        out.update(outcome="ambiguous", message=str(e), candidates=[c.to_json() for c in e.candidates], needs=e.needs)
    except NoRuleFound as e:
        out.update(outcome="none", message=str(e))
    return out


@router.get("/rules/gaps")
def gaps(on: dt.date | None = None, session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    on = on or dt.date.today()
    rows = verification_gaps(session, on)
    return {"on": on.isoformat(), "active_chs_slabs_unverified": len(rows), "gaps": rows}


# ------------------------------------------------------------------------------------------------
# ASR
# ------------------------------------------------------------------------------------------------
@router.post("/asr/import")
def asr_import(file: UploadFile = File(...), dry_run: bool = True, replace: bool = False,
               session: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)) -> dict:
    data = file.file.read()
    report = import_asr(session, data, dry_run=dry_run, replace=replace, imported_by=user.id)
    if not dry_run:
        audit(session, "asr.import", actor_kind="user", entity_type="asr_rates", entity_id=report.batch_id, actor=str(user.id),
              filename=file.filename, ok=report.ok, inserted=report.inserted, replaced=report.replaced,
              errors=len(report.errors), sha256=hashlib.sha256(data).hexdigest())
    return report.to_json()


@router.get("/asr")
def asr_list(year: int | None = None, district: str | None = None, village: str | None = None,
             limit: int = Query(200, le=2000), session: Session = Depends(get_session), user: CurrentUser = Depends(READ)) -> dict:
    q = select(AsrRate)
    if year:
        q = q.where(AsrRate.year == year)
    if district:
        q = q.where(AsrRate.district == district)
    if village:
        q = q.where(AsrRate.village.ilike(f"%{village}%"))
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    rows = session.scalars(q.order_by(AsrRate.year.desc(), AsrRate.district, AsrRate.village, AsrRate.zone).limit(limit)).all()
    years = session.execute(select(AsrRate.year, func.count(), func.count().filter(AsrRate.status == "verified"))
                            .group_by(AsrRate.year).order_by(AsrRate.year.desc())).all()
    cols = ("year", "division", "district", "taluka", "village", "zone", "sub_zone", "cts_range", "land_rate_per_sqm",
            "residential_rate_per_sqm", "commercial_rate_per_sqm", "source_ref", "status", "import_batch_id", "imported_at")
    return {"total": total, "years": [{"year": y, "rows": n, "verified": v} for y, n, v in years],
            "rows": [{"id": str(r.id), **{c: _j(getattr(r, c)) for c in cols}} for r in rows]}
