"""The generated answer: written only from the retrieved passages, every sentence citing a verbatim quote, then checked.

Post-check (deterministic, `check`):
  1 each cite's quote must be found in its passage (whitespace- and digit-script-tolerant); failing cites are dropped;
  2 a sentence left without a cite is dropped;
  3 every number, date, percentage, amount and GR reference in a sentence must appear in the union of its cited
    passages (ASCII digits, Indian commas removed, dates compared as dates whatever their format); otherwise the
    sentence is dropped, with the reason kept;
  4 nothing left, or the model said found=false → not_found.
Each surviving cite is then placed on its page (`cite_anchor`): the quote inside the provision's span, boxed.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from rci.adapters.llm import LLMClient
from rci.library.retrieval import Passage
from rci.library.taxonomy import TYPE_SHORT
from rci.pipeline.extract.common import DATE_RE, PageText, _loose, date_from_match, locate
from rci.pipeline.normalise import ascii_digits

NOT_FOUND = {"en": "Not found in the provided GRs.", "mr": "उपलब्ध शासन निर्णयांमध्ये आढळले नाही."}

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["found", "sentences"],
    "properties": {
        "found": {"type": "boolean"},
        "sentences": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "cites"],
            "properties": {
                "text": {"type": "string"},
                "cites": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["p", "quote"],
                                                     "properties": {"p": {"type": "integer"}, "quote": {"type": "string"}}}},
            }}},
    },
}

SYSTEM = (
    "You answer questions about Maharashtra Revenue Department rules using ONLY the numbered passages given: extracts "
    "from the Government Resolutions (GRs), gazette notifications, circulars, letters and case notes the department "
    "provided. You know nothing else.\n"
    "- Answer in {language}, in 1 to 4 short sentences.\n"
    "- Every sentence cites at least one passage: `p` is the passage number and `quote` is a short phrase copied "
    "character for character from that passage (as printed, Marathi stays Marathi) that supports the sentence.\n"
    "- Copy numbers, dates, percentages and amounts exactly as the passages give them. Never compute, convert or round.\n"
    "- Passages come from documents of different dates and later documents may change earlier ones. When they differ, "
    "say which document (with its date) says what; do not decide which one is in force.\n"
    "- If the passages do not answer the question, return found=false and no sentences. Never answer from general "
    "knowledge."
)


def chip_label(doc_type: str | None, doc_date: dt.date | None, label: str | None, kind: str | None, page: int | None) -> str:
    """"GR 05/07/2023 · para 3 · p.2"."""
    parts = [f"{TYPE_SHORT.get(doc_type or 'other', 'Document')}{' ' + doc_date.strftime('%d/%m/%Y') if doc_date else ''}"]
    if label:
        lab = ascii_digits(label)
        parts.append(f"para {lab}" if kind in ("paragraph", "clause") and re.match(r"^\d", lab) else lab)
    if page:
        parts.append(f"p.{page}")
    return " · ".join(parts)


def passage_block(n: int, p: Passage) -> str:
    head = chip_label(p.doc_type, p.doc_date, p.label, p.kind, p.page_from)
    return f"[P{n}] {head}{' — ' + p.subject_mr if p.subject_mr else ''}\n{p.text}"


def compose(query: str, lang: str, passages: list[Passage], llm: LLMClient) -> dict:
    """The model's raw answer (found, sentences with cites) — run `check` on it before showing it."""
    if not passages:
        return {"found": False, "sentences": []}
    user = f"Question: {query}\n\nPassages:\n\n" + "\n\n".join(passage_block(i, p) for i, p in enumerate(passages, start=1))
    return llm.complete_json(task="library_answer", system=SYSTEM.replace("{language}", "Marathi" if lang == "mr" else "English"),
                             user=user, schema=SCHEMA, schema_name="grounded_answer", data_class="public")


# ------------------------------------------------------------------------------------------------
# Post-check
# ------------------------------------------------------------------------------------------------
NUM = re.compile(r"(?<!\d)(?<!\d[.,])\d[\d,]*(?:\.\d+)?")


def _num(s: str) -> str | None:
    s = s.replace(",", "").rstrip(".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return format(d.normalize(), "f") if d == d.to_integral() else str(d.normalize())


def facts_of(text: str) -> tuple[set[str], set[dt.date]]:
    """(numbers, dates) mentioned in a text: dates in any d/m/y, Marathi or English-month format; the other numbers with
    Indian commas removed (3,23,70,460 → 32370460; 10.00 → 10)."""
    a = ascii_digits(text or "")
    dates: set[dt.date] = set()
    chars = list(a)
    for m in DATE_RE.finditer(a):
        d = date_from_match(m)
        if d:
            dates.add(d)
            for i in range(*m.span()):
                chars[i] = " "
    nums = {n for n in (_num(x) for x in NUM.findall("".join(chars))) if n is not None}
    return nums, dates


def _page_text(text: str) -> PageText:
    return PageText(None, 0, 0, "", text, ascii_digits(text))


def _support(passages: list[Passage]) -> tuple[set[str], set[dt.date]]:
    nums: set[str] = set()
    dates: set[dt.date] = set()
    for p in passages:
        for t in (p.text, p.label or "", p.doc_number or "", p.subject_mr or ""):
            n, d = facts_of(t)
            nums |= n
            dates |= d
        if p.doc_date:
            dates.add(p.doc_date)
    for d in list(dates):
        nums |= {str(d.day), str(d.month), str(d.year)}
    return nums, dates


def check(raw: dict, passages: list[Passage], lang: str) -> dict:
    """{status: answered | not_found, lang, text?, sentences: [{text, cites: [{p, passage_id, quote, label}]}],
    dropped: [{text, reason}]}"""
    kept, dropped = [], []
    for s in raw.get("sentences") or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        cites = []
        for c in s.get("cites") or []:
            i = c.get("p")
            q = (c.get("quote") or "").strip()
            if not isinstance(i, int) or not 1 <= i <= len(passages) or not q:
                continue
            p = passages[i - 1]
            if locate(q, _page_text(p.text)) is None:
                continue
            cites.append({"p": i, "passage_id": p.provision_id, "quote": q,
                          "label": chip_label(p.doc_type, p.doc_date, p.label, p.kind, p.page_from)})
        if not cites:
            dropped.append({"text": text, "reason": "no verifiable quote"})
            continue
        nums, dates = _support([passages[c["p"] - 1] for c in cites])
        s_nums, s_dates = facts_of(text)
        missing = sorted(n for n in s_nums if n not in nums) + sorted(d.isoformat() for d in s_dates if d not in dates)
        if missing:
            dropped.append({"text": text, "reason": f"not in the cited passages: {', '.join(missing)}"})
            continue
        kept.append({"text": text, "cites": cites})
    if not raw.get("found") or not kept:
        return {"status": "not_found", "lang": lang, "text": NOT_FOUND[lang], "sentences": [], "dropped": dropped}
    return {"status": "answered", "lang": lang, "sentences": kept, "dropped": dropped}


def find_in_span(quote: str, page_ascii: str, start: int, end: int) -> tuple[int, int] | None:
    """The quote inside [start, end) of a page (whitespace- and digit-script-tolerant)."""
    pattern = _loose(quote)
    m = pattern.search(page_ascii, start, end) if pattern else None
    return m.span() if m else None
