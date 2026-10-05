"""Span → boxes on the page image (rci.review.anchors), locating quotes (locate / locate_near) and word geometry in the
OCR blobs — no database."""
import pymupdf

from conftest import SAMPLES
from rci.adapters.ocr import AzureDocumentIntelligenceEngine
from rci.pipeline.extract.common import PageText, locate, locate_near
from rci.pipeline.normalise import ascii_digits
from rci.pipeline.runner import _text_layer_result
from rci.review.anchors import boxes_for_span, key
from rci.review.pageview import redacted_lines


def words_of(text: str, y: float, x0: float = 0.1, w: float = 0.05) -> list[dict]:
    """One word box per whitespace-separated word, laid out left to right on a line at height y."""
    out, x = [], x0
    for t in text.split():
        out.append({"text": t, "bbox": [x, y, x + w, y + 0.02], "confidence": 0.99})
        x += w + 0.01
    return out


def blob(*texts: str, words: bool = True) -> dict:
    lines = []
    for i, t in enumerate(texts):
        y = 0.1 + i * 0.03
        ws = words_of(t, y) if words and t else []
        bbox = [ws[0]["bbox"][0], y, ws[-1]["bbox"][2], y + 0.02] if ws else ([0.1, y, 0.9, y + 0.02] if t else None)
        lines.append({"text": t, "bbox": bbox, "confidence": 0.99, "words": ws})
    return {"engine": "test", "lines": lines, "mean_confidence": 0.99}


def span(text: str, quote: str) -> tuple[int, int]:
    s = locate(quote, PageText(None, 1, 1, "t", text, ascii_digits(text)))
    assert s is not None, quote
    return s


def union(ws: list[dict]) -> list[float]:
    return [min(w["bbox"][0] for w in ws), min(w["bbox"][1] for w in ws), max(w["bbox"][2] for w in ws), max(w["bbox"][3] for w in ws)]


def approx(a, b) -> bool:
    return all(abs(x - y) < 1e-4 for x, y in zip(a, b))


# ------------------------------------------------------------------------------------------------
# boxes_for_span
# ------------------------------------------------------------------------------------------------
def test_word_run_in_the_middle_of_a_line():
    ocr = blob("Survey No 12/3 village Erandwane taluka Haveli", "second line")
    text = "Survey No 12/3 village Erandwane taluka Haveli\nsecond line"
    boxes, lines, precision = boxes_for_span(ocr, text, *span(text, "12/3 village Erand"))
    ws = ocr["lines"][0]["words"]
    assert lines == [0] and precision == "word"
    assert len(boxes) == 1 and approx(boxes[0], union(ws[2:5]))  # "Erand" is part of the word Erandwane


def test_whole_line():
    ocr = blob("first line", "the whole second line")
    text = "first line\nthe whole second line"
    boxes, lines, precision = boxes_for_span(ocr, text, *span(text, "the whole second line"))
    assert lines == [1] and precision == "word" and approx(boxes[0], ocr["lines"][1]["bbox"])


def test_span_over_two_lines_gives_one_box_per_line():
    ocr = blob("alpha beta gamma", "", "delta epsilon zeta")  # an empty OCR line does not shift the alignment
    text = "alpha beta gamma\ndelta epsilon zeta"
    boxes, lines, precision = boxes_for_span(ocr, text, *span(text, "gamma\ndelta"))
    assert lines == [0, 2] and precision == "word"
    assert approx(boxes[0], ocr["lines"][0]["words"][2]["bbox"]) and approx(boxes[1], ocr["lines"][2]["words"][0]["bbox"])


def test_pii_token_gets_the_line_box():
    ocr = blob("Holder: Vitthal Ramchandra Joshi (lessee)")
    text = "Holder: [PERSON_0a1b2c3d] (lessee)"
    boxes, lines, precision = boxes_for_span(ocr, text, *span(text, "[PERSON_0a1b2c3d] (lessee"))
    assert lines == [0] and precision == "line" and approx(boxes[0], ocr["lines"][0]["bbox"])
    # the words around a token can still be boxed
    boxes, _, precision = boxes_for_span(ocr, text, *span(text, "(lessee)"))
    assert precision == "word" and approx(boxes[0], ocr["lines"][0]["words"][-1]["bbox"])


def test_unaligned_page_has_no_geometry():
    ocr = blob("one", "two")
    text = "one\ntwo\nthree"
    assert boxes_for_span(ocr, text, *span(text, "two")) == ([], [], None)
    assert boxes_for_span(None, text, 0, 3) == ([], [], None)


def test_blob_without_words_gets_line_precision():
    ocr = blob("Survey No 12/3 village Erandwane", words=False)
    text = "Survey No 12/3 village Erandwane"
    boxes, lines, precision = boxes_for_span(ocr, text, *span(text, "12/3"))
    assert lines == [0] and precision == "line" and approx(boxes[0], ocr["lines"][0]["bbox"])


def test_lines_without_boxes_still_give_the_lines():
    ocr = {"engine": "llm_vision", "lines": [{"text": "a line", "bbox": None, "confidence": 0.8}, {"text": "quoted here", "bbox": None}]}
    text = "a line\nquoted here"
    assert boxes_for_span(ocr, text, *span(text, "quoted")) == ([], [1], None)


def test_devanagari_digits_against_ascii_quote():
    ocr = blob("नगर भूमापन क्रमांक ५६७ क्षेत्र ८००.०० चौ.मी.")
    text = "नगर भूमापन क्रमांक ५६७ क्षेत्र ८००.०० चौ.मी."
    s = span(text, "567 क्षेत्र 800.00")  # the quote has ASCII digits, the page Devanagari ones
    boxes, _, precision = boxes_for_span(ocr, text, *s)
    assert precision == "word" and approx(boxes[0], union(ocr["lines"][0]["words"][3:6]))
    assert key("५६७ क्षेत्र") == key("567  क्षेत्र") == "567क्षेत्र"


def test_repeated_text_picks_the_occurrence_at_the_span():
    ocr = blob("CTS 567 and again CTS 567 here")
    text = "CTS 567 and again CTS 567 here"
    start = text.rindex("CTS 567")
    boxes, _, _ = boxes_for_span(ocr, text, start, start + len("CTS 567"))
    assert approx(boxes[0], union(ocr["lines"][0]["words"][4:6]))


# ------------------------------------------------------------------------------------------------
# locate / locate_near
# ------------------------------------------------------------------------------------------------
def test_locate_near_uses_the_snippet_to_pick_the_occurrence():
    text = "CTS No. 567 on the lease\nproperty card: CTS No. 567 village Erandwane"
    page = PageText(None, 1, 1, "t", text, ascii_digits(text))
    first = locate("567", page)
    assert first == (8, 11)
    second = locate_near("567", "card: CTS No. 567 village Erandwane", page)
    assert text[second[0]:second[1]] == "567" and second[0] > first[0]
    assert locate_near("567", "not on this page", page) == first
    assert locate_near("567", None, page) == first
    assert locate_near("  ", "x", page) is None
    assert locate_near("५६७", "card: CTS No. ५६७", page) == second  # digit script does not matter


# ------------------------------------------------------------------------------------------------
# Word geometry in the OCR blobs
# ------------------------------------------------------------------------------------------------
def test_azure_parse_result_keeps_words_per_line_in_order():
    def poly(x0, y0, x1, y1):
        return [x0, y0, x1, y0, x1, y1, x0, y1]

    result = {"pages": [{
        "width": 100, "height": 200,
        "words": [  # not in reading order on purpose
            {"content": "Haveli", "polygon": poly(40, 30, 60, 40), "span": {"offset": 17, "length": 6}, "confidence": 0.9},
            {"content": "Survey", "polygon": poly(10, 10, 30, 20), "span": {"offset": 0, "length": 6}, "confidence": 0.95},
            {"content": "567", "polygon": poly(35, 10, 45, 20), "span": {"offset": 7, "length": 3}, "confidence": 0.85},
            {"content": "Taluka", "polygon": poly(10, 30, 35, 40), "span": {"offset": 10, "length": 6}, "confidence": 0.8},
        ],
        "lines": [
            {"content": "Survey 567", "polygon": poly(10, 10, 45, 20), "spans": [{"offset": 0, "length": 10}]},
            {"content": "Taluka Haveli", "polygon": poly(10, 30, 60, 40), "spans": [{"offset": 10, "length": 13}]},
        ],
    }]}
    ocr = AzureDocumentIntelligenceEngine.parse_result(result)
    assert [[w.text for w in l.words] for l in ocr.lines] == [["Survey", "567"], ["Taluka", "Haveli"]]
    assert ocr.lines[0].words[1].bbox == (0.35, 0.05, 0.45, 0.1)
    assert abs(ocr.lines[0].confidence - 0.9) < 1e-9
    assert ocr.to_json()["lines"][1]["words"][0] == {"text": "Taluka", "bbox": (0.1, 0.15, 0.35, 0.2), "confidence": 0.8}


def test_text_layer_has_words_and_the_same_line_text():
    page = pymupdf.open(SAMPLES / "dockets" / "DKT-002" / "01_arj.pdf")[0]
    old = [t for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
           if (t := "".join(s["text"] for s in ln["spans"]).strip())]
    ocr = _text_layer_result(page)
    assert [l.text for l in ocr.lines] == old
    for l in ocr.lines:
        assert l.words and " ".join(w.text for w in l.words) == " ".join(l.text.split())
        for w in l.words:
            x0, y0, x1, y1 = w.bbox
            assert l.bbox[0] - 1e-6 <= x0 < x1 <= l.bbox[2] + 1e-6 and 0 <= y0 < y1 <= 1


def test_page_ocr_view_carries_no_words():
    ocr = blob("Holder: Vitthal Joshi")
    lines, aligned = redacted_lines(ocr, "Holder: [PERSON_0a1b2c3d]")
    assert aligned and all("words" not in l for l in lines)
