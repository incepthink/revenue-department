"""Provisions: a document's own structure (numbered paragraphs, clauses, table rows, rules, references, the order, the
signature block) as the unit of search, each with its text spans on the pages and one anchor per span.

Pure: input is the normalised page texts ([(page_no, text)]); patterns run on the ASCII-digit shadow of each line and
labels are cut from the line as printed ("३", "३(अ)", "तक्ता (ब) अ.क्र. ५", "नियम ५").

  ^\\d+[.)]                         paragraph (a rule / section line "नियम ५" too)
  ^(अ) / अ) / (a)                   clause of the current paragraph
  ^(\\d+)                           sub-clause
  तक्ता (ब) header, then ^\\d+ rows   table_row, labelled "तक्ता (ब) अ.क्र. ५" (rows run 1, 2, 3, ...)
  ^संदर्भ ... (numbered items)       reference, until the preamble or the order
  ^प्रस्तावना                        preamble
  "शासन निर्णय :" alone on a line    order (heads the operative part)
  (राज्यपाल ...) आदेशानुसार व नावाने  signature (to the end, or to an annex)
  ^परिशिष्ट / जोडपत्र / अनुसूची        annex (heads what follows)
  anything before all that          title

Provisions continue across pages (one span per page). Provisions under 40 characters merge into the next one; those
over 1,200 characters are split at a danda or full stop into label·1, label·2, ...
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rci.pipeline.normalise import ascii_digits

MIN_CHARS = 40
MAX_CHARS = 1200

_END = r"(?=[\s:.,(\-–]|$)"
ANNEX = re.compile(r"^(परिशिष्ट|जोडपत्र|सहपत्र|अनुसूची|Annexure|Appendix|Schedule)" + _END + r"\s*[-–]?\s*([^\s:.,]{0,8})", re.I)
SIGNATURE = re.compile(r"(राज्यपाल|Governor).{0,40}(आदेशानुसार|नावाने)|आदेशानुसार\s*(व|आणि)\s*नावाने|By order and in the name", re.I)
REFERENCE = re.compile(r"^(संदर्भ|वाचा)" + _END)
PREAMBLE = re.compile(r"^प्रस्तावना" + _END)
ORDER = re.compile(r"^(शासन\s*निर्णय|शासन\s*परिपत्रक|शासन\s*शुद्धिपत्रक|शुद्धिपत्रक|अधिसूचना|परिपत्रक|आदेश)\s*[:\-–.]?\s*$")
TABLE = re.compile(r"^तक्ता\s*(?:[-–:]\s*)?(\(?([अ-ह])\)?)?" + _END)
RULE = re.compile(r"^(नियम|परिच्छेद|कलम)\s*(\d{1,3}(?:\s*\([अ-हa-z0-9]\))?)")
PARA = re.compile(r"^(\d{1,3})[.)](?!\d)")
ROW = re.compile(r"^\(?(\d{1,3})[.)]?(?=\s)")
CLAUSE = re.compile(r"^\(([अ-ह])\)|^([अ-ह])\)|^\(([a-z])\)")
SUBCLAUSE = re.compile(r"^\((\d{1,2})\)")
SENTENCE_END = re.compile(r"[।.](?=\s)|[।]")


@dataclass
class Provision:
    seq: int
    label: str
    kind: str  # title | preamble | reference | paragraph | clause | table_row | order | annex | signature
    heading_path: list[str]
    text: str
    spans: list[dict] = field(default_factory=list)  # [{page_no, start, end}] into the page text

    @property
    def page_from(self) -> int:
        return self.spans[0]["page_no"]

    @property
    def page_to(self) -> int:
        return self.spans[-1]["page_no"]


@dataclass
class _Raw:
    kind: str
    label: str
    heading: list[str]
    segs: list[list[int]] = field(default_factory=list)  # [page_no, start, end]

    def extend(self, page_no: int, s: int, e: int) -> None:
        if self.segs and self.segs[-1][0] == page_no:
            self.segs[-1][2] = e
        else:
            self.segs.append([page_no, s, e])


def _text(segs: list[list[int]], texts: dict[int, str]) -> str:
    return "\n".join(texts[p][s:e] for p, s, e in segs)


def _split_long(r: _Raw, texts: dict[int, str]) -> list[_Raw]:
    full = _text(r.segs, texts)
    if len(full) <= MAX_CHARS:
        return [r]
    # provision-text offset -> (seg index, offset in seg)
    starts, pos = [], 0
    for p, s, e in r.segs:
        starts.append(pos)
        pos += (e - s) + 1
    cuts, a = [], 0
    while len(full) - a > MAX_CHARS:
        window = full[a + MAX_CHARS // 3: a + MAX_CHARS]
        ends = [m.end() for m in SENTENCE_END.finditer(window)]
        if ends:
            b = a + MAX_CHARS // 3 + ends[-1]
        else:
            sp = full.rfind(" ", a + MAX_CHARS // 3, a + MAX_CHARS)
            b = sp if sp > a else a + MAX_CHARS
        cuts.append(b)
        a = b
    bounds = [0] + cuts + [len(full)]
    out = []
    for n, (x, y) in enumerate(zip(bounds, bounds[1:]), start=1):
        while x < y and full[x].isspace():
            x += 1
        while y > x and full[y - 1].isspace():
            y -= 1
        if x >= y:
            continue
        segs = []
        for (p, s, e), t0 in zip(r.segs, starts):
            lo, hi = max(x, t0), min(y, t0 + (e - s))
            if lo < hi:
                segs.append([p, s + lo - t0, s + hi - t0])
        out.append(_Raw(r.kind, f"{r.label}·{n}", r.heading, segs))
    return out


def split(pages: list[tuple[int, str]]) -> list[Provision]:
    texts = {p: t for p, t in pages}
    raws: list[_Raw] = []
    cur: _Raw | None = None
    mode = "title"
    section: str | None = None
    para: str | None = None
    clause: str | None = None
    table: str | None = None
    last_row = last_ref = 0
    row_style = False

    def heading(*extra: str | None) -> list[str]:
        return [h for h in (section, table, *extra) if h]

    def start(kind: str, label: str, head: list[str], page_no: int, s: int, e: int) -> None:
        nonlocal cur
        cur = _Raw(kind, label, head)
        cur.extend(page_no, s, e)
        raws.append(cur)

    for page_no, text in pages:
        off = 0
        for line in text.split("\n"):
            s, e = off, off + len(line)
            off = e + 1
            if not line.strip():
                continue
            a = ascii_digits(line)

            def orig(m: re.Match, g: int = 1) -> str:
                return line[m.start(g):m.end(g)]

            if (m := ANNEX.match(a)):
                section, table, para, clause, mode = line[:m.end()].strip(" :.-–"), None, None, None, "body"
                start("annex", section, [], page_no, s, e)
                continue
            if mode == "signature":
                cur.extend(page_no, s, e)
                continue
            if SIGNATURE.search(a) and raws:
                mode, table = "signature", None
                start("signature", "स्वाक्षरी", heading(), page_no, s, e)
                continue
            if REFERENCE.match(a) and mode in ("title", "body", "preamble"):
                mode, last_ref = "reference", 0
                start("reference", "संदर्भ", [], page_no, s, e)
                continue
            if PREAMBLE.match(a):
                mode = "preamble"
                start("preamble", "प्रस्तावना", [], page_no, s, e)
                continue
            if (m := ORDER.match(a)):
                section, table, para, clause, mode = re.sub(r"\s+", " ", orig(m)), None, None, None, "body"
                start("order", section, [], page_no, s, e)
                continue
            if (m := TABLE.match(a)):
                letter = orig(m, 2) if m.group(2) else None
                table, mode, last_row = (f"तक्ता ({letter})" if letter else "तक्ता"), "table", 0
                start("paragraph", table, heading(), page_no, s, e)
                continue
            if mode == "reference" and (m := ROW.match(a) or PARA.match(a)):
                n = int(m.group(1))
                if n == last_ref + 1:
                    last_ref = n
                    start("reference", f"संदर्भ {orig(m)}", ["संदर्भ"], page_no, s, e)
                    continue
                mode = "body"
            if mode == "table" and (m := ROW.match(a)):
                n = int(m.group(1))
                style = a[m.end(1):m.end(1) + 1] in ".)"  # rows keep the numbering style of row 1 ("५" vs "५.")
                if last_row == 0 or (n == last_row + 1 and style == row_style):
                    last_row, row_style = n, style
                    start("table_row", f"{table} अ.क्र. {orig(m)}", heading(), page_no, s, e)
                    continue
                mode, table = "body", None
            if (m := RULE.match(a)):
                para, clause, mode, table = f"{orig(m)} {orig(m, 2)}", None, "body", None
                start("paragraph", para, heading(), page_no, s, e)
                continue
            if (m := PARA.match(a)):
                para, clause, table = orig(m), None, None
                mode = "body"
                start("paragraph", para, heading(), page_no, s, e)
                continue
            if (m := CLAUSE.match(a)):
                g = next(i for i in (1, 2, 3) if m.group(i))
                clause = f"{para or ''}({orig(m, g)})"
                start("clause", clause, heading(para), page_no, s, e)
                continue
            if (m := SUBCLAUSE.match(a)):
                start("clause", f"{clause or para or ''}({orig(m)})", heading(para, clause), page_no, s, e)
                continue
            if cur is None:
                start("title", "शीर्षक", [], page_no, s, e)
            else:
                cur.extend(page_no, s, e)

    # short provisions merge into the next one (the last into the one before it)
    merged: list[_Raw] = []
    carry: _Raw | None = None
    for r in raws:
        if carry is not None:
            if carry.segs[-1][0] == r.segs[0][0]:
                r.segs = carry.segs[:-1] + [[r.segs[0][0], carry.segs[-1][1], r.segs[0][2]]] + r.segs[1:]
            else:
                r.segs = carry.segs + r.segs
            carry = None
        if len(_text(r.segs, texts).strip()) < MIN_CHARS:
            carry = r
            continue
        merged.append(r)
    if carry is not None:
        if merged:
            last = merged[-1]
            if last.segs[-1][0] == carry.segs[0][0]:
                last.segs = last.segs[:-1] + [[last.segs[-1][0], last.segs[-1][1], carry.segs[0][2]]] + carry.segs[1:]
            else:
                last.segs += carry.segs
        else:
            merged.append(carry)

    out: list[Provision] = []
    for r in merged:
        for piece in _split_long(r, texts):
            out.append(Provision(len(out) + 1, piece.label, piece.kind, piece.heading, _text(piece.segs, texts),
                                 [{"page_no": p, "start": s, "end": e} for p, s, e in piece.segs]))
    return out


def anchors(prov: Provision, pages: dict[int, dict], *, document_id: str, filename: str) -> list[dict]:
    """One Part 1 anchor per span. `pages`: page_no -> {"text", "ocr" (blob JSON or None), "page_id"}."""
    from rci.review.anchors import boxes_for_span

    out = []
    for sp in prov.spans:
        pg = pages.get(sp["page_no"]) or {}
        text = pg.get("text") or ""
        boxes, lines, precision = boxes_for_span(pg.get("ocr"), text, sp["start"], sp["end"])
        quote = re.sub(r"\s+", " ", text[sp["start"]:sp["end"]]).strip()[:200]
        out.append({"document_id": document_id, "filename": filename, "page_seq": sp["page_no"], "page_id": pg.get("page_id"),
                    "page_no": sp["page_no"], "kind": "highlight" if boxes else "page", "boxes": boxes, "lines": lines,
                    "precision": precision, "quote": quote or None, "reason": None if boxes else "no_geometry"})
    return out
