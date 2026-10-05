"""Which document is this file? Run for files uploaded without a slot (AUTO) before the per-document scrutiny.

One LLM call on the first pages answers with one of the standard filing documents (rci.intake.slots.SLOTS) or OTHER,
what the document is in a few words, and any further documents bundled into the same file. Without an LLM (or when
the provider is not allowed for the case's data class) the slots' keywords decide — a heading weighs three times a
mention — helped by the filename. Input is the redacted page text, as for the scrutiny.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from rci.adapters.llm import DataClass, LLMClient, LLMError
from rci.intake.extract import readable
from rci.intake.slots import OTHER, SLOTS, Slot
from rci.pipeline.extract.common import PageText
from rci.pipeline.normalise import ascii_digits

log = logging.getLogger(__name__)

IDENTIFY_VERSION = "identify.v1"
IDENTIFY_PAGES = 3  # the first pages say what a document is
MAX_CHARS = 8000
HEADING_LINES = 8
HEADING_EXTRA_TOKENS = 3
MIN_SCORE = 3.0  # below this no slot is claimed: the file is an additional document

SYSTEM = (
    "A cooperative housing society in Maharashtra has filed an application with the District Collector to convert "
    "its land from Occupancy Class-II / Government lease to Occupancy Class-I. The applicant uploaded all the files "
    "of the case without saying which is which. Say which document this file is. Text is Marathi and/or English; "
    "personal data is replaced by tokens like [PERSON_ab12cd34].\n"
    "slot: one of these standard filing documents —\n{catalog}\n"
    "or `other` for anything else (society registration certificate, general body resolution, member list, NOC, "
    "conveyance deed, building plan or completion certificate, letters, notices, …).\n"
    "detected_as: what the document is, in a few English words (e.g. \"Property card (मिळकत पत्रिका)\", "
    "\"Society registration certificate\"). reason: one short sentence naming what on the page shows it.\n"
    "confidence: 0 to 1.\n"
    "bundled: when the file clearly holds further separate documents after the first one, what each of them is, in a "
    "few words; otherwise an empty list. Continuation pages and annexures of the same document are not separate."
)


def identify_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["slot", "detected_as", "reason", "confidence", "bundled"],
        "properties": {
            "slot": {"type": "string", "enum": [s.key for s in SLOTS] + [OTHER.key]},
            "detected_as": {"type": "string"},
            "reason": {"type": "string"},
            "confidence": {"type": "number"},
            "bundled": {"type": "array", "items": {"type": "string"}},
        },
    }


def _catalog() -> str:
    return "\n".join(f"  {s.key}: {s.what}" for s in SLOTS)


def _fold(s: str) -> str:
    return ascii_digits(unicodedata.normalize("NFC", s or "")).casefold()


def _is_heading(term: str, line: str) -> bool:
    return term in line and len(line.split()) <= len(term.split()) + HEADING_EXTRA_TOKENS


def keyword_scores(pages: list[PageText], filename: str) -> dict[str, float]:
    """Keyword score of each standard slot over the first pages and the filename."""
    lines = [_fold(ln) for p in pages[:IDENTIFY_PAGES] for ln in (p.ascii or p.text).split("\n") if ln.strip()]
    body, heads = "\n".join(lines), lines[:HEADING_LINES]
    name = re.sub(r"[\s_.\-]+", " ", _fold(filename))
    raw_name = _fold(filename)
    scores: dict[str, float] = {}
    for slot in SLOTS:
        s = 0.0
        for term, weight in slot.keywords:
            t = _fold(term)
            if t in body:
                s += weight * (3 if any(_is_heading(t, ln) for ln in heads) else 1)
        if any(_fold(h) in raw_name or _fold(h) in name for h in slot.filename_hints):
            s += 3
        if s > 0:
            scores[slot.key] = s
    return scores


def _by_rules(pages: list[PageText], filename: str) -> dict:
    ranked = sorted(keyword_scores(pages, filename).items(), key=lambda kv: -kv[1])
    top_key, top = ranked[0] if ranked else (None, 0.0)
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_key is None or top < MIN_SCORE:
        return {"slot": OTHER.key, "detected_as": None, "reason": "none of the standard documents' keywords were found",
                "confidence": 0.0}
    slot: Slot = next(s for s in SLOTS if s.key == top_key)
    return {"slot": slot.key, "detected_as": slot.label, "reason": f"keywords of the {slot.label} (score {top:g})",
            "confidence": round(top / (top + second + 1.0), 2)}


def identify_document(pages: list[PageText], filename: str, *, llm: LLMClient | None, data_class: DataClass) -> dict:
    """The stored identification: slot (a SLOTS key or OTHER), what the document is, how it was decided."""
    result = {"version": IDENTIFY_VERSION, "method": "rules", "bundled": [], "notes": []}
    if llm is not None and llm.available and readable(pages):
        user = f"Filename: {filename}\n\n" + "\n\n".join(f"=== page {p.doc_page_no} ===\n{p.text}" for p in pages[:IDENTIFY_PAGES])
        try:
            out = llm.complete_json(task="intake_identify", system=SYSTEM.format(catalog=_catalog()), user=user[:MAX_CHARS],
                                    schema=identify_schema(), schema_name="intake_identify", data_class=data_class)
            return {**result, "method": "llm", "slot": out["slot"], "detected_as": out["detected_as"].strip()[:120] or None,
                    "reason": out["reason"].strip()[:400] or None, "confidence": round(min(1.0, max(0.0, out["confidence"])), 2),
                    "bundled": [b.strip()[:120] for b in out["bundled"] if b.strip()][:10]}
        except LLMError as e:
            log.warning("intake identify: LLM skipped for %s: %s", filename, e)
            result["notes"].append(f"automatic identification unavailable ({e}); decided by keywords")
    return {**result, **_by_rules(pages, filename)}
