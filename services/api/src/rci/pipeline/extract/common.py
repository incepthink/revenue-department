"""Shared extraction primitives: page texts, evidence objects and value parsers.

Regexes run on the ASCII-digit shadow of the redacted text; `raw` and `snippet` are cut from the
redacted text at the same offsets, so evidence shows the page exactly as written (Devanagari digits
and all) while values are parsed uniformly.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from rci.pipeline.normalise import ascii_digits

SQFT_TO_SQM = Decimal("0.09290304")


@dataclass
class PageText:
    page_id: uuid.UUID | None
    seq: int
    doc_page_no: int
    document: str
    text: str  # redacted, original digits
    ascii: str  # redacted, ASCII digits (same length)


@dataclass
class Evidence:
    value: Any
    raw: str
    page_seq: int
    doc_page_no: int
    document: str
    snippet: str
    method: str = "rules"  # rules | llm | rules+llm
    confidence: float = 0.75
    page_id: str | None = None
    alternatives: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        d["value"] = jsonable(self.value)
        return d


def jsonable(v: Any) -> Any:
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    return v


def snippet(page: PageText, start: int, end: int, pad: int = 50) -> str:
    s = page.text[max(0, start - pad) : min(len(page.text), end + pad)]
    return re.sub(r"\s+", " ", s).strip()


def make_evidence(page: PageText, start: int, end: int, value: Any, confidence: float = 0.75, method: str = "rules") -> Evidence:
    return Evidence(
        value=value, raw=page.text[start:end].strip(), page_seq=page.seq, doc_page_no=page.doc_page_no,
        document=page.document, snippet=snippet(page, start, end), method=method, confidence=confidence,
        page_id=str(page.page_id) if page.page_id else None,
    )


# ------------------------------------------------------------------------------------------------
# Parsers (input: ASCII-digit text)
# ------------------------------------------------------------------------------------------------
MR_MONTHS = {
    "जानेवारी": 1, "फेब्रुवारी": 2, "मार्च": 3, "एप्रिल": 4, "मे": 5, "जून": 6, "जुलै": 7,
    "ऑगस्ट": 8, "सप्टेंबर": 9, "ऑक्टोबर": 10, "नोव्हेंबर": 11, "डिसेंबर": 12,
}
EN_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

DATE_NUMERIC = r"(?<!\d)(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})(?!\d)"
DATE_MR = r"(?<!\d)(\d{1,2})\s+(" + "|".join(MR_MONTHS) + r"),?\s+(\d{4})"
DATE_EN = r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s-]+([A-Za-z]{3,9})[\s,-]+(\d{4})"
DATE_RE = re.compile(f"{DATE_NUMERIC}|{DATE_MR}|{DATE_EN}")


def _mk_date(y: int, m: int, d: int) -> dt.date | None:
    if y < 100:
        y += 2000 if y < 50 else 1900
    try:
        out = dt.date(y, m, d)
    except ValueError:
        return None
    return out if 1950 <= out.year <= 2100 else None


def date_from_match(m: re.Match) -> dt.date | None:
    g = m.groups()
    if g[0]:
        return _mk_date(int(g[2]), int(g[1]), int(g[0]))
    if g[3]:
        return _mk_date(int(g[5]), MR_MONTHS[g[4]], int(g[3]))
    mon = EN_MONTHS.get(g[7][:3].lower())
    return _mk_date(int(g[8]), mon, int(g[6])) if mon else None


def parse_date(s: str) -> dt.date | None:
    m = DATE_RE.search(s)
    return date_from_match(m) if m else None


AMOUNT_RE = re.compile(r"(?:रु\.?|रुपये|Rs\.?|INR|₹)\s*[:\-]?\s*(\d[\d,]*\d(?:\.\d{1,2})?|\d)(?!\d)\s*(?:/-)?", re.I)
BARE_AMOUNT_RE = re.compile(r"(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+\.\d{2})")


def parse_decimal(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def money(d: Decimal | None) -> Decimal | None:
    """Amounts and areas are carried with exactly two decimals."""
    return d.quantize(Decimal("0.01")) if d is not None else None


AREA_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(चौ\.?\s*मी\.?|चौरस\s*मीटर|sq\.?\s*m(?:trs?|eters?|etres?)?\b\.?|चौ\.?\s*फू\.?|चौरस\s*फूट|sq\.?\s*f(?:ee)?t\.?)",
    re.I,
)


def area_from_match(m: re.Match) -> Decimal | None:
    n = parse_decimal(m.group(1))
    if n is None:
        return None
    unit = m.group(2).lower()
    if "फू" in unit or "f" in unit:
        return money(n * SQFT_TO_SQM)
    return money(n)


CTS_LABEL = (
    r"(?:नगर\s*भूमापन\s*क्र(?:मांक)?\.?|न\.\s*भू\.\s*क्र(?:मांक)?\.?|सि\.\s*स\.\s*(?:क्र\.?|नं\.?)|"
    r"सी\.\s*टी\.\s*एस\.?\s*(?:क्र(?:मांक)?\.?|नं\.?)|C\.?\s*T\.?\s*S\.?\s*No\.?|City\s+Survey\s+No\.?)"
)
CTS_VALUE = r"(\d{1,6}(?:\s*/\s*[0-9A-Za-z\u0905-\u0939]{1,4}){0,3}(?:\s*(?:पै|pt)\.?)?)"
CTS_RE = re.compile(CTS_LABEL + r"\s*[:\-]?\s*" + CTS_VALUE, re.I)


def normalise_cts(s: str) -> str:
    s = re.sub(r"\s+", "", s).upper()
    s = re.sub(r"(पै|PT)\.?$", "PT", s)
    return s


SOCIETY_MR_RE = re.compile(r"((?:[\u0900-\u097f]+[^\S\n]+){1,4}सहकारी[^\S\n]+गृहनिर्माण[^\S\n]+संस्था(?:[^\S\n]+(?:मर्यादित|मर्या\.|लि\.))?)")
SOCIETY_EN_RE = re.compile(r"((?:[A-Z][A-Za-z]+[^\S\n]+){1,4}Co-?op(?:erative)?\.?[^\S\n]+Hou?s(?:ing)?\.?[^\S\n]+Soc(?:iety)?\.?(?:[^\S\n]+(?:Ltd\.?|Limited))?)")
SOCIETY_LEAD_STOP = {
    "अर्जदार", "आमची", "आमच्या", "आम्ही", "ही", "या", "सदर", "की", "व", "संस्थेचे", "नाव", "मौजे", "श्री",
    "धारकाचे", "मे.", "सभा", "प्रति", "सचिव", "अध्यक्ष", "येथील", "मधील", "भाडेपट्टाधारक", "पट्टेदार", "करिता", "रोजी", "आणि",
}


def clean_society(name: str) -> str:
    toks = name.split()
    while toks and (toks[0] in SOCIETY_LEAD_STOP or toks[0].rstrip(":") in SOCIETY_LEAD_STOP):
        toks.pop(0)
    return " ".join(toks)


def society_key(name: str | None) -> str:
    """Comparable form: drop suffixes and whitespace."""
    if not name:
        return ""
    n = re.sub(r"मर्यादित|मर्या\.|लि\.|ltd\.?|limited", "", name, flags=re.I)
    return re.sub(r"[\s.,]+", "", n).casefold()


# ------------------------------------------------------------------------------------------------
# Search helpers
# ------------------------------------------------------------------------------------------------
Parser = Callable[[re.Match], Any]


def _loose(raw: str) -> re.Pattern | None:
    needle = re.sub(r"\s+", "", ascii_digits(raw))
    return re.compile(r"\s*".join(re.escape(ch) for ch in needle)) if needle else None


def locate(raw: str, page: PageText) -> tuple[int, int] | None:
    """Find raw text on the page, tolerant to whitespace and digit script."""
    pattern = _loose(raw)
    m = pattern.search(page.ascii) if pattern else None
    return m.span() if m else None


def locate_near(raw: str, snippet: str | None, page: PageText) -> tuple[int, int] | None:
    """`locate`, picking the occurrence inside `snippet` (the context stored with the evidence) when the raw text
    is on the page more than once; the first occurrence when the snippet is not found."""
    pattern = _loose(raw)
    if pattern is None:
        return None
    around = locate(snippet, page) if snippet else None
    m = pattern.search(page.ascii, *around) if around else None
    m = m or pattern.search(page.ascii)
    return m.span() if m else None


def find_first(pages: list[PageText], pattern: re.Pattern, parse: Parser, group: int = 1, confidence: float = 0.75) -> Evidence | None:
    for p in pages:
        for m in pattern.finditer(p.ascii):
            v = parse(m)
            if v not in (None, ""):
                return make_evidence(p, m.start(group), m.end(group), v, confidence)
    return None


def find_labeled(pages: list[PageText], label: str, value: re.Pattern | str, parse: Parser, *, window: int = 80,
                 confidence: float = 0.8, last: bool = False, group: int | None = None) -> Evidence | None:
    """Find `label`, then the first `value` match within `window` chars after it. Evidence covers
    `group` of the value match (default: its first participating group, else the whole match)."""
    lab = re.compile(label, re.I)
    val = re.compile(value, re.I) if isinstance(value, str) else value
    found: list[Evidence] = []
    for p in pages:
        for lm in lab.finditer(p.ascii):
            vm = val.search(p.ascii, lm.end(), min(len(p.ascii), lm.end() + window))
            if vm:
                v = parse(vm)
                if v not in (None, ""):
                    g = group if group is not None else next(
                        (i for i in range(1, len(vm.groups()) + 1) if vm.group(i) is not None), 0)
                    ev = make_evidence(p, vm.start(g), vm.end(g), v, confidence)
                    if not last:
                        return ev
                    found.append(ev)
    return found[-1] if found else None


def find_all_dates(pages: list[PageText]) -> list[Evidence]:
    out = []
    for p in pages:
        for m in DATE_RE.finditer(p.ascii):
            d = date_from_match(m)
            if d:
                out.append(make_evidence(p, m.start(), m.end(), d, 0.6))
    return out
