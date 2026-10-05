"""Pattern recognisers for Indian PII. All functions run on the ASCII-digit shadow text, so a number
written in Devanagari digits (२३४५ ६७८९ ...) is found exactly like its ASCII form, at the same offsets.

Each recogniser returns Spans; `presidio_engine.py` wraps them as Presidio recognisers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rci.pipeline.redact import verhoeff


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity: str
    score: float
    source: str = "pattern"


def _context(text: str, start: int, end: int, words: re.Pattern, window: int = 60) -> bool:
    return bool(words.search(text[max(0, start - window) : min(len(text), end + window)]))


_SEP = r"[ \-]?"
# Aadhaar (4-4-4) and VID (4-4-4-4) must not be a slice of a longer 4-digit-group run, but may sit next to
# other numbers in a table row ("... 2345 6789 0124 9822012345").
_NO_DIGIT_BEFORE = r"(?<!\d)(?<!\d{4}[ \-])"
_NO_DIGIT_AFTER = r"(?!\d)(?![ \-]\d{4}(?!\d))"

AADHAAR_RE = re.compile(_NO_DIGIT_BEFORE + r"([2-9]\d{3}" + _SEP + r"\d{4}" + _SEP + r"\d{4})" + _NO_DIGIT_AFTER)
AADHAAR_CTX = re.compile(r"आधार|aadh?aa?r|uidai|\buid\b|यूआयडी", re.I)
VID_RE = re.compile(_NO_DIGIT_BEFORE + r"(\d{4}" + _SEP + r"\d{4}" + _SEP + r"\d{4}" + _SEP + r"\d{4})" + _NO_DIGIT_AFTER)
VID_CTX = re.compile(r"\bVID\b|virtual\s*id|आभासी|व्हीआयडी", re.I)
MOBILE_RE = re.compile(r"(?<![\dA-Za-z+])((?:\+91[ \-]?|0091[ \-]?|0)?[6-9]\d{4}[ \-]?\d{5})(?![\dA-Za-z])")
MOBILE_CTX = re.compile(r"मो\.|मोबा|मोबाईल|मोबाइल|भ्रमणध्वनी|दूरध्वनी|फोन|संपर्क|mobile|mob\.?|phone|ph\.|tel\.?|contact|whatsapp", re.I)
PAN_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{3}[ABCFGHLJPTK][A-Z]\d{4}[A-Z])(?![A-Z0-9])")
EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
IFSC_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{4}0[A-Z0-9]{6})(?![A-Z0-9])")
ACCOUNT_RE = re.compile(r"(?<![\dA-Za-z])(\d{9,18})(?![\dA-Za-z])")
ACCOUNT_CTX = re.compile(r"खाते|खाता|a/?c\b|acct|account|बँक|bank", re.I)


def find_aadhaar(text: str) -> list[Span]:
    spans = []
    for m in AADHAAR_RE.finditer(text):
        s, e = m.span(1)
        if verhoeff.is_valid(m.group(1)):
            spans.append(Span(s, e, "AADHAAR", 0.95, "aadhaar_verhoeff"))
        elif _context(text, s, e, AADHAAR_CTX):
            # Checksum fails (OCR error or typo) but it is labelled as Aadhaar: still redact.
            spans.append(Span(s, e, "AADHAAR", 0.7, "aadhaar_context"))
    return spans


def find_vid(text: str) -> list[Span]:
    spans = []
    for m in VID_RE.finditer(text):
        s, e = m.span(1)
        if verhoeff.is_valid(m.group(1)) or _context(text, s, e, VID_CTX):
            spans.append(Span(s, e, "AADHAAR_VID", 0.9, "vid"))
    return spans


def find_mobile(text: str) -> list[Span]:
    spans = []
    for m in MOBILE_RE.finditer(text):
        s, e = m.span(1)
        spans.append(Span(s, e, "PHONE", 0.85 if _context(text, s, e, MOBILE_CTX) else 0.6, "mobile"))
    return spans


def find_pan(text: str) -> list[Span]:
    return [Span(*m.span(1), "PAN", 0.9, "pan") for m in PAN_RE.finditer(text)]


def find_email(text: str) -> list[Span]:
    return [Span(*m.span(1), "EMAIL", 0.95, "email") for m in EMAIL_RE.finditer(text)]


def find_bank_account(text: str, window: int = 150) -> list[Span]:
    """Account numbers are only redacted when an IFSC code or account wording is nearby; a bare
    9-18 digit number is too often a challan/GRN/inward number."""
    ifsc_pos = [m.start(1) for m in IFSC_RE.finditer(text)]
    spans = []
    for m in ACCOUNT_RE.finditer(text):
        s, e = m.span(1)
        near_ifsc = any(abs(p - s) <= window for p in ifsc_pos)
        if near_ifsc or _context(text, s, e, ACCOUNT_CTX, window=40):
            spans.append(Span(s, e, "BANK_ACCOUNT", 0.85 if near_ifsc else 0.65, "bank_account"))
    return spans


PATTERN_RECOGNISERS = {
    "AADHAAR": find_aadhaar,
    "AADHAAR_VID": find_vid,
    "PHONE": find_mobile,
    "PAN": find_pan,
    "EMAIL": find_email,
    "BANK_ACCOUNT": find_bank_account,
}
