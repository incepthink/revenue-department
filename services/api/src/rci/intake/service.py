"""Intake run for a newly filed case, and the results view.

  1 read     every document through the pipeline's per-document step (rasterise, text layer / OCR, redact)
  2 check    identify every document uploaded without a slot (rci.intake.identify — the slot is then set on the
             document); scrutinise the application first, so its date picks the rules in force (rci.rules.stack_for,
             loaded once), then every other document — one LLM call each (rci.intake.extract), told the rules'
             document checks for its slot
  3 report   checks across the documents → verdict, findings, actions (rci.intake.checks), under the rules in force
  4 assemble the usual case card (classify, segment, extract, validators), so review and analysis work as before

Each document is its own short transaction, so the results page can show progress while the run goes on. Every
transaction takes the same advisory lock as rci.pipeline.runner.process_case. Results live in pipeline_steps
(scope = document sha256 / 'case', step = 'intake'; the identification under step = 'identify'), and re-running
skips unchanged documents like the pipeline does. A document's input hash includes the rule checks sent with it, so
editing a rule's text re-reads only the documents that rule is about (verifying a rule changes nothing the model sees;
the report picks the new status up by itself).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rci.db.models import Case, Document, Page, PipelineStep
from rci.db.session import session_scope
from rci.intake.anchors import ANCHORS_VERSION, anchor_report
from rci.intake.checks import INTAKE_VERSION, DocIn, Docs, build_report, reference_date
from rci.intake.extract import scrutinise_document
from rci.intake.identify import IDENTIFY_VERSION, identify_document
from rci.intake.slots import AUTO, BY_KEY, OTHER, slot_catalog
from rci.pipeline.extract.common import PageText
from rci.pipeline.redact.engine import Redactor
from rci.pipeline.redact.vault import SqlVaultStore, Vault
from rci.pipeline.runner import (
    PipelineContext,
    RunSummary,
    _assign_page_seq,
    _record_step,
    _step,
    audit,
    process_case,
    process_document,
    sha256,
)
from rci.rules import RuleStack, document_checks_for, requirements_for, stack_for

log = logging.getLogger(__name__)

STEP = "intake"
IDENTIFY = "identify"
RUNNING = ("queued", "ingesting")


def _lock(session: Session, case_id: uuid.UUID) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"case:{case_id}"})


def _mark_report_stale(session: Session, case_id: uuid.UUID) -> None:
    previous = _step(session, case_id, "case", STEP)
    if previous is not None:  # a re-run: the old report is not the answer any more
        previous.status = "running"


def queue_intake(session: Session, case: Case) -> None:
    """Mark the case as waiting for its intake run (the caller starts run_intake after committing)."""
    case.status, case.last_error = "queued", None
    _mark_report_stale(session, case.id)


def _page_texts(session: Session, doc: Document) -> list[PageText]:
    pages = session.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.page_no)).all()
    return [PageText(p.id, p.seq, p.page_no, doc.filename, p.text_redacted or "", p.text_redacted_ascii or "") for p in pages]


def _unreadable_reason(session: Session, doc: Document) -> str | None:
    statuses = set(session.scalars(select(Page.ocr_status).where(Page.document_id == doc.id)))
    if "needs_ocr" in statuses:
        return "the file is a scan without a text layer and no OCR engine is configured (RCI_OCR_PRIMARY / RCI_OCR_FALLBACK)"
    return None


def _read_documents(ctx: PipelineContext, case_id: uuid.UUID, url: str | None) -> None:
    done: set[uuid.UUID] = set()
    while True:  # splitting a PDF can add child documents; pick them up in the same run
        with session_scope(url) as s:
            ids = [d for d in s.scalars(select(Document.id).where(Document.case_id == case_id).order_by(Document.seq)) if d not in done]
        if not ids:
            return
        for doc_id in ids:
            with session_scope(url) as s:
                doc = s.get(Document, doc_id)
                if doc.status != "processed":
                    doc.status = "reading"
            with session_scope(url) as s:
                _lock(s, case_id)
                case, doc = s.get(Case, case_id), s.get(Document, doc_id)
                redactor = Redactor(Vault.from_settings(SqlVaultStore(s), ctx.settings))
                try:
                    process_document(s, ctx, case, doc, redactor, RunSummary(case_id=str(case_id)))
                except Exception:  # recorded on the document (committed with it); the other documents still get read
                    log.exception("intake: reading %s failed", doc.filename)
            done.add(doc_id)


def _identify(ctx: PipelineContext, case_id: uuid.UUID, doc_id: uuid.UUID, url: str | None) -> None:
    """Decide the slot of a file uploaded without one, and set it on the document."""
    with session_scope(url) as s:
        case, doc = s.get(Case, case_id), s.get(Document, doc_id)
        pages, filename, data_class, scope = _page_texts(s, doc), doc.filename, case.data_class, doc.sha256
        input_hash = sha256("|".join([IDENTIFY_VERSION, ctx.signature(), filename, case.data_class]
                                     + [f"{p.seq}:{sha256(p.text.encode())}" for p in pages]).encode())
        _record_step(s, _step(s, case_id, scope, IDENTIFY), case_id, scope, IDENTIFY, input_hash, "running")
    try:  # outside any transaction, like the scrutiny
        result, status, error = identify_document(pages, filename, llm=ctx.llm, data_class=data_class), "done", None
    except Exception as e:
        log.exception("intake: identifying document %s failed", doc_id)
        result, status, error = {"slot": OTHER.key, "method": "none", "bundled": [], "notes": [str(e)[:300]]}, "failed", str(e)[:2000]
    with session_scope(url) as s:
        _lock(s, case_id)
        _record_step(s, _step(s, case_id, scope, IDENTIFY), case_id, scope, IDENTIFY, input_hash, status, result, error)
        s.get(Document, doc_id).slot = result["slot"]


def rules_for_slot(stack: RuleStack, slot: str) -> tuple[list[dict], list[dict]]:
    """(the document checks sent with a document in `slot`, the required documents without a slot of their own — offered
    to an additional document). Conditions are not decided yet (the tenure is read from the documents being checked):
    only rows whose condition is already false are left out; the report applies the rest once the facts are known."""
    checks = [{"code": c["code"], "text_en": c["text_en"]} for c in document_checks_for(stack, slot) if c["applies"] is not False]
    requirements = []
    if slot == OTHER.key:
        slotted = {k for k in BY_KEY if k != OTHER.key}
        requirements = [{"code": r["code"], "name_en": r.get("name_en") or r["code"], "name_mr": r.get("name_mr")}
                        for r in requirements_for(stack) if r.get("category") == "applicant_document"
                        and r.get("intake_slot") not in slotted and r["applies"] is not False]
    return checks, requirements


def _application_date(url: str | None, case_id: uuid.UUID, today: dt.date) -> dt.date:
    """The date that picks the rules: the application's (as stored by its scrutiny), else today."""
    with session_scope(url) as s:
        return reference_date(Docs(_doc_inputs(s, case_id)), today)[0]


def _scrutinise(ctx: PipelineContext, case_id: uuid.UUID, doc_id: uuid.UUID, stack: RuleStack, url: str | None) -> None:
    with session_scope(url) as s:
        case, doc = s.get(Case, case_id), s.get(Document, doc_id)
        pages = _page_texts(s, doc)
        checks, requirements = rules_for_slot(stack, doc.slot)
        rules = sha256(json.dumps([checks, requirements], sort_keys=True, ensure_ascii=False).encode())
        input_hash = sha256("|".join([INTAKE_VERSION, ctx.signature(), doc.slot, case.data_class, rules]
                                     + [f"{p.seq}:{sha256(p.text.encode())}" for p in pages]).encode())
        row = _step(s, case_id, doc.sha256, STEP)
        if row is not None and row.status == "done" and row.input_hash == input_hash:
            return
        _record_step(s, row, case_id, doc.sha256, STEP, input_hash, "running")
        reason = _unreadable_reason(s, doc)
        slot, data_class, scope = BY_KEY[doc.slot], case.data_class, doc.sha256
        identified = _step(s, case_id, doc.sha256, IDENTIFY) is not None
    # the model call runs outside any transaction: nothing is locked while it waits
    try:
        result, status, error = scrutinise_document(slot, pages, llm=ctx.llm, data_class=data_class, unreadable_reason=reason,
                                                    identified=identified, checks=checks, requirements=requirements), "done", None
    except Exception as e:
        log.exception("intake: checking document %s failed", doc_id)
        result, status, error = None, "failed", str(e)[:2000]
    with session_scope(url) as s:
        _lock(s, case_id)
        _record_step(s, _step(s, case_id, scope, STEP), case_id, scope, STEP, input_hash, status, result, error)


def _check_documents(ctx: PipelineContext, case_id: uuid.UUID, url: str | None, today: dt.date) -> None:
    with session_scope(url) as s:
        _lock(s, case_id)
        _assign_page_seq(s, s.get(Case, case_id))
        ids = list(s.scalars(select(Document.id).where(Document.case_id == case_id, Document.slot.is_not(None),
                                                      Document.status == "processed").order_by(Document.seq)))
    for doc_id in ids:
        with session_scope(url) as s:
            auto = s.get(Document, doc_id).slot == AUTO
        if auto:
            _identify(ctx, case_id, doc_id, url)
    with session_scope(url) as s:
        slots = dict(s.execute(select(Document.id, Document.slot).where(Document.id.in_(ids))).all())
    application = [d for d in ids if slots[d] == "application"]
    # the application's own checks come from the rules on its stored date (a previous run) or today's; when reading it
    # gives another date, it is checked again under those rules (skipped when their checks for it are the same)
    on = _application_date(url, case_id, today)
    with session_scope(url) as s:
        stack = stack_for(s, on)
    for doc_id in application:
        _scrutinise(ctx, case_id, doc_id, stack, url)
    read_on = _application_date(url, case_id, today)
    if read_on != on:
        with session_scope(url) as s:
            stack = stack_for(s, read_on)
        for doc_id in application:
            _scrutinise(ctx, case_id, doc_id, stack, url)
    for doc_id in ids:
        if doc_id not in application:
            _scrutinise(ctx, case_id, doc_id, stack, url)


def _doc_inputs(session: Session, case_id: uuid.UUID) -> list[DocIn]:
    docs = session.scalars(select(Document).where(Document.case_id == case_id, Document.slot.is_not(None)).order_by(Document.seq)).all()
    rows = {r.scope: r for r in session.scalars(select(PipelineStep).where(PipelineStep.case_id == case_id, PipelineStep.step == STEP))}
    ids = _identifications(session, case_id)
    out = []
    for d in docs:
        r = rows.get(d.sha256)
        error = d.error if d.status == "failed" else (r.error if r is not None and r.status == "failed" else None)
        out.append(DocIn(str(d.id), d.filename, d.slot, r.output if r is not None and r.status == "done" else None, error,
                         ids[d.sha256].output if d.sha256 in ids else None))
    return out


def _identifications(session: Session, case_id: uuid.UUID) -> dict[str, PipelineStep]:
    return {r.scope: r for r in session.scalars(select(PipelineStep).where(PipelineStep.case_id == case_id,
                                                                            PipelineStep.step == IDENTIFY))}


def _report(ctx: PipelineContext, case_id: uuid.UUID, url: str | None, today: dt.date) -> dict:
    with session_scope(url) as s:
        _lock(s, case_id)
        docs = _doc_inputs(s, case_id)
        stack = stack_for(s, reference_date(Docs(docs), today)[0])
        report = build_report(docs, ctx.params, stack, today=today, llm_provider=ctx.llm.provider)
        try:
            anchor_report(s, ctx.blobs, case_id, report)
        except Exception:  # the viewer anchors the report when it is read; the intake never fails over it
            log.exception("intake: anchoring the report of case %s failed", case_id)
        _record_step(s, _step(s, case_id, "case", STEP), case_id, "case", STEP, sha256(repr(report).encode()), "done", report)
        audit(s, "intake.report", case_id=case_id, entity_type="case", entity_id=case_id, verdict=report["verdict"],
              counts=report["counts"])
        return report


def run_intake(case_id: uuid.UUID, *, ctx: PipelineContext | None = None, url: str | None = None,
               today: dt.date | None = None, actor_kind: str = "api") -> dict | None:
    ctx = ctx or PipelineContext.default()
    try:
        with session_scope(url) as s:
            case = s.get(Case, case_id)
            if case is None:
                raise ValueError(f"case {case_id} not found")
            case.status, case.last_error = "ingesting", None
            _mark_report_stale(s, case_id)
        today = today or dt.date.today()
        _read_documents(ctx, case_id, url)
        _check_documents(ctx, case_id, url, today)
        report = _report(ctx, case_id, url, today)
        with session_scope(url) as s:
            process_case(s, case_id, ctx, actor_kind=actor_kind)  # the case card for review / analysis
        return report
    except Exception as e:
        log.exception("intake run for case %s failed", case_id)
        with session_scope(url) as s:
            case = s.get(Case, case_id)
            if case is not None:
                case.status, case.last_error = "failed", str(e)[:2000]
                audit(s, "intake.failed", actor_kind=actor_kind, case_id=case_id, error=str(e)[:500])
        return None


# ------------------------------------------------------------------------------------------------
# Results view (GET /cases/{id}/intake)
# ------------------------------------------------------------------------------------------------
def _stage(doc: Document, row: PipelineStep | None, identify: PipelineStep | None = None) -> str:
    if doc.status == "failed":
        return "failed"
    if doc.status == "processed" and doc.slot == AUTO:
        return "identifying" if identify is not None and identify.status == "running" else "read"
    if doc.status == "processed":
        if row is None:
            return "read"
        return {"running": "checking", "done": "checked", "failed": "failed"}.get(row.status, "read")
    return "reading" if doc.status == "reading" else "queued"


def _anchored(session: Session, case: Case, report: dict | None, blobs) -> dict | None:
    """The report with anchors: as stored, or — made before anchors existed — anchored on a copy (not persisted)."""
    if report is None or blobs is None or report.get("anchors_version") == ANCHORS_VERSION:
        return report
    try:
        return anchor_report(session, blobs, case.id, copy.deepcopy(report))
    except Exception:
        log.exception("intake: anchoring the stored report of case %s failed", case.id)
        return report


def intake_view(session: Session, case: Case, llm_provider: str, llm_available: bool, *, full: bool = True,
                blobs=None) -> dict:
    """full=False is the progress poll: stages and counts only, no extracted values. `blobs` lets a report made before
    anchors existed be anchored on read."""
    docs = session.scalars(select(Document).where(Document.case_id == case.id).order_by(Document.seq)).all()
    rows = {r.scope: r for r in session.scalars(select(PipelineStep).where(PipelineStep.case_id == case.id, PipelineStep.step == STEP))}
    report_row = rows.get("case")
    ids = _identifications(session, case.id)
    documents = []
    for d in docs:
        row, ident = rows.get(d.sha256), ids.get(d.sha256)
        slot = BY_KEY.get(d.slot or "")
        result = row.output if row is not None and row.status == "done" else None
        labels = {f.name: f.label for f in slot.fields} if slot else {}
        documents.append({
            "id": str(d.id), "slot": d.slot, "filename": d.filename, "pages": d.page_count,
            "stage": _stage(d, row, ident) if slot or d.slot == AUTO else ("read" if d.status == "processed" else _stage(d, None)),
            "error": d.error or (row.error if row is not None else None),
            "field_labels": labels, "result": result,
            "identified": ident.output if ident is not None and ident.status != "running" else None,
        })
    slotted = [x for x in documents if x["slot"]]
    if case.status == "failed":
        state = "failed"
    elif report_row is not None and report_row.status == "done":
        state = "done"  # the case card may still be assembling (`assembling`); the report does not wait for it
    elif case.status in RUNNING:
        state = "processing"
    else:
        state = "not_started"
    return {
        "case": {"id": str(case.id), "case_ref": case.case_ref, "title": case.title, "data_class": case.data_class,
                 "status": case.status, "last_error": case.last_error, "society_name": case.society_name},
        "state": state,
        "assembling": state == "done" and case.status in RUNNING,
        "progress": {"total": len(slotted),
                     "read": sum(1 for x in slotted if x["stage"] in ("read", "checking", "checked")),
                     "checked": sum(1 for x in slotted if x["stage"] in ("checked", "failed"))},
        "llm": {"provider": llm_provider, "available": llm_available},
        "slots": slot_catalog(),
        "documents": documents if full else [{k: x[k] for k in ("id", "slot", "filename", "pages", "stage")} for x in documents],
        "report": _anchored(session, case, report_row.output, blobs) if full and state == "done" else None,
        "report_at": report_row.finished_at if report_row is not None and state == "done" else None,
    }
