"""Ingestion pipeline orchestrator (DB side effects live here; the stages themselves are pure).

Per document (step "pages", idempotent on the document sha256 + pipeline version):
  1 ingest/split   PDF → pages; embedded PDF attachments → child documents; images → 1-page PDFs
  2 rasterise      PyMuPDF at settings.raster_dpi → blob pages/{sha}/{n}.png
  3 text layer     legacy/broken-font detector decides text layer vs forced OCR
  4 OCR/normalise  OCR router (Document Intelligence → LLM-vision fallback), NFC + ASCII-digit shadow
  5 redact         Presidio + custom recognisers + structural names → vault tokens
Per case (step "assemble", idempotent on the hash of every page text + catalogs + parameters):
  6 classify → segment → stage
  7 extract (rules [+ LLM]) with evidence
  8 validators
  9 card.v1 + draft case_requirements / case_events (new card version only if content changed)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import mimetypes
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pymupdf
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rci.adapters.llm import LLMClient, get_llm_client
from rci.adapters.ocr import OcrLine, OcrResult, OcrRouter, OcrWord, get_ocr_router
from rci.config import Parameters, Settings, get_settings
from rci.db.models import (
    AuditLog,
    Case,
    CaseCard,
    CaseEvent,
    CaseRequirement,
    Document,
    EventCatalog,
    Exhibit,
    ExhibitPage,
    ExhibitType,
    Office,
    Page,
    PipelineStep,
    RequirementCatalog,
)
from rci.pipeline.card import CardExhibit, assemble_card, content_hash, doc_date_of
from rci.pipeline.classify import ExhibitTypeDef, PageClass, classify_page
from rci.pipeline.extract.common import PageText
from rci.pipeline.extract.extractor import extract_exhibit
from rci.pipeline.normalise import normalise_text
from rci.pipeline.redact.engine import Redactor
from rci.pipeline.redact.vault import SqlVaultStore, Vault
from rci.pipeline.segment import PageSignal, segment_pages
from rci.pipeline.stage import StageInput, assign_stages
from rci.pipeline.textlayer import assess_text_layer
from rci.pipeline.validators import ExhibitFacts, run_validators
from rci.storage import BlobStore, get_blob_store

log = logging.getLogger(__name__)

PIPELINE_VERSION = "p1.0"
SUPPORTED = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".tif": "image/tiff", ".tiff": "image/tiff"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@lru_cache
def code_fingerprint() -> str:
    """Hash of the pipeline source: a code change invalidates cached steps without a manual version bump."""
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for f in sorted(root.rglob("*.py")):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


@dataclass
class PipelineContext:
    settings: Settings
    blobs: BlobStore
    llm: LLMClient
    ocr: OcrRouter
    params: Parameters

    @classmethod
    def default(cls) -> "PipelineContext":
        s = get_settings()
        llm = get_llm_client(s)
        return cls(settings=s, blobs=get_blob_store(s), llm=llm, ocr=get_ocr_router(llm, s), params=Parameters.load(s.parameters_file))

    def signature(self) -> str:
        """Configuration that changes pipeline output (part of every step's input hash)."""
        engines = [self.ocr.primary.name if self.ocr.primary else "-", self.ocr.fallback.name if self.ocr.fallback else "-"]
        return f"{PIPELINE_VERSION}|{code_fingerprint()}|{self.settings.raster_dpi}|{','.join(engines)}|{self.llm.provider}"


@dataclass
class RunSummary:
    case_id: str
    documents_processed: int = 0
    documents_skipped: int = 0
    pages: int = 0
    assembled: bool = False
    card_version: int | None = None
    card_created: bool = False
    exhibits: int = 0
    pii_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------
# Cases & documents
# ------------------------------------------------------------------------------------------------
def audit(session: Session, action: str, *, actor_kind: str = "system", case_id=None, entity_type=None, entity_id=None, **details):
    session.add(AuditLog(actor_kind=actor_kind, action=action, case_id=case_id, entity_type=entity_type,
                         entity_id=str(entity_id) if entity_id else None, details=details))


def get_or_create_case(session: Session, *, case_ref: str, data_class: str, office_code: str = "PILOT",
                       title: str | None = None, actor_kind: str = "system") -> tuple[Case, bool]:
    office = session.scalar(select(Office).where(Office.code == office_code))
    if office is None:
        raise ValueError(f"unknown office {office_code!r} — run `rci seed`")
    case = session.scalar(select(Case).where(Case.office_id == office.id, Case.case_ref == case_ref))
    if case:
        if case.data_class != data_class:
            raise ValueError(f"case {case_ref} exists with data_class={case.data_class}")
        return case, False
    case = Case(office_id=office.id, case_ref=case_ref, data_class=data_class, title=title)
    session.add(case)
    session.flush()
    audit(session, "case.create", actor_kind=actor_kind, case_id=case.id, entity_type="case", entity_id=case.id, case_ref=case_ref)
    return case, True


def add_document(session: Session, blobs: BlobStore, case: Case, filename: str, data: bytes, *,
                 parent: Document | None = None, slot: str | None = None, actor_kind: str = "system") -> tuple[Document, bool]:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"unsupported file type {ext!r} ({filename})")
    digest = sha256(data)
    slot = parent.slot if parent else slot  # attachments split out of a PDF belong to the parent's slot
    existing = session.scalar(select(Document).where(Document.case_id == case.id, Document.sha256 == digest))
    if existing:
        if slot and existing.slot is None:
            existing.slot = slot
        return existing, False
    key = f"{case.id}/{digest}{ext}"
    if not blobs.exists("raw", key):
        blobs.put("raw", key, data, SUPPORTED[ext])
    seq = (session.scalar(select(func.max(Document.seq)).where(Document.case_id == case.id)) or 0) + 1
    doc = Document(case_id=case.id, seq=seq, sha256=digest, filename=filename, mime_type=SUPPORTED[ext],
                   size_bytes=len(data), blob_key=key, parent_document_id=parent.id if parent else None, slot=slot)
    session.add(doc)
    session.flush()
    audit(session, "document.add", actor_kind=actor_kind, case_id=case.id, entity_type="document", entity_id=doc.id,
          filename=filename, sha256=digest, size=len(data), slot=slot)
    return doc, True


# ------------------------------------------------------------------------------------------------
# Step bookkeeping
# ------------------------------------------------------------------------------------------------
def _step(session: Session, case_id, scope: str, step: str) -> PipelineStep | None:
    return session.scalar(select(PipelineStep).where(PipelineStep.case_id == case_id, PipelineStep.scope == scope, PipelineStep.step == step))


def _step_done(row: PipelineStep | None, input_hash: str) -> bool:
    return row is not None and row.status == "done" and row.input_hash == input_hash and row.pipeline_version == PIPELINE_VERSION


def _record_step(session: Session, row: PipelineStep | None, case_id, scope, step, input_hash, status, output=None, error=None) -> PipelineStep:
    now = dt.datetime.now(dt.timezone.utc)
    if row is None:
        row = PipelineStep(case_id=case_id, scope=scope, step=step, runs=0)
        session.add(row)
    row.input_hash, row.pipeline_version, row.status = input_hash, PIPELINE_VERSION, status
    row.output, row.error, row.finished_at = output or {}, error, now
    row.runs = (row.runs or 0) + 1
    session.flush()
    return row


# ------------------------------------------------------------------------------------------------
# Steps 1-5: per document
# ------------------------------------------------------------------------------------------------
def _open_pdf(data: bytes, mime: str) -> pymupdf.Document:
    if mime == "application/pdf":
        return pymupdf.open(stream=data, filetype="pdf")
    img = pymupdf.open(stream=data, filetype=mime.split("/")[1])
    return pymupdf.open(stream=img.convert_to_pdf(), filetype="pdf")


def _text_layer_words(chars: list[dict], W: float, H: float) -> list[OcrWord]:
    """Runs of non-whitespace chars, each with the union of its char boxes."""
    words: list[OcrWord] = []
    run: list[dict] = []
    for ch in chars + [{"c": " "}]:
        if ch["c"].strip():
            run.append(ch)
            continue
        if run:
            x0, y0 = min(c["bbox"][0] for c in run), min(c["bbox"][1] for c in run)
            x1, y1 = max(c["bbox"][2] for c in run), max(c["bbox"][3] for c in run)
            words.append(OcrWord("".join(c["c"] for c in run), (x0 / W, y0 / H, x1 / W, y1 / H), 1.0))
            run = []
    return words


def _text_layer_result(page: pymupdf.Page) -> OcrResult:
    W, H = page.rect.width, page.rect.height
    lines = []
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            chars = [c for s in ln["spans"] for c in s["chars"]]
            t = "".join(c["c"] for c in chars).strip()
            if t:
                x0, y0, x1, y1 = ln["bbox"]
                lines.append(OcrLine(t, (x0 / W, y0 / H, x1 / W, y1 / H), 1.0, _text_layer_words(chars, W, H)))
    return OcrResult(engine="text_layer", lines=lines, mean_confidence=1.0)


def process_document(session: Session, ctx: PipelineContext, case: Case, doc: Document, redactor: Redactor,
                     summary: RunSummary, force: bool = False) -> None:
    input_hash = sha256(f"{doc.sha256}|{ctx.signature()}".encode())
    row = _step(session, case.id, doc.sha256, "pages")
    if not force and _step_done(row, input_hash):
        summary.documents_skipped += 1
        summary.pages += doc.page_count or 0
        return
    try:
        pdf = _open_pdf(ctx.blobs.get("raw", doc.blob_key), doc.mime_type)
        # Split: PDF attachments (portfolio dockets) become child documents processed on their own.
        for i in range(pdf.embfile_count()):
            info = pdf.embfile_info(i)
            name = info.get("filename") or info.get("name") or f"attachment-{i}.pdf"
            if Path(name).suffix.lower() in SUPPORTED:
                add_document(session, ctx.blobs, case, f"{doc.filename}::{name}", pdf.embfile_get(i), parent=doc)
        counts: dict[str, int] = {}
        statuses: dict[str, int] = {}
        for idx, page in enumerate(pdf, start=1):
            pix = page.get_pixmap(dpi=ctx.settings.raster_dpi)
            png = pix.tobytes("png")
            img_key = f"{doc.sha256}/{idx:04d}.png"
            if not ctx.blobs.exists("pages", img_key):
                ctx.blobs.put("pages", img_key, png, "image/png")
            raw_text = page.get_text("text")
            report = assess_text_layer(raw_text, [f[3] for f in page.get_fonts()])
            ocr: OcrResult | None
            ocr_status = "done"
            if report.use_text_layer:
                ocr = _text_layer_result(page)
            elif ctx.ocr.available:
                ocr = ctx.ocr.recognize(png, data_class=case.data_class)
            else:
                ocr, ocr_status = None, "needs_ocr"
                summary.warnings.append(f"{doc.filename} p{idx}: text layer {report.status}, no OCR engine configured")
            text_norm = normalise_text(ocr.text if ocr else "")
            ocr_key = None
            if ocr:
                ocr_key = f"{doc.sha256}/{idx:04d}.json"
                ctx.blobs.put("ocr", ocr_key, json.dumps(ocr.to_json(), ensure_ascii=False).encode(), "application/json")
            red = redactor.redact(case.id, text_norm)
            for k, v in red.counts.items():
                counts[k] = counts.get(k, 0) + v
            statuses[report.status] = statuses.get(report.status, 0) + 1
            p = session.scalar(select(Page).where(Page.document_id == doc.id, Page.page_no == idx))
            if p is None:
                p = Page(document_id=doc.id, case_id=case.id, page_no=idx)
                session.add(p)
            p.image_sha256, p.image_blob_key = sha256(png), img_key
            p.width_px, p.height_px, p.dpi = pix.width, pix.height, ctx.settings.raster_dpi
            p.text_layer_status, p.text_layer_report = report.status, report.to_json()
            p.ocr_engine = ocr.engine if ocr else None
            p.ocr_confidence = ocr.mean_confidence if ocr else None
            p.ocr_blob_key, p.ocr_status = ocr_key, ocr_status
            p.text_sha256 = sha256(text_norm.encode())
            p.text_redacted, p.text_redacted_ascii, p.pii_counts = red.text, red.text_ascii, red.counts
            p.pipeline_version = PIPELINE_VERSION
        doc.page_count, doc.status, doc.error = pdf.page_count, "processed", None
        session.flush()
        for k, v in counts.items():
            summary.pii_counts[k] = summary.pii_counts.get(k, 0) + v
        summary.documents_processed += 1
        summary.pages += pdf.page_count
        _record_step(session, row, case.id, doc.sha256, "pages", input_hash, "done",
                     {"pages": pdf.page_count, "pii": counts, "text_layer": statuses})
    except Exception as e:
        doc.status, doc.error = "failed", str(e)[:2000]
        _record_step(session, row, case.id, doc.sha256, "pages", input_hash, "failed", error=str(e)[:2000])
        raise


def _assign_page_seq(session: Session, case: Case) -> list[Page]:
    pages = session.scalars(
        select(Page).join(Document).where(Page.case_id == case.id).order_by(Document.seq, Page.page_no)
    ).all()
    for i, p in enumerate(pages, start=1):
        if p.seq != i:
            p.seq = i
    session.flush()
    return list(pages)


# ------------------------------------------------------------------------------------------------
# Steps 6-9: per case
# ------------------------------------------------------------------------------------------------
def _catalogs(session: Session) -> tuple[list[ExhibitType], list[dict], list[dict]]:
    types = session.scalars(select(ExhibitType).where(ExhibitType.active)).all()
    reqs = [
        {"code": r.code, "satisfied_by": r.satisfied_by, "query_keywords": r.query_keywords, "applies_when": r.applies_when, "status": r.status}
        for r in session.scalars(select(RequirementCatalog).where(RequirementCatalog.active).order_by(RequirementCatalog.code))
    ]
    events = [
        {"code": e.code, "source_exhibit_types": e.source_exhibit_types, "date_field": e.date_field, "typical_order": e.typical_order}
        for e in session.scalars(select(EventCatalog).where(EventCatalog.active).order_by(EventCatalog.typical_order))
    ]
    return list(types), reqs, events


def exhibit_uuid(case_id: uuid.UUID, exhibit_type: str, page_seqs: list[int]) -> uuid.UUID:
    return uuid.uuid5(case_id, f"{exhibit_type}:{page_seqs[0]}-{page_seqs[-1]}")


def assemble_case(session: Session, ctx: PipelineContext, case: Case, pages: list[Page], summary: RunSummary, force: bool = False) -> None:
    types, req_catalog, event_catalog = _catalogs(session)
    catalog_sig = json.dumps(
        [[t.code, t.keywords, t.negative_keywords, t.default_stage, t.party] for t in sorted(types, key=lambda t: t.code)]
        + [req_catalog, event_catalog], sort_keys=True, ensure_ascii=False,
    )
    params_sig = ctx.settings.parameters_file.read_bytes()
    input_hash = sha256(
        "|".join([ctx.signature(), sha256(catalog_sig.encode()), sha256(params_sig)]
                 + [f"{p.id}:{p.seq}:{sha256((p.text_redacted or '').encode())}" for p in pages]).encode()
    )
    row = _step(session, case.id, "case", "assemble")
    latest = session.scalar(select(CaseCard).where(CaseCard.case_id == case.id).order_by(CaseCard.version.desc()).limit(1))
    if not force and _step_done(row, input_hash) and latest is not None:
        summary.card_version = latest.version
        summary.exhibits = session.scalar(select(func.count()).select_from(Exhibit).where(Exhibit.case_id == case.id))
        return
    if latest is not None and latest.status == "approved":
        summary.warnings.append("latest card is approved; automatic re-segmentation skipped")
        return
    if session.scalar(select(func.count()).select_from(Exhibit).where(Exhibit.case_id == case.id, Exhibit.origin == "manual")):
        summary.warnings.append("case has manually edited exhibits; automatic re-segmentation skipped")
        return

    type_defs = [ExhibitTypeDef.from_row({"code": t.code, "default_stage": t.default_stage, "party": t.party,
                                          "keywords": t.keywords, "negative_keywords": t.negative_keywords}) for t in types]
    tmeta = {t.code: t for t in types}
    min_score = float(ctx.params.value("classifier_min_score"))
    llm_below = float(ctx.params.value("classifier_llm_fallback_below"))
    doc_index = {d.id: d.seq for d in case.documents}
    signals: list[PageSignal] = []
    for p in pages:
        cls: PageClass = classify_page(p.text_redacted or "", type_defs, min_score=min_score, llm_below=llm_below,
                                       llm=ctx.llm, data_class=case.data_class)
        p.classification, p.predicted_type, p.predicted_confidence = cls.to_json(), cls.code, cls.confidence
        signals.append(PageSignal(seq=p.seq, document_index=doc_index[p.document_id], cls=cls))
    segments = segment_pages(signals, switch_confidence=float(ctx.params.value("classifier_switch_confidence")))

    by_seq = {p.seq: p for p in pages}
    docs = {d.id: d for d in case.documents}
    extracted = []
    for n, seg in enumerate(segments, start=1):
        seg_pages = [by_seq[s] for s in seg.page_seqs]
        texts = [PageText(p.id, p.seq, p.page_no, docs[p.document_id].filename, p.text_redacted or "", p.text_redacted_ascii or "")
                 for p in seg_pages]
        ext = extract_exhibit(seg.exhibit_type, texts, llm=ctx.llm, data_class=case.data_class)
        extracted.append((n, seg, seg_pages, ext, doc_date_of(seg.exhibit_type, ext["fields"])))
    stages = assign_stages([StageInput(seg.exhibit_type, tmeta[seg.exhibit_type].party, tmeta[seg.exhibit_type].default_stage, dd)
                            for _, seg, _, _, dd in extracted])

    # Upsert automatic exhibits in place. Ids are deterministic (type + page range), so an unchanged
    # segment keeps its row and every reference to it from earlier cards stays valid.
    existing = {e.id: e for e in session.scalars(select(Exhibit).where(Exhibit.case_id == case.id, Exhibit.origin == "auto"))}
    card_exhibits: list[CardExhibit] = []
    keep: set[uuid.UUID] = set()
    for (n, seg, seg_pages, ext, dd), stage in zip(extracted, stages):
        eid = exhibit_uuid(case.id, seg.exhibit_type, seg.page_seqs)
        keep.add(eid)
        ex = existing.get(eid) or Exhibit(id=eid, case_id=case.id, origin="auto")
        ex.seq, ex.exhibit_type, ex.stage = n, seg.exhibit_type, stage
        ex.start_page_seq, ex.end_page_seq = seg.page_seqs[0], seg.page_seqs[-1]
        ex.confidence, ex.classification_method, ex.doc_date, ex.extraction = seg.confidence, seg.method, dd, ext
        if eid not in existing:
            session.add(ex)
            session.flush()
        page_ids = [p.id for p in seg_pages]
        if [ep.page_id for ep in ex.pages] != page_ids:
            session.execute(text("DELETE FROM exhibit_pages WHERE exhibit_id = :e"), {"e": eid})
            session.expire(ex, ["pages"])
            for i, pid in enumerate(page_ids):
                session.add(ExhibitPage(exhibit_id=eid, page_id=pid, position=i))
        card_exhibits.append(CardExhibit(ExhibitFacts(str(eid), n, seg.exhibit_type, stage, ext["fields"]),
                                         seg.page_seqs, seg.confidence, seg.method, dd))
    for eid, ex in existing.items():
        if eid not in keep:
            session.delete(ex)
    session.flush()

    validations = run_validators([ce.facts for ce in card_exhibits], ctx.params)
    office = session.get(Office, case.office_id)
    card, requirements, events = assemble_card(
        case={"case_id": str(case.id), "case_ref": case.case_ref, "office_code": office.code,
              "data_class": case.data_class, "case_type": case.case_type},
        exhibits=card_exhibits, validations=validations, requirement_catalog=req_catalog,
        event_catalog=event_catalog, pipeline_version=PIPELINE_VERSION,
    )
    chash = content_hash(card)
    summary.exhibits = len(card_exhibits)
    if latest is not None and latest.content_hash == chash:
        summary.card_version = latest.version
    else:
        if latest is not None and latest.status == "draft":
            latest.status = "superseded"
        new = CaseCard(case_id=case.id, version=(latest.version + 1) if latest else 1, card=card, content_hash=chash,
                       status="draft", parent_card_id=latest.id if latest else None)
        session.add(new)
        session.flush()
        for r in requirements:
            session.add(CaseRequirement(case_id=case.id, card_id=new.id, requirement_code=r["code"], status=r["status"],
                                        satisfied_by_exhibit_id=uuid.UUID(r["satisfied_by_exhibit_id"]) if r["satisfied_by_exhibit_id"] else None,
                                        was_queried_in=uuid.UUID(r["was_queried_in"]) if r["was_queried_in"] else None,
                                        evidence=r["evidence"]))
        for e in events:
            session.add(CaseEvent(case_id=case.id, card_id=new.id, event_code=e["code"], seq=e["seq"],
                                  event_date=dt.date.fromisoformat(e["date"]) if e["date"] else None,
                                  exhibit_id=uuid.UUID(e["exhibit_id"]), evidence=[e["evidence"]] if e["evidence"] else []))
        summary.card_version, summary.card_created = new.version, True
    facts = card["facts"]
    case.society_name = (facts["society"].get("name") or {}).get("value")
    case.filed_on = _d(facts["application"].get("date"))
    case.decided_on = _d(facts["order"].get("order_date"))
    summary.assembled = True
    _record_step(session, row, case.id, "case", "assemble", input_hash, "done",
                 {"exhibits": len(card_exhibits), "card_version": summary.card_version, "card_hash": chash})


def _d(fact: dict | None) -> dt.date | None:
    return dt.date.fromisoformat(fact["value"]) if fact and fact.get("value") else None


# ------------------------------------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------------------------------------
def process_case(session: Session, case_id: uuid.UUID, ctx: PipelineContext | None = None, *, force: bool = False,
                 actor_kind: str = "system") -> RunSummary:
    ctx = ctx or PipelineContext.default()
    case = session.get(Case, case_id)
    if case is None:
        raise ValueError(f"case {case_id} not found")
    summary = RunSummary(case_id=str(case.id))
    # One pipeline run per case at a time, across API/worker/CLI processes.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"case:{case.id}"})
    case.status, case.last_error = "ingesting", None
    redactor = Redactor(Vault.from_settings(SqlVaultStore(session), ctx.settings))
    try:
        # Child documents created by splitting are picked up in the same run.
        done: set[uuid.UUID] = set()
        while True:
            pending = [d for d in session.scalars(select(Document).where(Document.case_id == case.id).order_by(Document.seq)) if d.id not in done]
            if not pending:
                break
            for doc in pending:
                process_document(session, ctx, case, doc, redactor, summary, force=force)
                done.add(doc.id)
        session.refresh(case)
        pages = _assign_page_seq(session, case)
        if pages:
            assemble_case(session, ctx, case, pages, summary, force=force)
        case.status = "ingested" if summary.assembled or summary.card_version else "processed"
    except Exception as e:
        case.status, case.last_error = "failed", str(e)[:2000]
        audit(session, "pipeline.failed", actor_kind=actor_kind, case_id=case.id, error=str(e)[:500])
        raise
    audit(session, "pipeline.run", actor_kind=actor_kind, case_id=case.id, entity_type="case", entity_id=case.id,
          **{k: v for k, v in summary.__dict__.items() if k not in ("case_id",)})
    return summary


def latest_card(session: Session, case_id: uuid.UUID) -> CaseCard | None:
    return session.scalar(select(CaseCard).where(CaseCard.case_id == case_id).order_by(CaseCard.version.desc()).limit(1))


def guess_mime(filename: str) -> str:
    return SUPPORTED.get(Path(filename).suffix.lower()) or mimetypes.guess_type(filename)[0] or "application/octet-stream"


def to_jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))
