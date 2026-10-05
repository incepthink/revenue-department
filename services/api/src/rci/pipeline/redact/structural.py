"""Structural personal-name redaction for Marathi/English dockets.

No NER model handles Marathi names reliably, so names are found by document structure:
  1. office-bearer / role-labelled lines   "अध्यक्ष : श्री. रमेश गणपत कुलकर्णी", "Secretary - Smt. S. Patil"
  2. member lists                          rows under "सभासदांची यादी" / "List of Members"
  3. honorific-prefixed names anywhere     "श्रीमती सुनिता देशमुख", "Shri R. G. Kulkarni"
Society / institution names (…सहकारी गृहनिर्माण संस्था मर्यादित, … Ltd) are never redacted.
"""
from __future__ import annotations

import re

from rci.pipeline.redact.recognisers import Span

DEV_WORD = r"[\u0900-\u0963\u0971-\u097f]+\.?"
LAT_WORD = r"[A-Z][a-zA-Z]*\.?"
INSTITUTION = re.compile(
    r"संस्था|सोसायटी|मर्यादित|मर्या\.|लिमिटेड|कार्यालय|शासन|बँक|society|ltd|limited|bank|office|government|co-?op",
    re.I,
)
STOP_TOKENS = {
    "यांनी", "यांचे", "यांच्या", "यांची", "यांना", "यांचा", "यांस", "हे", "ही", "व", "आणि", "अध्यक्ष",
    "उपाध्यक्ष", "सचिव", "खजिनदार", "सभासद", "राहणार", "वय", "तहसीलदार", "जिल्हाधिकारी", "मंडळ",
    "अधिकारी", "तलाठी", "उपजिल्हाधिकारी", "नगर", "भूमापन", "सही", "हजर", "उपस्थित", "सूचक",
    "अनुमोदक", "सदनिका", "फ्लॅट", "आधार", "मोबाईल", "मो.", "पॅन", "यादी", "नाव", "क्र.", "क्रमांक",
    "सहकारी", "गृहनिर्माण", "संस्था", "व्यवसाय", "करिता",
    "Chairman", "Secretary", "Treasurer", "President", "and", "Flat", "Mobile", "Aadhaar", "PAN",
}
HONORIFIC = re.compile(
    r"(?<![\u0900-\u097f\w])(?P<h>श्रीमती|श्री\.?|सौ\.|कु\.|डॉ\.|Shri\.?|Smt\.?|Mr\.|Mrs\.|Ms\.|Kum\.|Dr\.)\s*"
)
NAME_TOKEN = re.compile(rf"[^\S\n]*({DEV_WORD}|{LAT_WORD})(?=[\s,;:)|/]|$)")
ROLE_LINE = re.compile(
    r"(?P<label>(?:अध्यक्ष|उपाध्यक्ष|सचिव|खजिनदार|चेअरमन|सेक्रेटरी|सूचक|अनुमोदक|साक्षीदार|सभासदाचे नाव|अर्जदाराचे नाव|"
    r"धारकाचे नाव|धारक|लिहून देणार|लिहून घेणार|Chairman|Secretary|Treasurer|President|Proposer|Seconder|Witness|"
    r"Name of (?:the )?(?:member|holder|deponent))\s*[:\-]\s*)(?P<name>[^\n,;()|]+)",
    re.I,
)
INSTITUTION_AHEAD = re.compile(r"[^\S\n]*(?:सहकारी|गृहनिर्माण|संस्था|co-?op|housing)", re.I)
MEMBER_LIST_HEADER = re.compile(r"सभासदांची\s+यादी|सभासद\s+यादी|सभासदांची\s+नावे|list\s+of\s+members|members?\s+list", re.I)
BLOCK_END = re.compile(r"^(?:सही|ठिकाण|दिनांक|स्थळ|Place|Date|Signature)\b|प्रमाणित करण्यात येते|certified", re.I)
ROW = re.compile(r"^(?P<lead>\s*[\d\u0966-\u096f]{1,4}\s*[.)|]?\s+)(?P<rest>.+)$")
NAME_LIKE_LINE = re.compile(rf"^\s*(?:{DEV_WORD}|{LAT_WORD})(?:\s+(?:{DEV_WORD}|{LAT_WORD})){{1,4}}\s*$")


def _take_name(text: str, pos: int, max_tokens: int = 4) -> int:
    """Consume up to max_tokens name tokens starting at pos; return end offset (== pos if none)."""
    end, n = pos, 0
    while n < max_tokens:
        m = NAME_TOKEN.match(text, end)
        if not m or m.group(1) in STOP_TOKENS or m.group(1).rstrip(".") in STOP_TOKENS:
            break
        if "\n" in text[end : m.start(1)]:
            break
        end, n = m.end(1), n + 1
    return end


def find_honorific_names(text: str) -> list[Span]:
    spans = []
    for m in HONORIFIC.finditer(text):
        end = _take_name(text, m.end())
        # "श्री गणेश कृपा सहकारी गृहनिर्माण संस्था" is an institution, not a person
        if end > m.end() and not INSTITUTION.search(text[m.end() : end]) and not INSTITUTION_AHEAD.match(text, end):
            spans.append(Span(m.end(), end, "PERSON", 0.75, "honorific"))
    return spans


def find_role_lines(text: str) -> list[Span]:
    spans = []
    for m in ROLE_LINE.finditer(text):
        name = m.group("name")
        s = m.start("name") + (len(name) - len(name.lstrip()))
        e = m.end("name") - (len(name) - len(name.rstrip()))
        value = text[s:e]
        if not value or INSTITUTION.search(value) or len(value.split()) > 6 or re.fullmatch(r"[\W\d_]+", value):
            continue
        spans.append(Span(s, e, "PERSON", 0.8, "role_line"))
    return spans


def find_member_list_names(text: str) -> list[Span]:
    spans: list[Span] = []
    lines = text.split("\n")
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1
    in_block = False
    for i, ln in enumerate(lines):
        if MEMBER_LIST_HEADER.search(ln):
            in_block = True
            continue
        if not in_block:
            continue
        if BLOCK_END.search(ln.strip()):
            in_block = False
            continue
        base = offsets[i]
        row = ROW.match(ln)
        if row:
            rest_start = base + row.start("rest")
            end = _take_name(text, rest_start, max_tokens=5)
            if end > rest_start and not INSTITUTION.search(text[rest_start:end]):
                spans.append(Span(rest_start, end, "PERSON", 0.85, "member_list"))
        elif NAME_LIKE_LINE.match(ln) and not INSTITUTION.search(ln) and not any(t in STOP_TOKENS for t in ln.split()):
            s = base + (len(ln) - len(ln.lstrip()))
            spans.append(Span(s, base + len(ln.rstrip()), "PERSON", 0.7, "member_list"))
    return spans


def find_structural_names(text: str) -> list[Span]:
    return find_role_lines(text) + find_member_list_names(text) + find_honorific_names(text)
