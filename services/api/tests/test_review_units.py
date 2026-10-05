"""Review state ops, comparisons, summary, embeddings and auth primitives — no database."""
import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rci.adapters.embeddings import LocalHashEmbedder, cosine
from rci.adapters.llm import DataResidencyError
from rci.auth import hash_password, issue_token, decode_token, verify_password
from rci.pipeline.extract.fields import FIELDS
from rci.review.compare import agreement, cohen_kappa, corrections
from rci.review.state import (
    PageInfo,
    ReviewContext,
    ReviewError,
    apply_ops,
    derive,
    exhibit_uuid,
    initial_state,
    parse_manual_value,
)
from rci.review.summary import facet_texts, summary

CASE = uuid.UUID("00000000-0000-0000-0000-00000000c0de")
PAGES = {
    1: "अर्ज\nसविनय विनंती अर्ज करण्यात येतो की, मौजे कोथरूड, ता. हवेली, जि. पुणे येथील नगर भूमापन क्रमांक १२३४, क्षेत्र ८००.०० चौ.मी.\nदिनांक : १५/०१/२०२५",
    2: "मिळकत पत्रिका\nगाव : कोथरूड\nनगर भूमापन क्रमांक : १२३४\nक्षेत्र : ८००.०० चौ.मी.\nसत्ता प्रकार : भोगवटादार वर्ग-२",
    3: "पुढे चालू\nधारकाचे नाव : [PERSON_abcdef12]",
}


@pytest.fixture
def ctx(params):
    from rci.pipeline.normalise import ascii_digits

    pages = {n: PageInfo(uuid.uuid5(CASE, str(n)), n, n, "docket.pdf", t, ascii_digits(t)) for n, t in PAGES.items()}
    types = {c: {"code": c, "name_en": c, "name_mr": c, "party": "applicant", "default_stage": "filing"}
             for c in ("APPLICATION", "PROPERTY_CARD", "AFFIDAVIT")}
    reqs = [{"code": "PROPERTY_CARD_RECENT", "name_en": "PC", "name_mr": "PC", "category": "applicant_document",
             "satisfied_by": ["PROPERTY_CARD"], "query_keywords": ["मिळकत पत्रिका"], "applies_when": {}, "status": "to_verify"}]
    events = [{"code": "APPLICATION_FILED", "name_en": "filed", "name_mr": "filed", "source_exhibit_types": ["APPLICATION"],
               "date_field": "application_date", "typical_order": 10}]
    return ReviewContext(case_id=CASE, case={"case_id": str(CASE), "case_ref": "U-1", "office_code": "PILOT",
                                             "data_class": "synthetic", "case_type": "CHS_CLASS2_TO_CLASS1"},
                         data_class="synthetic", pages=pages, exhibit_types=types, requirement_catalog=reqs,
                         event_catalog=events, params=params)


@pytest.fixture
def state(ctx):
    from rci.review.state import _extract

    exs = []
    for t, a, b in (("APPLICATION", 1, 1), ("PROPERTY_CARD", 2, 3)):
        exs.append({"exhibit_id": exhibit_uuid(CASE, t, a, b), "type": t, "stage": "filing", "pages": list(range(a, b + 1)),
                    "confidence": 0.8, "classification_method": "keywords"})
    card = {"exhibits": exs, "facts": {"queries": []}, "requirements": [], "events": []}
    extractions = {e["exhibit_id"]: _extract(ctx, e["type"], e["pages"][0], e["pages"][-1]) for e in exs}
    return initial_state(card, "base", 1, "draft", extractions)


def test_initial_state_nothing_decided(state, ctx):
    d = derive(state, ctx)
    n_fields = len(FIELDS["APPLICATION"]) + len(FIELDS["PROPERTY_CARD"])
    assert len(d.undecided) == n_fields
    assert d.fields[state["exhibits"][1]["exhibit_id"]]["area_sqm"]["value"] == "800.00"


def test_split_merge_retype_roundtrip(state, ctx):
    pc = state["exhibits"][1]["exhibit_id"]
    s1 = apply_ops(state, ctx, [{"op": "exhibit.split", "exhibit_id": pc, "at_page_seq": 3}])
    assert [(e["type"], e["start"], e["end"]) for e in s1["exhibits"]] == [("APPLICATION", 1, 1), ("PROPERTY_CARD", 2, 2), ("PROPERTY_CARD", 3, 3)]
    assert state["exhibits"][1]["end"] == 3  # apply_ops never mutates its input
    s2 = apply_ops(s1, ctx, [{"op": "exhibit.retype", "exhibit_id": s1["exhibits"][2]["exhibit_id"], "type": "AFFIDAVIT"}])
    assert s2["exhibits"][2]["type"] == "AFFIDAVIT" and s2["exhibits"][2]["origin"] == "manual"
    s3 = apply_ops(s1, ctx, [{"op": "exhibit.merge", "exhibit_id": s1["exhibits"][1]["exhibit_id"]}])
    assert s3["exhibits"][1]["exhibit_id"] == pc  # deterministic ids: same type + range → same id
    with pytest.raises(ReviewError):
        apply_ops(s3, ctx, [{"op": "exhibit.merge", "exhibit_id": pc}])  # last exhibit
    with pytest.raises(ReviewError):
        apply_ops(state, ctx, [{"op": "exhibit.retype", "exhibit_id": pc, "type": "NOT_A_TYPE"}])


def test_decisions_are_dropped_with_their_exhibit(state, ctx):
    pc = state["exhibits"][1]["exhibit_id"]
    s1 = apply_ops(state, ctx, [{"op": "field.decide", "exhibit_id": pc, "field": "cts_no", "action": "accept"},
                                {"op": "exhibit.split", "exhibit_id": pc, "at_page_seq": 3}])
    assert pc not in s1["decisions"]


def test_field_edit_rules(state, ctx):
    app = state["exhibits"][0]["exhibit_id"]
    ok = apply_ops(state, ctx, [{"op": "field.decide", "exhibit_id": app, "field": "application_date", "action": "edit",
                                 "value": "16/01/2025", "page_seq": 1, "raw": "१५/०१/२०२५"}])
    d = derive(ok, ctx)
    ev = d.fields[app]["application_date"]
    assert ev["value"] == "2025-01-16" and ev["method"] == "manual" and ev["page_seq"] == 1 and ev["located"]
    assert "१५/०१/२०२५" in ev["snippet"]
    for bad, msg in [({"value": "x", "page_seq": 1}, "not a date"), ({"value": "2025-01-16"}, "page reference"),
                     ({"value": "2025-01-16", "page_seq": 2}, "outside"), ({"value": "2025-01-16", "page_seq": 99}, "not a page")]:
        with pytest.raises(ReviewError, match=msg):
            apply_ops(state, ctx, [{"op": "field.decide", "exhibit_id": app, "field": "application_date", "action": "edit", **bad}])
    with pytest.raises(ReviewError, match="no field"):
        apply_ops(state, ctx, [{"op": "field.decide", "exhibit_id": app, "field": "nope", "action": "accept"}])


def test_not_present_and_accept_all(state, ctx):
    pc = state["exhibits"][1]["exhibit_id"]
    s1 = apply_ops(state, ctx, [{"op": "field.decide", "exhibit_id": pc, "field": "area_sqm", "action": "not_present"},
                                {"op": "field.accept_all"}])
    d = derive(s1, ctx)
    assert not d.undecided and d.fields[pc]["area_sqm"] is None
    s2 = apply_ops(state, ctx, [{"op": "field.accept_all", "min_confidence": 0.99, "include_empty": False}])
    assert derive(s2, ctx).undecided  # nothing that confident; empty fields left for the reviewer


@pytest.mark.parametrize("kind,raw,expected", [
    ("date", "२०/०१/२०२५", "2025-01-20"), ("date", "2025-01-20", "2025-01-20"), ("amount", "1,25,000", "125000.00"),
    ("area", "८००", "800.00"), ("int", "२४", 24), ("bool", "होय", True), ("cts", "1234 / अ", "1234/अ"),
])
def test_parse_manual_value(kind, raw, expected):
    from rci.pipeline.extract.fields import FieldSpec

    assert parse_manual_value(FieldSpec("f", kind, "d"), raw) == expected


def test_manual_value_errors():
    from rci.pipeline.extract.fields import FieldSpec

    for kind, raw in (("int", "2.5"), ("enum", "maybe"), ("amount", "-5"), ("text", "  ")):
        with pytest.raises(ReviewError):
            parse_manual_value(FieldSpec("f", kind, "d", enum=("a",)), raw)


def test_events_editor(state, ctx):
    app = state["exhibits"][0]["exhibit_id"]
    s1 = apply_ops(state, ctx, [{"op": "events.set", "events": [{"code": "APPLICATION_FILED", "date": "2025-01-15", "exhibit_id": app}]}])
    d = derive(s1, ctx)
    assert d.events[0]["evidence"]["page_seq"] == 1 and d.events[0]["origin"] == "review"
    for bad in ({"code": "NOPE", "exhibit_id": app}, {"code": "APPLICATION_FILED"},
                {"code": "APPLICATION_FILED", "exhibit_id": app, "date": "15/01/2025"}):
        with pytest.raises(ReviewError):
            apply_ops(state, ctx, [{"op": "events.set", "events": [bad]}])


def test_requirement_override(state, ctx):
    s1 = apply_ops(state, ctx, [{"op": "requirement.set", "code": "PROPERTY_CARD_RECENT", "status": "invalid", "notes": "old"}])
    r = derive(s1, ctx).requirements[0]
    assert (r["status"], r["origin"], r["notes"]) == ("invalid", "review", "old")
    with pytest.raises(ReviewError):
        apply_ops(state, ctx, [{"op": "requirement.set", "code": "PROPERTY_CARD_RECENT", "status": "done"}])


def test_corrections_classify_actions():
    draft = [{"exhibit_id": "d1", "type": "PROPERTY_CARD", "stage": "filing", "start": 2, "end": 3,
              "fields": {"area_sqm": {"value": "800.00", "method": "rules", "page_seq": 2}, "cts_no": {"value": "1234", "method": "rules"},
                         "village": {"value": "X", "method": "llm"}, "tenure": None}}]
    approved = [{"exhibit_id": "a1", "type": "PROPERTY_CARD", "stage": "internal", "start": 2, "end": 3}]
    fields = {"a1": {"area_sqm": {"value": "805.00", "page_seq": 2}, "cts_no": {"value": "1234"}, "village": None,
                     "tenure": {"value": "govt_lease"}}}
    rows = {r["field_path"]: r for r in corrections(draft, approved, fields)}
    assert rows["PROPERTY_CARD.area_sqm"]["action"] == "edit"
    assert rows["PROPERTY_CARD.cts_no"]["action"] == "accept"
    assert rows["PROPERTY_CARD.village"]["action"] == "not_present" and rows["PROPERTY_CARD.village"]["draft_method"] == "llm"
    assert rows["PROPERTY_CARD.tenure"]["action"] == "added"
    assert rows["PROPERTY_CARD._stage"]["action"] == "edit"
    from rci.review.compare import accuracy_report

    rep = accuracy_report(list(rows.values()))
    assert rep["stage_corrections"] == 1 and "PROPERTY_CARD._stage" not in rep["by_field"] and rep["overall"]["n"] == 4
    seg = corrections(draft, [{**approved[0], "end": 2}], fields)
    assert seg[0]["action"] == "segment" and seg[0]["draft_value"]["pages"] == [2, 3]


def test_agreement_and_kappa():
    a = {"exhibits": {"A:1-1": "filing", "B:2-3": "filing"}, "fields": {"A:1-1": {"x": 1, "y": "p"}}, "requirements": {"R1": "present", "R2": "missing"},
         "query_map": {"5#0": ["R2"]}, "events": ["E@2025-01-01"]}
    b = {"exhibits": {"A:1-1": "filing", "B:2-2": "filing", "C:3-3": "filing"}, "fields": {"A:1-1": {"x": 1, "y": "q"}},
         "requirements": {"R1": "present", "R2": "queried"}, "query_map": {"5#0": ["R2"]}, "events": ["E@2025-01-01"]}
    ag = agreement(a, b)
    assert ag["segmentation"] == round(1 / 4, 4) and ag["fields"] == 0.5 and ag["requirements"] == 0.5 and ag["query_mapping"] == 1.0
    assert {d["kind"] for d in ag["disagreements"]} == {"field", "segment", "requirement"}
    assert cohen_kappa([("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")]) == 1.0
    assert cohen_kappa([("a", "b"), ("b", "a")]) < 0
    assert cohen_kappa([]) is None


def test_summary_and_facets():
    card = {"facts": {"society": {"name": {"value": "आनंद सहकारी गृहनिर्माण संस्था"}}, "land": {
        "village": {"value": "कोथरूड"}, "taluka": {"value": "हवेली"}, "district": {"value": "पुणे"}, "cts_no": {"value": "1234"},
        "area_sqm": {"value": "800.00"}, "tenure": {"value": "govt_lease"}}, "application": {"date": {"value": "2025-01-15"}},
        "order": {"order_date": {"value": "2025-06-01"}, "decision": {"value": "granted"}}, "demand": {}, "lease": {},
        "queries": [{"items": [{"value": "मिळकत पत्रिका", "requirement_codes": ["PROPERTY_CARD_RECENT"], "mapping": "requirement"}]}],
        "inspections": []},
        "exhibits": [{"type": "APPLICATION", "stage": "filing"}], "requirements": [{"code": "PROPERTY_CARD_RECENT", "status": "queried"}],
        "validations": [{"code": "CTS_MATCH", "status": "fail", "message": "differs"}]}
    s = summary(card)
    assert ".." not in s["mr"] and "न.भू.क्र. १२३४" in s["mr"] and "शासकीय भाडेपट्टा" in s["mr"] and "मंजूर" in s["mr"]
    assert "CTS 1234" in s["en"] and "Government lease" in s["en"] and "granted" in s["en"]
    f = facet_texts({**card, "summary": s}, {"PROPERTY_CARD_RECENT": {"name_mr": "मिळकत पत्रिका", "name_en": "Property card"}})
    assert "PROPERTY_CARD_RECENT" in f["issues"] and "CTS_MATCH" in f["issues"] and "filing: APPLICATION" in f["documents"]


def test_local_embedder_properties():
    e = LocalHashEmbedder()
    a, b, c = e.embed(["मौजे कोथरूड भाडेपट्टा जमीन", "मौजे कोथरूड भाडेपट्टा जमीन रूपांतरण", "challan payment GRN"], data_class="real")
    assert len(a) == 1536 and abs(cosine(a, a) - 1) < 1e-9
    assert cosine(a, b) > cosine(a, c)
    assert e.embed(["x"], data_class="synthetic") == e.embed(["x"], data_class="synthetic")


def test_remote_embedder_refuses_real_data():
    from rci.adapters.embeddings import _OpenAIEmbeddings

    client = _OpenAIEmbeddings(client=None, model="m", label="openai:m")
    with pytest.raises(DataResidencyError):
        client.embed(["x"], data_class="real")


@settings(max_examples=15, deadline=None)  # scrypt is deliberately slow
@given(st.text(min_size=8, max_size=40))
def test_password_hash_roundtrip(pw):
    h = hash_password(pw)
    assert verify_password(pw, h) and not verify_password(pw + "x", h)


def test_token_roundtrip_and_short_password():
    uid = uuid.uuid4()
    claims = decode_token(issue_token(uid, "reviewer"))
    assert claims["sub"] == str(uid) and claims["role"] == "reviewer"
    with pytest.raises(ValueError):
        hash_password("short")
    assert not verify_password("anything", None)
