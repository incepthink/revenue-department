"""Page classifier: weighted keyword scorer over exhibit_types keywords, LLM fallback when unsure.

Runs on the redacted ASCII-digit text, so neither PII nor Devanagari/ASCII digit variation reaches it.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from rci.adapters.llm import DataClass, LLMClient, LLMError
from rci.pipeline.normalise import ascii_digits

TITLE_ZONE_LINES = 10
START_ZONE_LINES = 7
TITLE_EXTRA_TOKENS = 2
CONTINUATION_RE = re.compile(
    r"(?:पान|पृष्ठ|page)\s*(?:क्र\.?|क्रमांक|no\.?)?\s*[:\-]?\s*(\d+)\s*(?:/|of|पैकी)\s*(\d+)", re.I
)


@dataclass(frozen=True)
class Keyword:
    term: str
    weight: float
    title: bool = False
    title_only: bool = False
    title_only: bool = False


@dataclass(frozen=True)
class ExhibitTypeDef:
    code: str
    default_stage: str
    party: str
    keywords: tuple[Keyword, ...]
    negative: tuple[Keyword, ...] = ()

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ExhibitTypeDef":
        def kws(items):
            return tuple(Keyword(_fold(k["t"]), float(k["w"]), bool(k.get("title", False)), bool(k.get("title_only", False)))
                         for k in items or [])

        return cls(row["code"], row["default_stage"], row["party"], kws(row.get("keywords")), kws(row.get("negative_keywords")))


def _fold(s: str) -> str:
    return ascii_digits(unicodedata.normalize("NFC", s)).casefold()


@dataclass
class PageClass:
    code: str | None
    confidence: float
    method: str  # keywords | llm | none
    is_start: bool
    continuation_of: tuple[int, int] | None
    scores: dict[str, float] = field(default_factory=dict)
    hits: dict[str, list[str]] = field(default_factory=dict)
    llm_reason: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


def _lines(text: str) -> list[str]:
    return [ln for ln in (text or "").split("\n") if ln.strip()]


def _is_title(term: str, line: str) -> bool:
    """The keyword *is* the line (a heading), not a mention inside a sentence or a reference list:
    at most TITLE_EXTRA_TOKENS tokens besides the keyword itself."""
    return term in line and len(line.split()) <= len(term.split()) + TITLE_EXTRA_TOKENS


def score_page(text: str, types: list[ExhibitTypeDef]) -> tuple[dict[str, float], dict[str, list[str]], dict[str, bool]]:
    lines = [_fold(ln) for ln in _lines(text)]
    body = "\n".join(lines)
    title_zone, start_zone = lines[:TITLE_ZONE_LINES], lines[:START_ZONE_LINES]
    scores, hits, starts = {}, {}, {}
    for t in types:
        s, h, start = 0.0, [], False
        for k in t.keywords:
            if k.term not in body:
                continue
            if k.title and any(_is_title(k.term, ln) for ln in title_zone):
                s += 3 * k.weight
                h.append(f"title:{k.term}")
                if k.weight >= 3 and any(_is_title(k.term, ln) for ln in start_zone):
                    start = True
            elif not k.title_only:
                s += k.weight
                h.append(k.term)
        for k in t.negative:
            if k.term in body:
                s -= k.weight
                h.append(f"neg:{k.term}")
        if s > 0:
            scores[t.code], hits[t.code], starts[t.code] = s, h, start
    return scores, hits, starts


def continuation_marker(text: str) -> tuple[int, int] | None:
    head = "\n".join(_lines(ascii_digits(text))[:4] + _lines(ascii_digits(text))[-3:])
    m = CONTINUATION_RE.search(head)
    if m:
        n, total = int(m.group(1)), int(m.group(2))
        if 1 < n <= total:
            return (n, total)
    return None


CLASSIFY_SCHEMA_TEMPLATE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["exhibit_type", "is_first_page", "confidence", "reason"],
    "properties": {
        "exhibit_type": {"type": "string", "enum": []},
        "is_first_page": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
}

CLASSIFY_SYSTEM = (
    "You classify single pages of Maharashtra revenue-department dockets for conversion of Cooperative "
    "Housing Society land from Occupancy Class-II / Government lease to Class-I. Personal data has been "
    "replaced by tokens like [PERSON_ab12cd34]. Answer with the exhibit type code of the document this page "
    "belongs to, CONTINUATION if it only continues the previous document, or OTHER."
)


def classify_page(
    text: str,
    types: list[ExhibitTypeDef],
    *,
    min_score: float = 4.0,
    llm_below: float = 0.55,
    llm: LLMClient | None = None,
    data_class: DataClass = "synthetic",
) -> PageClass:
    scores, hits, starts = score_page(text, types)
    cont = continuation_marker(text)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top_code, top = ranked[0] if ranked else (None, 0.0)
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = round(top / (top + second + 1.0), 3) if top else 0.0
    code = top_code if top >= min_score else None
    result = PageClass(
        code=code,
        confidence=confidence if code else 0.0,
        method="keywords" if code else "none",
        is_start=bool(code and starts.get(code) and cont is None),
        continuation_of=cont,
        scores={k: round(v, 2) for k, v in ranked[:5]},
        hits={k: hits[k] for k, _ in ranked[:3]},
    )
    needs_llm = cont is None and (code is None or confidence < llm_below) and len(text.strip()) > 40
    if needs_llm and llm is not None and llm.available:
        schema = {**CLASSIFY_SCHEMA_TEMPLATE, "properties": {**CLASSIFY_SCHEMA_TEMPLATE["properties"]}}
        schema["properties"]["exhibit_type"] = {"type": "string", "enum": [t.code for t in types] + ["CONTINUATION"]}
        try:
            out = llm.complete_json(
                task="classify_page", system=CLASSIFY_SYSTEM, user=text[:6000], schema=schema,
                schema_name="page_class", data_class=data_class,
            )
        except LLMError:
            return result
        if out["exhibit_type"] == "CONTINUATION":
            result.code, result.is_start = None, False
        else:
            result.code, result.is_start = out["exhibit_type"], bool(out["is_first_page"])
        result.method, result.confidence = "llm", round(min(0.9, max(0.0, out["confidence"])), 3)
        result.llm_reason = out["reason"][:300]
    return result
