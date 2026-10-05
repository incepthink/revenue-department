"""Drafts (DB side): generate, store, download, officer upload + diff, ratings.

    generate(session, case)        → for each template: build both renders from the case's latest stored analysis and the
                                     card it was run on, render the docx, leak-check the issue render, store the blobs
                                     (container `out`, restricted like the rest) and two `drafts` rows sharing a version
    download(session, draft, user) → the internal render as stored (PII stays tokenised); the issue render with this
                                     case's PII tokens detokenised through vault_access (officer / admin, audited)
    upload(session, draft, …)      → the officer's edited .docx: stored, re-tokenised / redacted, diffed against the
                                     generated text (paragraph ops, numbers changed, clauses kept / edited)
    rate(session, draft, …)        → eval_labels draft_rating (one current rating per case, template and officer)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import uuid
import zipfile

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rci.auth import CurrentUser
from rci.db.models import (
    Case,
    CaseAnalysis,
    CaseCard,
    Draft,
    EvalLabel,
    EventCatalog,
    ExhibitType,
    Office,
    RequirementCatalog,
    RuleSet,
)
from rci.drafting import clauses as cl
from rci.drafting import render
from rci.drafting.config import DRAFTING_VERSION, DraftingConfig
from rci.drafting.context import TEMPLATES, DocModel, DraftInputs, build
from rci.pipeline.redact.vault import TOKEN_RE, SqlVaultStore, Vault
from rci.pipeline.runner import audit, latest_card
from rci.storage import BlobStore, get_blob_store

MODES = ("internal", "issue")
MAX_UPLOAD = 15 * 1024 * 1024


class DraftError(Exception):
    """The draft cannot be produced / served in the current state (→ HTTP 409)."""


# ------------------------------------------------------------------------------------------------
# Inputs
# ------------------------------------------------------------------------------------------------
def catalogs(session: Session) -> dict:
    return {
        "requirements": {r.code: {"name_mr": r.name_mr, "name_en": r.name_en, "category": r.category}
                         for r in session.scalars(select(RequirementCatalog))},
        "exhibit_types": {t.code: {"name_mr": t.name_mr, "name_en": t.name_en} for t in session.scalars(select(ExhibitType))},
        "events": {e.code: {"name_mr": e.name_mr, "name_en": e.name_en} for e in session.scalars(select(EventCatalog))},
    }


def rule_sets(session: Session) -> dict:
    return {r.code: {"name": r.name, "kind": r.kind, "status": r.status} for r in session.scalars(select(RuleSet))}


def load_inputs(session: Session, case: Case, *, analysis: dict | None = None, card: dict | None = None,
                today: dt.date | None = None, llm=None, cfg: DraftingConfig | None = None,
                cats: dict | None = None, rules: dict | None = None) -> tuple[DraftInputs, list[str]]:
    """(inputs, warnings). Without `analysis` the case's latest stored analysis is used, with the card it was run on."""
    warnings = []
    if analysis is None:
        row = session.scalar(select(CaseAnalysis).where(CaseAnalysis.case_id == case.id).order_by(CaseAnalysis.created_at.desc()).limit(1))
        if row is None:
            raise DraftError("the case has not been analysed yet — run the case analysis first")
        analysis = row.result
    if card is None:
        cid = (analysis.get("card") or {}).get("id")
        card_row = session.get(CaseCard, uuid.UUID(cid)) if cid else latest_card(session, case.id)
        if card_row is None:
            raise DraftError("the case has no card")
        latest = latest_card(session, case.id)
        if latest is not None and latest.id != card_row.id:
            warnings.append(f"the analysis was run on card v{card_row.version}; the case now has v{latest.version} — re-run the analysis")
        card = card_row.card
    cfg = cfg or DraftingConfig.load()
    office = session.get(Office, case.office_id)
    if llm is None and cfg.value("narrative.llm", True):
        from rci.adapters.llm import get_llm_client

        llm = get_llm_client()
    inputs = DraftInputs(
        case={"id": str(case.id), "case_ref": case.case_ref, "data_class": case.data_class},
        office={"code": office.code, "name_mr": office.name_mr, "district": office.district},
        card=card, analysis=analysis, catalogs=cats or catalogs(session), rule_sets=rules or rule_sets(session),
        clauses=cl.canonical_rows(session, case.data_class), cfg=cfg, today=today or dt.date.today(), llm=llm)
    return inputs, warnings


def own_token_checker(session: Session, case_id: uuid.UUID):
    store = SqlVaultStore(session)
    return lambda token: store.find_token(case_id, token) is not None


# ------------------------------------------------------------------------------------------------
# Rendering (no storage) — also used by the backtest
# ------------------------------------------------------------------------------------------------
def render_template(inputs: DraftInputs, code: str, own_token) -> dict[str, dict]:
    """mode -> {"model", "data", "paragraphs", "leak"}"""
    out = {}
    for mode in MODES:
        model = build(code, inputs, mode)
        data = render.render_docx(code, model.context, internal=mode == "internal", cfg=inputs.cfg)
        leak = render.leak_check(data, model.forbidden, own_token, cfg=inputs.cfg) if mode == "issue" else None
        out[mode] = {"model": model, "data": data, "paragraphs": render.docx_paragraphs(data), "leak": leak}
    return out


def _content(model: DocModel, paragraphs: list[str], leak: dict | None, inputs: DraftInputs, extra_warnings: list[str]) -> dict:
    internal = model.mode == "internal"
    return {
        "template_name_mr": TEMPLATES[model.template_code]["name_mr"],
        "analysis_id": inputs.analysis.get("analysis_id"), "card_id": (inputs.analysis.get("card") or {}).get("id"),
        "card_version": (inputs.analysis.get("card") or {}).get("version"),
        "clause_library": cl.fingerprint(inputs.clauses), "config": inputs.cfg.fingerprint(),
        "clauses_used": [({k: v for k, v in c.items() if k != "source_case_ids"} if not internal else c) for c in model.clauses_used],
        "narratives": model.narratives, "missing_slots": model.missing_slots, "warnings": extra_warnings + model.warnings,
        "crosscheck": model.crosscheck, "leak_check": leak, "complete": model.complete,
        "notes": model.notes if internal else [], "paragraphs": paragraphs,
    }


def generate(session: Session, case: Case, *, templates: list[str] | None = None, user_id: uuid.UUID | None = None,
             analysis: dict | None = None, card: dict | None = None, run_id: uuid.UUID | None = None, today: dt.date | None = None,
             blobs: BlobStore | None = None, llm=None) -> list[dict]:
    codes = templates or list(TEMPLATES)
    bad = [c for c in codes if c not in TEMPLATES]
    if bad:
        raise DraftError(f"unknown templates {bad}")
    inputs, warnings = load_inputs(session, case, analysis=analysis, card=card, today=today, llm=llm)
    own = own_token_checker(session, case.id)
    blobs = blobs or get_blob_store()
    analysis_id = inputs.analysis.get("analysis_id")
    out = []
    for code in codes:
        rendered = render_template(inputs, code, own)
        version = (session.scalar(select(func.max(Draft.version)).where(Draft.case_id == case.id, Draft.template_code == code)) or 0) + 1
        for old in session.scalars(select(Draft).where(Draft.case_id == case.id, Draft.template_code == code,
                                                       Draft.status.in_(("generated", "incomplete")))):
            old.status = "superseded"
        issue_ok = rendered["issue"]["leak"]["ok"]
        rows = {}
        for mode in MODES:
            r = rendered[mode]
            model = r["model"]
            withheld = mode == "issue" and not issue_ok
            key = f"drafts/{case.id}/{code}/v{version}/{mode}.docx"
            if not withheld:
                blobs.put("out", key, r["data"], render.DOCX_MIME)
            status = "generated" if (model.complete and issue_ok) else "incomplete"
            row = Draft(case_id=case.id, template_code=code, version=version, render_mode=mode, status=status,
                        content=_content(model, r["paragraphs"], r["leak"], inputs, warnings),
                        blob_key=None if withheld else key, sha256=hashlib.sha256(r["data"]).hexdigest(),
                        analysis_id=uuid.UUID(analysis_id) if analysis_id else None, run_id=run_id,
                        drafting_version=f"{DRAFTING_VERSION}+{inputs.cfg.fingerprint()}", created_by=user_id)
            session.add(row)
            rows[mode] = row
        session.flush()
        audit(session, "draft.generate", actor_kind="user" if user_id else "system", case_id=case.id, entity_type="draft",
              entity_id=rows["issue"].id, actor=str(user_id) if user_id else None, template=code, version=version,
              status=rows["issue"].status, leak_ok=issue_ok, clauses=len(rendered["issue"]["model"].clauses_used),
              missing=len(rendered["issue"]["model"].missing_slots))
        out.append(summary(rows["internal"], rows["issue"]))
    return out


# ------------------------------------------------------------------------------------------------
# Read / download / upload / rate
# ------------------------------------------------------------------------------------------------
def draft_json(d: Draft, *, full: bool = False) -> dict:
    c = d.content or {}
    out = {"id": str(d.id), "case_id": str(d.case_id), "template_code": d.template_code, "template_name_mr": c.get("template_name_mr"),
           "version": d.version, "render_mode": d.render_mode, "status": d.status, "created_at": d.created_at,
           "downloadable": d.blob_key is not None, "complete": c.get("complete"), "warnings": c.get("warnings", []),
           "missing_slots": c.get("missing_slots", []), "leak_check": c.get("leak_check"),
           "crosscheck": c.get("crosscheck", []), "analysis_id": str(d.analysis_id) if d.analysis_id else None,
           "run_id": str(d.run_id) if d.run_id else None, "drafting_version": d.drafting_version,
           "uploaded_at": d.uploaded_at, "officer_diff": (d.officer_diff or None) if full else (
               {k: d.officer_diff[k] for k in ("paragraphs", "similarity", "clauses_edited")} if d.officer_diff else None)}
    if full:
        out.update(paragraphs=c.get("paragraphs", []), notes=c.get("notes", []), clauses_used=c.get("clauses_used", []),
                   narratives=c.get("narratives", []))
    return out


def summary(internal: Draft, issue: Draft) -> dict:
    return {"template_code": issue.template_code, "version": issue.version, "status": issue.status,
            "internal": draft_json(internal), "issue": draft_json(issue)}


def list_drafts(session: Session, case_id: uuid.UUID) -> dict:
    rows = session.scalars(select(Draft).where(Draft.case_id == case_id).order_by(Draft.template_code, Draft.version.desc(),
                                                                                 Draft.render_mode)).all()
    latest: dict[str, dict] = {}
    history = []
    for d in rows:
        cur = latest.setdefault(d.template_code, {"template_code": d.template_code, "version": d.version})
        if d.version == cur["version"]:
            cur[d.render_mode] = draft_json(d)
            cur["status"] = cur.get("issue", {}).get("status", d.status)
        history.append({"id": str(d.id), "template_code": d.template_code, "version": d.version, "render_mode": d.render_mode,
                        "status": d.status, "created_at": d.created_at, "uploaded_at": d.uploaded_at})
    return {"templates": [{"code": c, **v} for c, v in TEMPLATES.items()],
            "latest": [latest[c] for c in TEMPLATES if c in latest], "history": history}


def _vault_escape(v: str) -> str:
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def download(session: Session, d: Draft, user: CurrentUser, blobs: BlobStore | None = None) -> tuple[bytes, str]:
    from rci.vault_access import render_with_pii

    if d.blob_key is None:
        raise DraftError("this render was withheld (the leak check failed) — regenerate after fixing the clause library")
    data = (blobs or get_blob_store()).get("out", d.blob_key)
    case = session.get(Case, d.case_id)
    if d.render_mode == "issue" and TOKEN_RE.search(render.docx_all_text(data)):
        data = render.detokenize_docx(data, lambda xml: render_with_pii(
            session, d.case_id, xml, user, purpose="draft_render", draft_ref=str(d.id), escape=_vault_escape))
    audit(session, "draft.download", actor_kind="user", case_id=d.case_id, entity_type="draft", entity_id=d.id, actor=str(user.id),
          template=d.template_code, version=d.version, mode=d.render_mode)
    safe = re.sub(r"[^\w.-]+", "_", case.case_ref)
    return data, f"{safe}_{d.template_code}_v{d.version}_{d.render_mode}.docx"


def _retokenize(session: Session, case_id: uuid.UUID, generated: list[str], uploaded: list[str]) -> list[str]:
    """Put the case's own tokens back where the officer's copy carries the detokenised values, then redact anything new,
    so the stored diff never holds a real name / number."""
    from rci.pipeline.redact.engine import Redactor

    vault = Vault.from_settings(SqlVaultStore(session))
    tokens = {m.group(0) for p in generated for m in TOKEN_RE.finditer(p)}
    values = []
    for t in tokens:
        try:
            values.append((vault.detokenize(case_id, t), t))
        except KeyError:
            continue
    values.sort(key=lambda x: -len(x[0]))
    red = Redactor(vault)
    out = []
    for p in uploaded:
        for v, t in values:
            if v:
                p = p.replace(v, t)
        out.append(red.redact(case_id, p).text)
    if values:
        audit(session, "vault.retokenize", actor_kind="system", case_id=case_id, purpose="draft_diff", tokens=len(values))
    return out


def upload(session: Session, d: Draft, data: bytes, filename: str, user: CurrentUser, blobs: BlobStore | None = None) -> dict:
    if len(data) > MAX_UPLOAD:
        raise DraftError("file too large")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/document.xml" not in z.namelist():
                raise DraftError("not a .docx file")
    except zipfile.BadZipFile:
        raise DraftError("not a .docx file") from None
    if d.render_mode != "issue":
        raise DraftError("upload the edited issue version (the internal render is for review only)")
    now = dt.datetime.now(dt.timezone.utc)
    key = f"drafts/{d.case_id}/{d.template_code}/v{d.version}/officer-{now.strftime('%Y%m%dT%H%M%S')}.docx"
    (blobs or get_blob_store()).put("out", key, data, render.DOCX_MIME)
    generated = (d.content or {}).get("paragraphs", [])
    uploaded = _retokenize(session, d.case_id, generated, render.docx_paragraphs(data))
    result = render.diff(generated, uploaded, (d.content or {}).get("clauses_used"))
    result["filename"] = filename
    d.officer_upload_blob_key, d.officer_upload_sha256 = key, hashlib.sha256(data).hexdigest()
    d.officer_diff, d.uploaded_by, d.uploaded_at, d.status = result, user.id, now, "officer_edited"
    session.flush()
    audit(session, "draft.upload", actor_kind="user", case_id=d.case_id, entity_type="draft", entity_id=d.id, actor=str(user.id),
          template=d.template_code, version=d.version, similarity=result["similarity"], clauses_edited=result["clauses_edited"])
    return result


USABILITY = ("as_is", "minor_edits", "major_edits", "unusable")


def rate(session: Session, d: Draft, user: CurrentUser, *, rating: int, usability: str, comment: str | None = None) -> dict:
    if not 1 <= rating <= 5:
        raise ValueError("rating must be 1-5")
    if usability not in USABILITY:
        raise ValueError(f"usability must be one of {USABILITY}")
    value = {"rating": rating, "usability": usability, "comment": comment, "draft_id": str(d.id), "version": d.version,
             "render_mode": d.render_mode, "run_id": str(d.run_id) if d.run_id else None}
    row = upsert_label(session, case_id=d.case_id, kind="draft_rating", key=d.template_code, value=value, user_id=user.id,
                       run_id=d.run_id)
    audit(session, "label.draft_rating", actor_kind="user", case_id=d.case_id, entity_type="eval_label", entity_id=row.id,
          actor=str(user.id), template=d.template_code, rating=rating)
    return {"id": str(row.id), **value}


def upsert_label(session: Session, *, case_id: uuid.UUID, kind: str, key: str, value: dict, user_id: uuid.UUID | None,
                 target_case_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None) -> EvalLabel:
    row = session.scalar(select(EvalLabel).where(EvalLabel.case_id == case_id, EvalLabel.label_kind == kind,
                                                 EvalLabel.label_key == key, EvalLabel.labeller_id == user_id))
    if row is None:
        row = EvalLabel(case_id=case_id, label_kind=kind, label_key=key, labeller_id=user_id)
        session.add(row)
    row.value, row.target_case_id, row.run_id = value, target_case_id, run_id
    row.updated_at = dt.datetime.now(dt.timezone.utc)
    session.flush()
    return row
