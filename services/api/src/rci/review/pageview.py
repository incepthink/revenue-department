"""Page viewer support: OCR lines with *redacted* text and bounding boxes, and PII-masked page images.

The blob OCR JSON holds un-redacted lines; the page row holds the redacted page text. Normalising each OCR
line on its own gives exactly the non-empty lines of the normalised page text (normalise_text works line by
line), and redaction replaces spans without touching newlines, so non-empty line i of the redacted text is
OCR line i with its PII replaced by tokens. When the counts disagree the alignment is abandoned and every
line is treated as containing PII (masked images then black out all text lines).
"""
from __future__ import annotations

import io
import json

from PIL import Image, ImageDraw

from rci.pipeline.normalise import normalise_text
from rci.pipeline.redact.vault import TOKEN_RE


def redacted_lines(ocr_json: dict | None, text_redacted: str | None) -> tuple[list[dict], bool]:
    """[{index, bbox, confidence, text, pii}], aligned. `text` is redacted (None when not aligned)."""
    lines = (ocr_json or {}).get("lines") or []
    norm = [normalise_text(l.get("text") or "") for l in lines]
    red = [t for t in (text_redacted or "").split("\n") if t.strip()]
    idx = [i for i, t in enumerate(norm) if t]
    aligned = len(idx) == len(red)
    out = []
    j = 0
    for i, l in enumerate(lines):
        row = {"index": i, "bbox": l.get("bbox"), "confidence": l.get("confidence"), "text": None, "pii": True}
        if aligned and norm[i]:
            row["text"] = red[j]
            row["pii"] = bool(TOKEN_RE.search(red[j]))
            j += 1
        elif aligned:
            row["pii"] = False
        out.append(row)
    return out, aligned


def render_page(png: bytes, lines: list[dict] | None, *, masked: bool, width: int | None = None, quality: int = 82) -> bytes:
    """JPEG of the page, optionally downscaled to `width` px, with PII lines blacked out when `masked`."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if masked and lines:
        draw = ImageDraw.Draw(img)
        W, H = img.size
        for l in lines:
            if l["pii"] and l.get("bbox"):
                x0, y0, x1, y1 = l["bbox"]
                pad = 0.002
                draw.rectangle([(x0 - pad) * W, (y0 - pad) * H, (x1 + pad) * W, (y1 + pad) * H], fill=(20, 20, 20))
    if width and width < img.width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def load_ocr(blobs, key: str | None) -> dict | None:
    if not key:
        return None
    return json.loads(blobs.get("ocr", key).decode("utf-8"))
