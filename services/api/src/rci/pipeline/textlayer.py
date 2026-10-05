"""Decide whether a PDF page's embedded text layer can be trusted or OCR must be forced.

Marathi government PDFs often carry a text layer that is *not* Unicode Devanagari:
  * legacy 8-bit fonts (Shree-Lipi, Kruti Dev, DVB-TT Surekh, APS, Akruti, ...): Devanagari glyphs
    mapped onto Latin-1 code points, so the text extracts as "egkjk"Vª 'kklu";
  * broken ToUnicode maps from shaped Unicode fonts (Word/Mangal/Nirmala exports): conjuncts come out as
    Latin Extended code points (Ţ Ũ Ɨ ŵ) and pre-base matras precede their consonant ("िळकत").
Either way the text layer is unusable and the page goes to OCR.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

Status = Literal["none", "unicode", "legacy", "broken"]

LEGACY_FONT_RE = re.compile(
    r"kruti|shree[-_ ]?(dev|lipi)|shreelipi|dvb[-_ ]?tt|dv[-_ ]?tt|dvot|aps[-_ ]?(dv|c-dv)|akruti|surekh|"
    r"yogesh|sulekha|chanakya|walkman|kiran|shivaji|amruta|shusha|devlys|narad|ism[-_ ]?dv|agra|susha",
    re.I,
)
UNICODE_DEVANAGARI_FONTS = re.compile(r"mangal|nirmala|kokila|utsaah|aparajita|noto|lohit|gargi|kalimati|sakal|annapurna|mukta|hind|arial ?unicode", re.I)

DEVANAGARI = re.compile(r"[\u0900-\u0963\u0971-\u097f]")  # letters and signs, not digits/danda
LETTER = re.compile(r"[^\W\d_]", re.U)
LATIN1_LEGACY = re.compile(r"[¡-ÿŒœŠšŸŽžƒˆ˜–-•…‰‹›™]")
LATIN_EXT_IN_DEV = re.compile(r"(?<=[\u0900-\u097f])[Ā-ʯ]|[Ā-ʯ](?=[\u0900-\u097f])")
ORPHAN_MATRA = re.compile(r"(?:^|(?<=\s))[\u093e-\u094d\u0962\u0963]", re.M)
MIXED_CASE_WORD = re.compile(r"\b[a-z]+[A-Z][A-Za-z]*\b")
LATIN_WORD = re.compile(r"\b[A-Za-z]{2,}\b")

MIN_CHARS = 20


@dataclass
class TextLayerReport:
    status: Status
    reason: str
    chars: int
    devanagari_ratio: float
    legacy_char_ratio: float
    broken_ratio: float
    mixed_case_ratio: float
    legacy_fonts: list[str]

    @property
    def use_text_layer(self) -> bool:
        return self.status == "unicode"

    def to_json(self) -> dict:
        return asdict(self)


def assess_text_layer(text: str, font_names: list[str] | None = None) -> TextLayerReport:
    font_names = font_names or []
    stripped = re.sub(r"\s+", "", text or "")
    n = len(stripped)
    letters = LETTER.findall(text or "")
    nl = max(1, len(letters))
    dev = len(DEVANAGARI.findall(text or ""))
    dev_ratio = dev / nl
    legacy_chars = len(LATIN1_LEGACY.findall(text or "")) / max(1, n)
    broken = (len(LATIN_EXT_IN_DEV.findall(text or "")) + len(ORPHAN_MATRA.findall(text or ""))) / max(1, dev)
    latin_words = LATIN_WORD.findall(text or "")
    mixed = len(MIXED_CASE_WORD.findall(text or "")) / max(1, len(latin_words))
    legacy_fonts = sorted({f for f in font_names if LEGACY_FONT_RE.search(f) and not UNICODE_DEVANAGARI_FONTS.search(f)})

    def report(status: Status, reason: str) -> TextLayerReport:
        return TextLayerReport(status, reason, n, round(dev_ratio, 4), round(legacy_chars, 4), round(broken, 4), round(mixed, 4), legacy_fonts)

    if n < MIN_CHARS or "(cid:" in (text or ""):
        return report("none", "no usable text layer")
    if "�" in text:
        return report("broken", "replacement characters in text layer")
    if legacy_fonts and dev_ratio < 0.5:
        return report("legacy", f"legacy non-Unicode Devanagari font(s): {', '.join(legacy_fonts)}")
    if dev > 0 and broken > 0.02:
        return report("broken", "Devanagari text layer with broken glyph-to-Unicode mapping")
    if dev_ratio < 0.2 and (legacy_chars > 0.03 or mixed > 0.15):
        return report("legacy", "Latin-1 glyph soup typical of legacy Devanagari fonts")
    return report("unicode", "Unicode Devanagari/Latin text layer")
