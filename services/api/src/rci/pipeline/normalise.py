"""Text normalisation: NFC + whitespace cleanup, and a length-preserving ASCII-digit shadow copy.

The shadow copy replaces Devanagari digits (०-९) with ASCII (0-9) one-for-one, so offsets in the
shadow are offsets in the original. Regexes (PII, extraction) run on the shadow; snippets are cut
from the original.
"""
from __future__ import annotations

import re
import unicodedata

DEVANAGARI_DIGITS = "०१२३४५६७८९"
_TO_ASCII = str.maketrans(DEVANAGARI_DIGITS, "0123456789")
_TO_DEV = str.maketrans("0123456789", DEVANAGARI_DIGITS)

# Zero-width chars, including ZWJ/ZWNJ: they only steer conjunct rendering (OCR emits र\u094d\u200dक for रक),
# and dropping them makes the same word match regardless of the engine. Raw OCR is kept in blob storage.
_ZW = re.compile("[\u200b-\u200d\u2060\ufeff\u00ad]")
_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"})
_SPACES = re.compile(r"[ \t\u00a0\u2000-\u200a\u202f\u3000]+")


def normalise_text(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = _ZW.sub("", t).translate(_DASHES)
    lines = [_SPACES.sub(" ", ln).strip() for ln in t.split("\n")]
    out: list[str] = []
    for ln in lines:  # collapse runs of blank lines to one
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def ascii_digits(text: str) -> str:
    shadow = text.translate(_TO_ASCII)
    assert len(shadow) == len(text)
    return shadow


def devanagari_digits(text: str) -> str:
    return text.translate(_TO_DEV)
