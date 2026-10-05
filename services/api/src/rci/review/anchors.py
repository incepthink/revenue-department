"""Where a span of a page's redacted text sits on the page image: boxes over the quoted words.

The redacted text and the OCR lines line up (rci.review.pageview.redacted_lines): non-empty line j of the text is OCR
line idx[j]. A span becomes one piece per text line it touches, and each piece one box. With word geometry in the OCR
blob the box is the union of the shortest run of words that spells the piece (compared without spaces, digits in ASCII);
otherwise — or when the piece covers the whole line or holds a PII token, whose words cannot be compared — it is the
line's box. Pages read before word geometry was kept therefore get line-precision boxes until they are read again.
"""
from __future__ import annotations

import re

from rci.adapters.ocr import BBox
from rci.pipeline.normalise import ascii_digits, normalise_text
from rci.pipeline.redact.vault import TOKEN_RE
from rci.review.pageview import redacted_lines

_WS = re.compile(r"\s+")


def key(s: str) -> str:
    """Comparable form: normalised, ASCII digits, no whitespace."""
    return _WS.sub("", ascii_digits(normalise_text(s or "")))


def _union(boxes: list) -> BBox | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _word_run(words: list[dict], piece: str, near: int) -> BBox | None:
    """The union box of the shortest run of words whose joined keys contain key(piece); among equally short runs the
    one starting nearest to key offset `near` (the piece's position in the line)."""
    want = key(piece)
    if not want:
        return None
    keys = [key(w.get("text") or "") for w in words]
    starts, pos = [], 0
    for k in keys:
        starts.append(pos)
        pos += len(k)
    best: tuple[int, int, int, int] | None = None  # (length, distance, i, k)
    for i in range(len(words)):
        acc = ""
        for k in range(i, len(words)):
            acc += keys[k]
            if want in acc:
                cand = (k - i + 1, abs(starts[i] - near), i, k)
                if best is None or cand < best:
                    best = cand
                break
            if best is not None and k - i + 1 >= best[0]:
                break
    if best is None:
        return None
    return _union([words[x].get("bbox") for x in range(best[2], best[3] + 1)])


def _round(b) -> list[float]:
    return [round(float(v), 5) for v in b]


def boxes_for_span(ocr_json: dict | None, text: str, start: int, end: int) -> tuple[list[list[float]], list[int], str | None]:
    """(boxes, OCR line indices, precision "word" | "line") of text[start:end] — `text` is the page's redacted text.
    Precision is "word" when every box is exact (a word run, or a whole line quoted in full), else "line".
    Boxes are empty and precision None when the page has no usable geometry (no OCR blob, lines without boxes, text
    not aligned with the OCR lines); the line indices are still given whenever the text is aligned."""
    rows, aligned = redacted_lines(ocr_json, text)
    if not aligned or end <= start:
        return [], [], None
    ocr_lines = (ocr_json or {}).get("lines") or []
    idx = [r["index"] for r in rows if r["text"] is not None]
    pieces: list[tuple[int, str, int, int]] = []  # (OCR line index, line text, local start, local end)
    offset, j = 0, 0
    for line in text.split("\n"):
        ls, le = offset, offset + len(line)
        offset = le + 1
        if not line.strip():
            continue
        a, b = max(start, ls), min(end, le)
        if a < b and line[a - ls:b - ls].strip():
            pieces.append((idx[j], line, a - ls, b - ls))
        j += 1
    if not pieces:
        return [], [], None
    lines = [p[0] for p in pieces]
    boxes: list[list[float]] = []
    precision = "word"
    for i, line, a, b in pieces:
        ocr = ocr_lines[i]
        piece = line[a:b]
        words = ocr.get("words") or []
        whole = key(piece) == key(line)  # the line box is then exact
        box = None
        if words and not whole and not TOKEN_RE.search(piece):
            box = _word_run(words, piece, len(key(line[:a])))
        if box is None:
            precision = precision if whole else "line"
            box = ocr.get("bbox")
        if not box:
            return [], lines, None
        boxes.append(_round(box))
    return boxes, lines, precision
