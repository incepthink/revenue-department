"""Segmenter: turns the ordered page classifications of a docket into exhibits (contiguous page runs)."""
from __future__ import annotations

from dataclasses import dataclass, field

from rci.pipeline.classify import PageClass


@dataclass
class PageSignal:
    seq: int  # docket page number (1-based, across documents)
    document_index: int
    cls: PageClass


@dataclass
class Segment:
    exhibit_type: str
    page_seqs: list[int] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    method: str = "keywords"

    @property
    def confidence(self) -> float:
        known = [c for c in self.confidences if c > 0]
        # Unclassified continuation pages don't lower confidence; an all-unknown segment is 0.
        return round(sum(known) / len(known), 3) if known else 0.0


def segment_pages(pages: list[PageSignal], *, switch_confidence: float = 0.6) -> list[Segment]:
    """Rules, in order:
    1. a document (file) boundary always starts a new exhibit;
    2. an explicit continuation marker ("पान क्र. 2/3") never starts one;
    3. a start marker (title keyword on a short line at the top) starts one, even for the same type
       (back-to-back challans, two property cards);
    4. a confidently classified page of a *different* type starts one;
    5. anything else continues the current exhibit (an OTHER exhibit adopts the first real type it sees).
    """
    segments: list[Segment] = []
    prev_doc: int | None = None
    for p in pages:
        c = p.cls
        cur = segments[-1] if segments else None
        new_doc = p.document_index != prev_doc
        prev_doc = p.document_index
        if cur is None or new_doc:
            segments.append(Segment(c.code or "OTHER", [p.seq], [c.confidence], c.method))
            continue
        if c.continuation_of is not None:
            cur.page_seqs.append(p.seq)
            cur.confidences.append(c.confidence if c.code == cur.exhibit_type else 0.0)
            continue
        starts_new = c.code is not None and (
            c.is_start or (c.code != cur.exhibit_type and c.confidence >= switch_confidence and cur.exhibit_type != "OTHER")
        )
        if starts_new:
            segments.append(Segment(c.code, [p.seq], [c.confidence], c.method))
        else:
            if cur.exhibit_type == "OTHER" and c.code is not None:
                cur.exhibit_type, cur.method = c.code, c.method
            cur.page_seqs.append(p.seq)
            cur.confidences.append(c.confidence if c.code == cur.exhibit_type else 0.0)
    return segments
