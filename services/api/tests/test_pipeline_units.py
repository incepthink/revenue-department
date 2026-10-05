"""Classifier, segmenter, extraction, stages, validators — no database."""
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from rci.adapters.llm import FakeLLMClient
from rci.pipeline.classify import ExhibitTypeDef, PageClass, classify_page, continuation_marker
from rci.pipeline.extract.common import PageText, parse_date
from rci.pipeline.extract.extractor import extract_exhibit
from rci.pipeline.extract.fields import parse_value
from rci.pipeline.normalise import ascii_digits
from rci.pipeline.segment import PageSignal, segment_pages
from rci.pipeline.stage import StageInput, assign_stages
from rci.pipeline.validators import ExhibitFacts, run_validators

SEED = Path(__file__).resolve().parents[1] / "src" / "rci" / "seed" / "exhibit_types.yaml"
TYPES = [ExhibitTypeDef.from_row(r) for r in yaml.safe_load(SEED.read_text(encoding="utf-8"))["exhibit_types"]]


def page(text, seq=1):
    return PageText(None, seq, seq, "doc.pdf", text, ascii_digits(text))


# ---- classification -----------------------------------------------------------------------------
def test_title_on_heading_line_starts_exhibit():
    c = classify_page("महाराष्ट्र शासन\nमिळकत पत्रिका\nनगर भूमापन क्रमांक : १२३४/अ\nधारणाधिकार : भोगवटादार वर्ग-२", TYPES)
    assert c.code == "PROPERTY_CARD" and c.is_start


def test_titles_mentioned_in_reference_lists_do_not_win():
    text = ("जिल्हाधिकारी कार्यालय, पुणे\nक्रमांक : महसूल/१२\nदिनांक : २८/०६/२०२४\nआदेश\nवाचले :\n"
            "१) या कार्यालयाचे त्रुटी पत्र दिनांक ११/०३/२०२४.\n२) या कार्यालयाचे मागणी पत्र दिनांक २०/०५/२०२४.")
    c = classify_page(text, TYPES)
    assert c.code == "COLLECTOR_ORDER" and c.is_start


def test_continuation_marker():
    assert continuation_marker("पान क्र. २/२\nपुढील मजकूर") == (2, 2)
    assert continuation_marker("Page 1 of 3\ntext") is None
    c = classify_page("पान क्र. २/२\nभाडेपट्टा करारनामा अटी", TYPES)
    assert not c.is_start and c.continuation_of == (2, 2)


def test_llm_fallback_only_when_keywords_unsure():
    llm = FakeLLMClient(responder=lambda t, u, s: {"exhibit_type": "NOC", "is_first_page": True, "confidence": 0.99, "reason": "r"})
    c = classify_page("कोणताही ओळखता न येणारा मजकूर असलेले एक पान येथे आहे आणि त्यात शीर्षक नाही", TYPES, llm=llm)
    assert c.method == "llm" and c.code == "NOC" and c.confidence <= 0.9
    assert classify_page("महाराष्ट्र शासन\nमिळकत पत्रिका\nधारणाधिकार : x", TYPES, llm=llm).method == "keywords"


# ---- segmentation -------------------------------------------------------------------------------
def sig(seq, doc, code, start=False, conf=0.9, cont=None):
    return PageSignal(seq, doc, PageClass(code, conf if code else 0.0, "keywords", start, cont))


def test_segmenter_rules():
    pages = [
        sig(1, 1, "APPLICATION", True), sig(2, 1, None, cont=(2, 2)),     # continuation
        sig(3, 1, "CHALLAN", True), sig(4, 1, "CHALLAN", True),           # back-to-back same type
        sig(5, 1, "LEASE_SANAD", True), sig(6, 1, "PROPERTY_CARD", False, 0.4),  # weak switch → continuation
        sig(7, 2, "LEASE_SANAD", False),                                   # new file → new exhibit
    ]
    segs = segment_pages(pages)
    assert [(s.exhibit_type, s.page_seqs) for s in segs] == [
        ("APPLICATION", [1, 2]), ("CHALLAN", [3]), ("CHALLAN", [4]), ("LEASE_SANAD", [5, 6]), ("LEASE_SANAD", [7])]


# ---- parsers & extraction -----------------------------------------------------------------------
@pytest.mark.parametrize("s,want", [("दिनांक ०५/०२/२०२४", dt.date(2024, 2, 5)), ("5 मार्च 2024", dt.date(2024, 3, 5)),
                                    ("dated 10-06-24", dt.date(2024, 6, 10)), ("12 June, 2023", dt.date(2023, 6, 12)),
                                    ("31/02/2024", None)])
def test_parse_date(s, want):
    assert parse_date(ascii_digits(s)) == want


def test_parse_values():
    assert parse_value("amount", "रु. ४५,६४,३२५/-") == Decimal("4564325.00")
    assert parse_value("area", "1000 चौ. फूट") == Decimal("92.90")
    assert parse_value("cts", "१२३४ / अ") == "1234/अ"


@pytest.mark.parametrize("raw,want", [("रु. ४५६४,३२५/- दिनांक", "4564325.00"),  # OCR dropped a comma
                                      ("रु. 1,200, दरवर्षी", "1200.00"), ("रु.१२५००/-", "12500.00")])
def test_amounts_tolerate_ocr_grouping(raw, want):
    assert str(parse_value("amount", raw)) == want


def test_joiners_do_not_break_keywords():
    from rci.pipeline.normalise import normalise_text

    ocr = "रक" + chr(0x094D) + chr(0x200D) + "कम"  # explicit half-form, as Tesseract emits it
    assert normalise_text(ocr) == "रक्कम"


def test_rules_extraction_with_page_evidence():
    p1 = page("दिनांक : ०५/०२/२०२४\nअर्जदार संस्था : सुयोग सहकारी गृहनिर्माण संस्था मर्यादित\n"
              "मौजे कोथरूड, ता. हवेली, जि. पुणे येथील नगर भूमापन क्रमांक १२३४/अ, क्षेत्र १२५०.५० चौ.मी.\n"
              "मा. जिल्हाधिकारी, पुणे", seq=7)
    f = extract_exhibit("APPLICATION", [p1])["fields"]
    assert f["cts_no"]["value"] == "1234/अ" and f["cts_no"]["raw"] == "१२३४/अ" and f["cts_no"]["page_seq"] == 7
    assert f["area_sqm"]["value"] == "1250.50"
    assert f["society_name"]["value"] == "सुयोग सहकारी गृहनिर्माण संस्था मर्यादित"
    assert f["application_date"]["value"] == "2024-02-05"
    assert (f["village"]["value"], f["taluka"]["value"], f["district"]["value"]) == ("कोथरूड", "हवेली", "पुणे")


def test_llm_extraction_is_grounded_on_the_page():
    p = page("GRN : MH004512345678202425\nTotal Amount : Rs. 45,64,325.00\nPayment Date : 10/06/2024", seq=3)

    def responder(task, user, schema):
        return {"grn": {"found": True, "raw": "MH004512345678202425", "page_seq": 3},
                "payment_date": {"found": True, "raw": "10/06/2024", "page_seq": 3},
                "amount": {"found": True, "raw": "Rs. 99,99,999.00", "page_seq": 3},  # hallucinated
                "head_of_account": {"found": False, "raw": "", "page_seq": 0}}

    out = extract_exhibit("CHALLAN", [p], llm=FakeLLMClient(responder=responder))
    f = out["fields"]
    assert f["grn"]["method"] == "rules+llm" and f["grn"]["confidence"] == 0.95
    assert f["amount"]["value"] == "4564325.00" and f["amount"]["method"] == "rules"
    assert out["unverified"] and out["unverified"][0]["field"] == "amount"


# ---- stages -------------------------------------------------------------------------------------
def si(t, party, date=None):
    return StageInput(t, party, "filing", date)


def test_stage_assignment_forward_and_reversed():
    seq = [si("APPLICATION", "applicant", dt.date(2024, 1, 1)), si("PROPERTY_CARD", "applicant"),
           si("QUERY_LETTER", "office", dt.date(2024, 2, 1)), si("QUERY_REPLY", "applicant", dt.date(2024, 3, 1)),
           si("PROPERTY_CARD", "applicant"), si("DEMAND_LETTER", "office", dt.date(2024, 4, 1)),
           si("CHALLAN", "applicant"), si("COLLECTOR_ORDER", "office", dt.date(2024, 5, 1))]
    want = ["filing", "filing", "internal", "query_response", "query_response", "internal", "final", "final"]
    assert assign_stages(seq) == want
    assert assign_stages(seq[::-1]) == want[::-1]  # latest-on-top docket


# ---- validators ---------------------------------------------------------------------------------
def ev(v, seq=1):
    return {"value": v, "raw": str(v), "page_seq": seq, "snippet": str(v)}


def facts(n, t, stage="filing", **f):
    return ExhibitFacts(f"e{n}", n, t, stage, {k: ev(v) for k, v in f.items()})


def by_code(results):
    return {r.code: r for r in results}


def test_validators_pass_and_fail(params):
    ok = [facts(1, "APPLICATION", society_name="सुयोग सहकारी गृहनिर्माण संस्था मर्यादित", cts_no="1234/अ",
                area_sqm="1250.50", application_date="2024-02-05"),
          facts(2, "PROPERTY_CARD", cts_no="1234/अ", area_sqm="1250.50", issue_date="2024-01-20",
                holder_name="सुयोग सहकारी गृहनिर्माण संस्था मर्यादित"),
          facts(3, "SGM_RESOLUTION", meeting_date="2024-01-12"),
          facts(4, "FIELD_REPORT", "internal", cts_no="1234/अPT", area_sqm="1248.00")]
    r = by_code(run_validators(ok, params))
    assert {k: v.status for k, v in r.items()} == {k: "pass" for k in r}
    assert r["AREA_TOLERANCE"].depends_on_unverified  # parameter still to_verify

    bad = [facts(1, "APPLICATION", society_name="सुयोग सहकारी गृहनिर्माण संस्था", cts_no="1234/अ",
                 area_sqm="800.00", application_date="2025-01-15"),
           facts(2, "PROPERTY_CARD", cts_no="1235", area_sqm="800.00", issue_date="2023-06-01", holder_name="[PERSON_ab12cd34]"),
           facts(3, "SGM_RESOLUTION", meeting_date="2025-01-20"),
           facts(4, "CTSO_REPORT", "internal", cts_no="1234/अ", area_sqm="841.50")]
    r = by_code(run_validators(bad, params))
    assert {k: v.status for k, v in r.items()} == {k: "fail" for k in r}
    assert r["PROPERTY_CARD_RECENCY"].details["age_days"] == 594
