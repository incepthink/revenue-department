"""Anchors: where each finding and fact of an intake report sits on the case's pages (for the document viewer).

One anchor per evidence item, in the same order, plus one per fact:

  highlight  the quoted text was found on its page and the page has geometry — `boxes` cover it (word or line precision)
  page       the evidence names a page but cannot be boxed — reason no_quote (nothing quoted: a document-level finding or
             an observation), not_found (the quote is no longer on the page) or no_geometry (the page has no line boxes,
             e.g. read by the vision model; `lines` still point the text view at the quote when the text is aligned)
  missing    the evidence names no page (the document banner)

The quote is re-located on the stored redacted text (the snippet picks the occurrence), so reports made before anchors
existed are anchored when read, without running the check again. Box coordinates are 0..1 of the page image.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.db.models import Document, Page
from rci.pipeline.extract.common import PageText, locate_near
from rci.review.anchors import boxes_for_span
from rci.review.pageview import load_ocr

log = logging.getLogger(__name__)

ANCHORS_VERSION = "anchors.v1"


class _Pages:
    def __init__(self, session: Session, blobs, case_id: uuid.UUID):
        rows = session.execute(select(Page, Document.filename).join(Document, Page.document_id == Document.id)
                               .where(Page.case_id == case_id, Page.seq.is_not(None)).order_by(Page.seq)).all()
        self.blobs = blobs
        self.by_seq: dict[int, Page] = {p.seq: p for p, _ in rows}
        self.by_doc: dict[str, list[Page]] = {}
        self.texts: dict[int, PageText] = {}
        for p, filename in rows:
            self.by_doc.setdefault(str(p.document_id), []).append(p)
            self.texts[p.seq] = PageText(p.id, p.seq, p.page_no, filename, p.text_redacted or "", p.text_redacted_ascii or "")
        self._ocr: dict[int, dict | None] = {}

    def ocr(self, p: Page) -> dict | None:
        if p.seq not in self._ocr:
            try:
                self._ocr[p.seq] = load_ocr(self.blobs, p.ocr_blob_key)
            except Exception as e:  # a missing blob only costs the boxes
                log.warning("anchors: OCR blob of page %s unreadable: %s", p.seq, e)
                self._ocr[p.seq] = None
        return self._ocr[p.seq]


def _anchor(pages: _Pages, ev: dict) -> dict:
    raw = str(ev.get("raw") or "").strip()
    seq, doc_id = ev.get("page_seq"), ev.get("document_id")
    out = {"document_id": doc_id, "filename": ev.get("filename"), "page_seq": None, "page_id": None, "page_no": None,
           "kind": "missing", "boxes": [], "lines": [], "precision": None, "quote": raw or None, "reason": None}
    page = pages.by_seq.get(seq) if seq else None
    span = None
    if page is not None and doc_id and str(page.document_id) != doc_id:
        page = None  # page numbers moved since the check: look for the quote in the document itself
    if page is None and seq and doc_id and raw:
        page, span = next(((p, sp) for p in pages.by_doc.get(doc_id, []) if (sp := locate_near(raw, ev.get("snippet"), pages.texts[p.seq]))),
                          (None, None))
    if page is None:
        return out
    text = pages.texts[page.seq]
    out.update(page_seq=page.seq, page_id=str(page.id), page_no=page.page_no, kind="page")
    if not raw:
        out["reason"] = "no_quote"
        return out
    span = span or locate_near(raw, ev.get("snippet"), text)
    if span is None:
        out["reason"] = "not_found"
        return out
    boxes, lines, precision = boxes_for_span(pages.ocr(page), text.text, *span)
    out["lines"] = lines
    if boxes:
        out.update(kind="highlight", boxes=boxes, precision=precision)
    else:
        out["reason"] = "no_geometry"
    return out


def anchor_report(session: Session, blobs, case_id: uuid.UUID, report: dict) -> dict:
    """Adds `id` and `anchors` to every finding and `anchor` to every fact, in place; returns the report."""
    pages = _Pages(session, blobs, case_id)
    for i, f in enumerate(report.get("findings") or []):
        f["id"] = f"f{i}"
        f["anchors"] = [_anchor(pages, ev) for ev in f.get("evidence") or []]
    for fact in report.get("facts") or []:
        fact["anchor"] = _anchor(pages, fact)
    report["anchors_version"] = ANCHORS_VERSION
    return report
