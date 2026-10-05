"""Geometry first, then GPT reads each line box.

The OpenAI reader returns text only, and two failures are known on these pages: Devanagari digits get misread, and a
whole page sent at high detail is scaled so its short side is 768 px, which loses matras. So:

  1 geometry   line and word boxes from the PDF text layer (its positions are valid even when its glyphs are junk),
               else from the page image (projection-profile segmenter in PIL), else from an OCR engine's boxes;
  2 tiles      the page is cut into horizontal strips between lines, each short enough not to be downscaled much;
  3 read       variant A: the strip plus a copy with numbered line boxes, GPT returns the text of each id;
               variant B: GPT transcribes the strip line by line and a monotonic DP aligns the lines to the boxes
               (also the fallback when variant A returns missing or duplicated ids);
  4 words      a line whose whitespace words match its word boxes one to one gets word boxes;
  5 digits     on pages with a usable text layer, digit runs of each line are compared with the layer's line.

Box coordinates are 0..1 of the page image (x0, y0, x1, y1), as everywhere else.
"""
from __future__ import annotations

import io
import logging
import re
import statistics
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from rci.adapters.llm import DataClass, ImagePart, LLMClient, LLMError
from rci.adapters.ocr import BBox, OcrLine, OcrResult, OcrWord
from rci.pipeline.normalise import ascii_digits

log = logging.getLogger(__name__)

OPENAI_SHORT_SIDE = 768  # high detail: fit in 2048 x 2048, then the short side is scaled down to 768
OPENAI_MAX_SIDE = 2048
ENGINE = "gpt_lines"

_DIGITS = re.compile(r"\d+")


@dataclass
class GeomLine:
    bbox: BBox
    words: list[BBox] = field(default_factory=list)
    layer_text: str | None = None  # the text layer's glyphs for this line (may be junk on legacy / broken pages)


def _values(img: Image.Image) -> list[float]:
    flat = getattr(img, "get_flattened_data", None)
    return list(flat() if flat else img.getdata())


def _union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


# ------------------------------------------------------------------------------------------------
# 1 Geometry
# ------------------------------------------------------------------------------------------------
def merge_rows(lines: list[GeomLine]) -> list[GeomLine]:
    """Fragments on the same visual row (table cells, runs split by the PDF producer) become one line, left to right."""
    rows: list[list[GeomLine]] = []
    for g in sorted(lines, key=lambda g: ((g.bbox[1] + g.bbox[3]) / 2, g.bbox[0])):
        if rows:
            r = _union([p.bbox for p in rows[-1]])
            ov = min(r[3], g.bbox[3]) - max(r[1], g.bbox[1])
            h = min(r[3] - r[1], g.bbox[3] - g.bbox[1])
            if h > 0 and ov / h >= 0.5:
                rows[-1].append(g)
                continue
        rows.append([g])
    out = []
    for parts in rows:
        parts.sort(key=lambda p: p.bbox[0])
        texts = [p.layer_text for p in parts if p.layer_text]
        out.append(GeomLine(_union([p.bbox for p in parts]), [w for p in parts for w in p.words], " ".join(texts) or None))
    return out


def text_layer_geometry(page) -> list[GeomLine]:
    """Horizontal text-layer lines of a PyMuPDF page (rotated pages are left to the image segmenter)."""
    if page.rotation:
        return []
    W, H = page.rect.width, page.rect.height
    lines = []
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            if abs(ln.get("dir", (1, 0))[0]) < 0.9:
                continue
            chars = [c for s in ln["spans"] for c in s["chars"]]
            text = "".join(c["c"] for c in chars).strip()
            if not text:
                continue
            words, run = [], []
            for ch in chars + [{"c": " "}]:
                if ch["c"].strip():
                    run.append(ch["bbox"])
                elif run:
                    x0, y0, x1, y1 = _union(run)
                    words.append((x0 / W, y0 / H, x1 / W, y1 / H))
                    run = []
            x0, y0, x1, y1 = ln["bbox"]
            box = (max(0.0, x0 / W), max(0.0, y0 / H), min(1.0, x1 / W), min(1.0, y1 / H))
            if box[2] > box[0] and box[3] > box[1]:
                lines.append(GeomLine(box, words, text))
    return merge_rows(lines)


def _otsu(hist: list[int]) -> int:
    total = sum(hist)
    sum_all = sum(i * h for i, h in enumerate(hist))
    w_b = sum_b = 0
    best, thr = -1.0, 127
    for t in range(256):
        w_b += hist[t]
        if not w_b:
            continue
        w_f = total - w_b
        if not w_f:
            break
        sum_b += t * hist[t]
        between = w_b * w_f * (sum_b / w_b - (sum_all - sum_b) / w_f) ** 2
        if between > best:
            best, thr = between, t
    return thr


def _profile(ink: Image.Image, rows: bool) -> list[float]:
    """Mean ink (0..1) of each row (rows=True) or column of a 0/255 ink image."""
    W, H = ink.size
    small = ink.convert("F").resize((1, H) if rows else (W, 1), Image.BOX)
    return [v / 255 for v in _values(small)]


def _runs(on: list[bool]) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(on + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    return out


def _remove_rules(ink: Image.Image) -> Image.Image:
    """Erase long straight rules (table borders, scan edges): runs of solid ink far longer than any glyph stroke."""
    W, H = ink.size
    for k, vertical in ((80, True), (320, False)):
        if (H if vertical else W) < 2 * k:
            continue
        size = (W, max(1, H // k)) if vertical else (max(1, W // k), H)
        mask = ink.resize(size, Image.BOX).point(lambda v: 255 if v >= 200 else 0).resize((W, H), Image.NEAREST)
        ink = ImageChops.subtract(ink, mask.filter(ImageFilter.MaxFilter(5)))
    return ink


ROW_MIN = 0.004  # a row with more ink than this share of the width is part of a text line


def _split_tall(band: tuple[int, int], rows: list[float], med: float) -> list[tuple[int, int]]:
    y0, y1 = band
    if y1 - y0 <= 1.8 * med:
        return [band]
    lo, hi = int(y0 + 0.5 * med), int(y1 - 0.5 * med)
    if hi <= lo:
        return [band]
    cut = min(range(lo, hi), key=lambda y: rows[y])
    mean = sum(rows[y0:y1]) / (y1 - y0)
    if rows[cut] > 0.35 * mean:
        return [band]
    return _split_tall((y0, cut), rows, med) + _split_tall((cut + 1, y1), rows, med)


def word_gap_threshold(gaps: list[int], h: int) -> int | None:
    """Smallest gap that separates words in a line of height `h` px, or None (one word)."""
    if not gaps:
        return None
    values = sorted(set(gaps))
    best, thr = -1.0, None
    for t in values[1:]:
        lo = [g for g in gaps if g < t]
        hi = [g for g in gaps if g >= t]
        m_lo, m_hi = sum(lo) / len(lo), sum(hi) / len(hi)
        between = len(lo) * len(hi) * (m_hi - m_lo) ** 2
        if between > best:
            best, thr, ratio, hi_mean = between, t, m_hi / max(1.0, m_lo), m_hi
    if thr is not None and ratio >= 1.8 and hi_mean >= 0.12 * h and thr >= 3:
        return thr
    if min(gaps) >= 0.15 * h:
        return min(gaps)
    return None


def _words_in_band(ink: Image.Image, y0: int, y1: int) -> tuple[tuple[int, int] | None, list[tuple[int, int]]]:
    """(x extent, word x-ranges) of a line band. The column gaps are split in two classes (letter gaps, word gaps) at
    the threshold with the largest between-class variance; it counts when word gaps average at least 1.8× letter gaps
    and are space-sized. A line whose gaps are all space-sized splits at every gap; otherwise it stays one word."""
    W = ink.size[0]
    cols = _profile(ink.crop((0, y0, W, y1)), rows=False)
    runs = _runs([v > 0.5 / max(1, y1 - y0) for v in cols])
    if not runs:
        return None, []
    gaps = [b[0] - a[1] for a, b in zip(runs, runs[1:])]
    h = y1 - y0
    thr = word_gap_threshold(gaps, h)
    words, cur = [], list(runs[0])
    for a, b in zip(runs, runs[1:]):
        if thr is not None and b[0] - a[1] >= thr:
            words.append((cur[0], cur[1]))
            cur = [b[0], b[1]]
        else:
            cur[1] = b[1]
    words.append((cur[0], cur[1]))
    return (runs[0][0], runs[-1][1]), words


def image_geometry(png: bytes) -> list[GeomLine]:
    """Line and word boxes of a page image: Otsu binarisation, rules erased, row-ink bands (thin matra / dot bands
    merged into the neighbouring line, tall bands split at their faintest row), then column gaps inside each band."""
    img = Image.open(io.BytesIO(png)).convert("L")
    W, H = img.size
    thr = _otsu(img.histogram())
    ink = img.point(lambda v: 255 if v <= thr else 0).filter(ImageFilter.MedianFilter(3))
    ink = _remove_rules(ink)
    rows = _profile(ink, rows=True)
    bands = _runs([v > ROW_MIN for v in rows])
    if not bands:
        return []
    heights = [b - a for a, b in bands if b - a >= 4]
    med = statistics.median(heights) if heights else 10.0
    # thin bands: rule remnants are dropped, matras / dots join the nearest line within reach, specks are dropped
    merged: list[list[int]] = []
    for a, b in bands:
        h = b - a
        if h < 0.45 * med:
            mean = sum(rows[a:b]) / max(1, h)
            if h <= 6 and mean > 0.25:
                continue
            gap_up = a - merged[-1][1] if merged else None
            nxt = next(((x, y) for x, y in bands if x >= b and y - x >= 0.45 * med), None)
            gap_dn = nxt[0] - b if nxt else None
            if gap_up is not None and gap_up <= 0.6 * med and (gap_dn is None or gap_up <= gap_dn):
                merged[-1][1] = b
                continue
            if gap_dn is not None and gap_dn <= 0.6 * med:
                merged.append([a, b, 1])  # marked: to be joined with the next band
                continue
            if h < 0.25 * med:
                continue
        if merged and len(merged[-1]) == 3:
            merged[-1] = [merged[-1][0], b]
        else:
            merged.append([a, b])
    out = []
    for band in merged:
        for y0, y1 in _split_tall((band[0], band[1]), rows, med):
            extent, words = _words_in_band(ink, y0, y1)
            if extent is None or (extent[1] - extent[0]) < 0.01 * W:
                continue
            out.append(GeomLine((extent[0] / W, y0 / H, extent[1] / W, y1 / H),
                                [(a / W, y0 / H, b / W, y1 / H) for a, b in words]))
    return out


def page_geometry(page, png: bytes, text_status: str, *, engine: OcrResult | None = None) -> tuple[list[GeomLine], str]:
    """(lines, source). An OCR engine's boxes win when given; then the PDF text layer (any status but "none"); then
    the page image."""
    if engine is not None and any(l.bbox for l in engine.lines):
        return [GeomLine(l.bbox, [w.bbox for w in l.words if w.bbox]) for l in engine.lines if l.bbox], engine.engine
    if text_status != "none" and page is not None:
        lines = text_layer_geometry(page)
        if lines:
            return lines, "text_layer"
    return image_geometry(png), "image"


# ------------------------------------------------------------------------------------------------
# 2 Tiles
# ------------------------------------------------------------------------------------------------
@dataclass
class Tile:
    y0: int  # crop, px
    y1: int
    lines: list[int]  # indices into the page's geometry lines


def plan_tiles(lines: list[GeomLine], W: int, H: int, min_px: int) -> list[Tile]:
    """Horizontal strips short enough to be read at (close to) full resolution, at least `min_px` tall where the page
    allows. Every line belongs to exactly one strip (the one holding its centre); a strip is cropped to its lines."""
    fit = min(1.0, OPENAI_MAX_SIDE / max(1, W))
    max_h = OPENAI_SHORT_SIDE / fit
    n = max(1, -(-H // int(max_h)))
    if n > 1 and H / n < min_px:
        n = max(1, H // min_px)
    cores = [round(k * H / n) for k in range(n + 1)]
    heights = [(l.bbox[3] - l.bbox[1]) * H for l in lines]
    pad = int(0.25 * statistics.median(heights)) + 2 if heights else 4
    tiles = []
    for k in range(n):
        idx = [i for i, l in enumerate(lines) if cores[k] <= (l.bbox[1] + l.bbox[3]) / 2 * H < cores[k + 1]
               or (k == n - 1 and (l.bbox[1] + l.bbox[3]) / 2 * H >= cores[k + 1])]
        if not idx:
            continue
        y0 = max(0, int(min(lines[i].bbox[1] * H for i in idx)) - pad)
        y1 = min(H, int(max(lines[i].bbox[3] * H for i in idx)) + pad)
        if y1 - y0 < min_px and H >= min_px:  # grow a short strip around its centre (context only; ids say what to read)
            c = (y0 + y1) // 2
            y0 = max(0, min(c - min_px // 2, H - min_px))
            y1 = y0 + min_px
        tiles.append(Tile(y0, y1, idx))
    return tiles


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


MARGIN = 72


def annotate(tile_img: Image.Image, lines: list[GeomLine], ids: list[int], y0: int, H: int) -> Image.Image:
    """The strip with thin boxes around its lines and ids (L12) in a white left margin."""
    W, h = tile_img.size
    out = Image.new("RGB", (W + MARGIN, h), "white")
    out.paste(tile_img.convert("RGB"), (MARGIN, 0))
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.load_default(size=20)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    for i in ids:
        x0, ly0, x1, ly1 = lines[i].bbox
        box = [MARGIN + x0 * W - 2, ly0 * H - y0 - 2, MARGIN + x1 * W + 2, ly1 * H - y0 + 2]
        draw.rectangle(box, outline=(220, 30, 30), width=1)
        draw.text((4, (box[1] + box[3]) / 2 - 10), f"L{i + 1}", fill=(220, 30, 30), font=font)
    return out


# ------------------------------------------------------------------------------------------------
# 3 Read
# ------------------------------------------------------------------------------------------------
_RULES = (
    "- Transcribe exactly what is printed, in the language and script it is printed in. Never translate, "
    "transliterate, summarise, correct, complete or explain anything. Marathi stays Marathi in Devanagari; English stays "
    "English in Latin; a bilingual line keeps both.\n"
    "- The page may embed corrupted text from a legacy Marathi font. Read the page as it LOOKS: correctly spelled "
    "Marathi as printed (निर्णय, नोंदणी — never ननणणय, नोंिणी).\n"
    "- DIGITS DESERVE A SECOND LOOK. Read every numeral digit by digit, keep the script it is printed in (Devanagari "
    "०-९ or Latin 0-9) and keep separators exactly (05.07.2023, ३,२३,७०,४६०, १०%). Devanagari numerals are easily "
    "confused (५/६, ९/०, १/३, ७/९). Where a figure is not clearly legible write [अस्पष्ट] in its place instead of the "
    "closest-looking one.\n"
    "- Never reconstruct a name or a word you cannot read; write [अस्पष्ट] for it.\n"
    "- A table row is one line: transcribe its cells left to right, separated by spaces.\n"
)

SYSTEM_A = (
    "You transcribe one horizontal strip of a page of an official Government of Maharashtra document (a Government "
    "Resolution, gazette notification, circular or letter; mostly Marathi, some English).\n"
    "You get two images of the same strip: (1) the clean strip, (2) a copy where each text line is boxed in red and "
    "labelled with an id (L1, L2, ...) in the left margin. Read the text from the clean image; use the boxed copy only "
    "to know which line each id is.\n\n"
    "Return exactly one entry per id you are given, in id order, with the text printed inside that box.\n"
    "- Never join two boxes into one entry and never split one box across entries. A box with no text (a stamp, a "
    "rule, a smudge) gets \"\".\n"
    "- Text in the strip that lies in no box (e.g. a handwritten note) goes to `extra`, with the id of the box it "
    "follows (\"\" if it comes before the first box). Never repeat boxed text in `extra`. Lines cut off at the top or "
    "bottom edge of the strip belong to the neighbouring strip: skip them.\n\n" + _RULES
)

SYSTEM_B = (
    "You transcribe one horizontal strip of a page of an official Government of Maharashtra document (a Government "
    "Resolution, gazette notification, circular or letter; mostly Marathi, some English).\n"
    "Return every printed line of the strip, top to bottom, one entry per line. Never join two printed lines into one "
    "entry and never split one. Lines cut off at the top or bottom edge of the strip belong to the neighbouring strip: "
    "skip them.\n\n" + _RULES
)

SCHEMA_A = {
    "type": "object", "additionalProperties": False, "required": ["lines", "extra"],
    "properties": {
        "lines": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "text"],
                                             "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}},
        "extra": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["after_id", "text"],
                                             "properties": {"after_id": {"type": "string"}, "text": {"type": "string"}}}},
    },
}

SCHEMA_B = {
    "type": "object", "additionalProperties": False, "required": ["lines"],
    "properties": {"lines": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["text"],
                                                        "properties": {"text": {"type": "string"}}}}},
}


SKIP_TEXT, SKIP_BOX, MERGE = 0.5, 0.35, 0.1


def align(texts: list[str], widths: list[float]) -> list[list[int]]:
    """Monotonic alignment of transcribed lines to geometry lines (variant B). Each text gets [] (no box), [j] or
    [j, j+1] (one transcribed line over two boxes: the union box). Cost of a match: |expected width − box width| /
    widest box, the expected width being the text length × the page's mean width per character."""
    m, n = len(texts), len(widths)
    lens = [len(t.strip()) for t in texts]
    k = sum(widths) / max(1, sum(lens))
    maxw = max(widths, default=1.0) or 1.0

    def cost(i: int, js: tuple[int, ...]) -> float:
        if not lens[i]:
            return 1.0
        return min(1.0, abs(k * lens[i] - sum(widths[j] for j in js)) / maxw)

    INF = float("inf")
    D = [[INF] * (n + 1) for _ in range(m + 1)]
    back: list[list[tuple | None]] = [[None] * (n + 1) for _ in range(m + 1)]
    D[0][0] = 0.0
    for i in range(m + 1):
        for j in range(n + 1):
            if D[i][j] == INF:
                continue
            cands = []
            if i < m and j < n:
                cands.append((i + 1, j + 1, D[i][j] + cost(i, (j,)), (j,)))
            if i < m and j + 1 < n:
                cands.append((i + 1, j + 2, D[i][j] + cost(i, (j, j + 1)) + MERGE, (j, j + 1)))
            if i < m:
                cands.append((i + 1, j, D[i][j] + SKIP_TEXT, ()))
            if j < n:
                cands.append((i, j + 1, D[i][j] + SKIP_BOX, None))
            for a, b, c, js in cands:
                if c < D[a][b]:
                    D[a][b] = c
                    back[a][b] = (i, j, js)
    out: list[list[int]] = [[] for _ in range(m)]
    i, j = m, n
    while (i, j) != (0, 0):
        pi, pj, js = back[i][j]
        if js is not None and pi < i:
            out[pi] = list(js)
        i, j = pi, pj
    return out


@dataclass
class PageRead:
    ocr: OcrResult
    flags: dict
    confidence: float
    aligned_share: float
    tiles: int


def _digit_runs(s: str | None) -> list[str]:
    return [d.lstrip("0") or "0" for d in _DIGITS.findall(ascii_digits(s or ""))]


def _read_tile_a(llm: LLMClient, clean: bytes, boxed: bytes, ids: list[int], label: str, data_class: DataClass):
    want = [f"L{i + 1}" for i in ids]
    out = llm.complete_json(
        task="library_ocr", system=SYSTEM_A, schema=SCHEMA_A, schema_name="line_transcription", data_class=data_class,
        user=f"{label}. Boxes in this strip: {', '.join(want)}. Return exactly these ids.",
        images=[ImagePart(clean), ImagePart(boxed)],
    )
    got = [l["id"].strip() for l in out["lines"]]
    if sorted(got) != sorted(want):  # missing / duplicated / foreign ids: align the texts instead
        texts = [l["text"] for l in out["lines"]]
        for e in out["extra"]:
            at = next((n for n, l in enumerate(out["lines"]) if l["id"].strip() == e["after_id"].strip()), -1)
            texts.insert(at + 1, e["text"])
        return None, texts
    by_id = {l["id"].strip(): l["text"] for l in out["lines"]}
    extra: dict[int, list[str]] = {}
    for e in out["extra"]:
        aid = e["after_id"].strip()
        key = int(aid[1:]) - 1 if aid in by_id else -1
        if e["text"].strip():
            extra.setdefault(key, []).append(e["text"])
    return {i: by_id[f"L{i + 1}"] for i in ids}, extra


def _read_tile_b(llm: LLMClient, clean: bytes, label: str, data_class: DataClass) -> list[str]:
    out = llm.complete_json(task="library_ocr", system=SYSTEM_B, schema=SCHEMA_B, schema_name="strip_transcription",
                            data_class=data_class, user=f"{label}. Transcribe this strip.", images=[ImagePart(clean)])
    return [l["text"] for l in out["lines"]]


def _words(text: str, boxes: list[BBox]) -> list[OcrWord]:
    toks = text.split()
    return [OcrWord(t, b) for t, b in zip(toks, boxes)] if toks and len(toks) == len(boxes) else []


def read_page(llm: LLMClient, png: bytes, geom: list[GeomLine], *, check_digits: bool = False, data_class: DataClass = "public",
              variant: str = "A", min_px: int = 700, label: str = "Page") -> PageRead:
    """Read a page line by line over its geometry. `check_digits`: the geometry came from a text layer whose digits can
    be trusted (status unicode / broken), so digit runs are compared per line."""
    img = Image.open(io.BytesIO(png))
    W, H = img.size
    if not geom:  # nothing segmented: one plain read of the page, lines without boxes
        texts = _read_tile_b(llm, png, label, data_class)
        lines = [OcrLine(t) for t in texts if t.strip()]
        return PageRead(OcrResult(ENGINE, lines, 0.0), {"tiles": 1, "unaligned_lines": len(lines), "digit_conflicts": [],
                                                         "no_geometry": True}, 0.0, 0.0, 1)
    tiles = plan_tiles(geom, W, H, min_px)
    text_of: dict[int, str] = {}  # geometry index -> text (merged pairs: on the first index)
    merged_with: dict[int, int] = {}
    extras: dict[int, list[str]] = {}  # after geometry index (-1: before the first) -> texts without a box
    fallbacks = 0
    for n, t in enumerate(tiles, start=1):
        crop = img.crop((0, t.y0, W, t.y1))
        clean = _png(crop)
        tl = f"{label}, strip {n} of {len(tiles)}"
        mapping, rest = (None, None)
        if variant == "A":
            mapping, rest = _read_tile_a(llm, clean, _png(annotate(crop, geom, t.lines, t.y0, H)), t.lines, tl, data_class)
            if mapping is None:
                fallbacks += 1
        else:
            rest = _read_tile_b(llm, clean, tl, data_class)
        if mapping is not None:
            text_of.update(mapping)
            for k, v in rest.items():
                extras.setdefault(k, []).extend(v)
            continue
        widths = [geom[i].bbox[2] - geom[i].bbox[0] for i in t.lines]
        last = t.lines[0] - 1
        for txt, js in zip(rest, align(rest, widths)):
            if not js:
                if txt.strip():
                    extras.setdefault(last, []).append(txt)
                continue
            gi = [t.lines[j] for j in js]
            text_of[gi[0]] = txt
            if len(gi) == 2:
                merged_with[gi[0]] = gi[1]
            last = gi[-1]

    lines: list[OcrLine] = [OcrLine(x) for x in extras.get(-1, [])]
    conflicts, aligned, boxes_without_text = [], 0, 0
    skip: set[int] = set(merged_with.values())
    for i, g in enumerate(geom):
        if i in skip:
            continue
        txt = text_of.get(i, "")
        if txt.strip():
            parts = [g] + ([geom[merged_with[i]]] if i in merged_with else [])
            bbox = _union([p.bbox for p in parts])
            words = _words(txt, [w for p in parts for w in p.words])
            if check_digits:
                layer = " ".join(p.layer_text or "" for p in parts)
                if _digit_runs(txt) != _digit_runs(layer) and (_DIGITS.search(ascii_digits(txt)) or _DIGITS.search(ascii_digits(layer))):
                    conflicts.append({"line": len(lines), "gpt": txt, "layer": layer})
            lines.append(OcrLine(txt, tuple(round(v, 5) for v in bbox), None, words))
            aligned += 1
        else:
            boxes_without_text += 1
        lines += [OcrLine(x) for x in extras.get(i, [])]
    n_text = sum(1 for l in lines if l.text.strip())
    share = aligned / n_text if n_text else 0.0
    conf = round(share * (1 - (len(conflicts) / aligned if aligned else 0.0)), 4)
    flags = {"tiles": len(tiles), "digit_conflicts": conflicts, "unaligned_lines": n_text - aligned,
             "boxes_without_text": boxes_without_text, "variant": variant, "variant_a_fallbacks": fallbacks}
    return PageRead(OcrResult(ENGINE, lines, conf), flags, conf, round(share, 4), len(tiles))


def safe_read_page(llm: LLMClient, png: bytes, geom: list[GeomLine], **kw) -> PageRead:
    """read_page that never raises: a failed page comes back empty with flags.read_failed (the document still ingests)."""
    try:
        return read_page(llm, png, geom, **kw)
    except (LLMError, Exception) as e:  # noqa: BLE001 - any reader failure costs the page, not the document
        log.warning("library reader failed on %s: %s", kw.get("label", "page"), e)
        return PageRead(OcrResult(ENGINE, [], 0.0), {"read_failed": str(e)[:300], "tiles": 0, "digit_conflicts": []}, 0.0, 0.0, 0)
