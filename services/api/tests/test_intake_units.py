"""Intake scrutiny (per-document LLM call, grounding, fallbacks) and the cross-document report — no database."""
import datetime as dt

import pytest

from conftest import seed_stack
from rci.adapters.llm import FakeLLMClient, NullLLMClient, assert_strict_schema
from rci.intake.checks import DocIn, Docs, build_report, reference_date, survey_key
from rci.intake.extract import _rule_results, scrutinise_document, scrutiny_schema, system_prompt, tenure_of
from rci.intake.slots import BY_KEY, SLOTS
from rci.pipeline.extract.common import PageText
from rci.pipeline.normalise import ascii_digits

TODAY = dt.date(2025, 2, 1)
PROPERTY_CARD = (
    "महाराष्ट्र शासन\nमिळकत पत्रिका\nगाव / पेठ : एरंडवणे      तालुका : हवेली      जिल्हा : पुणे\n"
    "नगर भूमापन क्रमांक : ५६७      शीट क्रमांक : १२\nक्षेत्र : ८००.०० चौ.मी.\nसत्ता प्रकार : शासकीय भाडेपट्टा\n"
    "धारकाचे नाव : श्री. विठ्ठल रामचंद्र जोशी (मूळ भाडेपट्टाधारक)\nप्रत दिल्याचा दिनांक : ०१/०६/२०२३\nनगर भूमापन अधिकारी, पुणे\n"
)


def page(seq: int, text: str) -> PageText:
    return PageText(None, seq, 1, "upload.pdf", text, ascii_digits(text))


def answer(slot: str, fields: dict, matches: bool = True, detected: str = "", observations=()) -> dict:
    none = {"found": False, "raw": "", "page_seq": 0}
    return {"document_matches": matches, "detected_as": detected, "match_reason": "",
            "fields": {f.name: fields.get(f.name, none) for f in BY_KEY[slot].fields}, "observations": list(observations)}


def fake(ans: dict, real_ok: bool = True) -> FakeLLMClient:
    return FakeLLMClient(responder=lambda task, user, schema: ans, real_data_ok=real_ok)


def test_every_slot_schema_is_strict():
    for s in SLOTS:
        assert_strict_schema(scrutiny_schema(s))


def test_tenure_and_survey_normalisation():
    assert tenure_of("भोगवटादार वर्ग-२") == "occupancy_class_2"
    assert tenure_of("शासकीय भाडेपट्टा") == "govt_lease"
    assert tenure_of("something else") is None
    assert survey_key("स.नं. १२/३ अ") == survey_key("12/3अ") == "12/3अ"
    assert survey_key("S. No. 12/3") == "12/3"
    assert survey_key("गट क्र. ४५") == "45"


def test_llm_fields_are_grounded_on_the_page():
    ans = answer("property_card", {
        "cts_no": {"found": True, "raw": "५६७", "page_seq": 4},
        "tenure": {"found": True, "raw": "शासकीय भाडेपट्टा", "page_seq": 4},
        "issue_date": {"found": True, "raw": "०१/०६/२०२३", "page_seq": 4},
        "holder_name": {"found": True, "raw": "a name that is not on the page", "page_seq": 4},
    }, observations=[{"severity": "warning", "text": "Society not yet recorded as holder", "page_seq": 4},
                     {"severity": "info", "text": "cites a page that does not exist", "page_seq": 99}])
    llm = fake(ans)
    r = scrutinise_document(BY_KEY["property_card"], [page(4, PROPERTY_CARD)], llm=llm, data_class="real")
    assert llm.calls == [{"task": "intake_property_card", "data_class": "real", "images": 0}]
    assert r["method"] == "llm" and r["document_matches"] is True
    assert r["fields"]["cts_no"]["value"] == "567" and r["fields"]["cts_no"]["method"] == "rules+llm"
    assert r["fields"]["tenure"]["value"] == "govt_lease"
    assert r["fields"]["issue_date"]["value"] == "2023-06-01" and r["fields"]["issue_date"]["page_seq"] == 4
    # the model's holder is not on the page: dropped (listed as unverified), the rules value stays
    assert any(u["field"] == "holder_name" for u in r["unverified"])
    assert r["fields"]["holder_name"]["method"] == "rules"
    assert [o["page_seq"] for o in r["observations"]] == [4, None]


def test_without_llm_the_rules_fill_what_they_can():
    r = scrutinise_document(BY_KEY["property_card"], [page(1, PROPERTY_CARD)], llm=NullLLMClient(), data_class="real")
    assert r["method"] == "rules" and r["document_matches"] is None
    assert r["fields"]["cts_no"]["value"] == "567" and r["fields"]["area_sqm"]["value"] == "800.00"
    assert r["notes"] and "no LLM" in r["notes"][0]


def test_provider_not_allowed_for_real_data_falls_back_to_rules():
    llm = fake(answer("property_card", {}), real_ok=False)
    r = scrutinise_document(BY_KEY["property_card"], [page(1, PROPERTY_CARD)], llm=llm, data_class="real")
    assert r["method"] == "rules" and "not approved for real" in r["notes"][0]
    assert r["fields"]["cts_no"]["value"] == "567"


def test_unreadable_file_is_not_sent_to_the_model():
    llm = fake(answer("satbara", {}))
    r = scrutinise_document(BY_KEY["satbara"], [page(1, "  [PERSON_0a1b2c3d] \n")], llm=llm, data_class="synthetic",
                            unreadable_reason="the file is a scan without a text layer and no OCR engine is configured")
    assert not r["readable"] and not llm.calls and "OCR" in r["notes"][0]
    rep, _ = report([DocIn("d", "scan.pdf", "satbara", r)])
    f = next(f for f in rep["findings"] if f["code"] == "DOC_UNREADABLE")
    assert f["level"] == "problem" and f["action"] is None  # our configuration, not the applicant's to fix


def test_wrong_document_makes_the_case_incomplete():
    ans = answer("satbara", {}, matches=False, detected="Property card (मिळकत पत्रिका)")
    r = scrutinise_document(BY_KEY["satbara"], [page(1, PROPERTY_CARD)], llm=fake(ans), data_class="synthetic")
    docs = [d for d in complete_case() if d.slot != "satbara"] + [DocIn("x", "card.pdf", "satbara", r)]
    rep, _ = report(docs)
    f = next(f for f in rep["findings"] if f["code"] == "DOC_WRONG_TYPE")
    assert rep["verdict"] == "incomplete" and "Property card" in f["detail"] and "7/12" in f["action"]
    assert rep["findings"][0]["level"] == "problem"


# ------------------------------------------------------------------------------------------------
# Report over stored per-document results
# ------------------------------------------------------------------------------------------------
def _params():
    from rci.config import REPO_ROOT, Parameters

    return Parameters.load(REPO_ROOT / "config" / "parameters.yaml")


def ev(value) -> dict:
    return {"value": value, "raw": str(value), "page_seq": 1, "snippet": str(value)}


def doc(slot: str, **fields) -> DocIn:
    return DocIn(f"{slot}-id", f"{slot}.pdf", slot, {
        "slot": slot, "method": "llm", "readable": True, "pages": [1], "document_matches": True, "detected_as": None,
        "match_reason": None, "fields": {k: ev(v) for k, v in fields.items()}, "observations": [], "unverified": [], "notes": []})


SOCIETY = "गणेश कृपा सहकारी गृहनिर्माण संस्था मर्यादित"


def resolution_doc() -> DocIn:
    """The general body resolution: required by the seeded 2019 rules, filed as an additional document."""
    d = doc("other", document_date="2025-01-10")
    d.document_id, d.filename = "sgm-id", "resolution.pdf"
    d.result.update(detected_as="Special General Meeting resolution", summary="The society resolved to apply for conversion.",
                    relevance="supporting", satisfies_requirement="SGM_RESOLUTION")
    return d


def complete_case(sgm: bool = True, **changes) -> list[DocIn]:
    base = {
        "satbara": dict(survey_no="स.नं. १२/३", holder_name=SOCIETY, tenure="occupancy_class_2", issue_date="2025-01-02", other_rights="निरंक"),
        "property_card": dict(cts_no="567", area_sqm="800.00", holder_name="गणेश कृपा सहकारी गृहनिर्माण संस्था", tenure="occupancy_class_2",
                              issue_date="2025-01-05"),
        "grant": dict(cts_no="567", area_sqm="800.00", survey_no="12/3", tenure="occupancy_class_2"),
        "application": dict(addressed_to="मा. जिल्हाधिकारी, पुणे", society_name=SOCIETY, application_date="2025-01-15", cts_no="567",
                            area_sqm="801.00", signatory="[PERSON_0a1b2c3d], अध्यक्ष"),
        "aadhaar": dict(name="[PERSON_0a1b2c3d]"),
        "tax_receipts": dict(receipt_date="2024-10-01", arrears="0.00"),
        "index_ii": dict(cts_no="567", registration_date="1990-05-01"),
    }
    for slot, change in changes.items():
        if change is None:
            del base[slot]
        else:
            base[slot] = {**base[slot], **change}
    return [doc(slot, **fields) for slot, fields in base.items()] + ([resolution_doc()] if sgm else [])


def report(docs, stack=None):
    """The report under the seeded rules in force on the case's reference date (as the service loads them)."""
    stack = stack or seed_stack(reference_date(Docs(docs), TODAY)[0])
    rep = build_report(docs, _params(), stack, today=TODAY, llm_provider="fake")
    return rep, {f["code"]: f for f in rep["findings"]}


def test_consistent_complete_case_is_ready():
    rep, by = report(complete_case())
    assert rep["verdict"] == "ready", [f for f in rep["findings"] if f["level"] != "ok"]
    assert rep["counts"]["problem"] == 0 and rep["actions"] == []
    for code in ("LAND_CTS_MATCH", "LAND_SURVEY_MATCH", "LAND_AREA_MATCH", "HOLDER_IS_SOCIETY", "TENURE_ELIGIBLE",
                 "PROPERTY_CARD_RECENT", "SATBARA_RECENT", "TAX_RECEIPT_RECENT", "TAX_ARREARS", "APPLICATION_ADDRESSEE",
                 "SIGNATORY_IDENTITY"):
        assert by[code]["level"] == "ok", by[code]
    assert "SATBARA_OTHER_RIGHTS" not in by  # निरंक = nil
    assert by["LAND_AREA_MATCH"]["depends_on_unverified"]  # area tolerance is to_verify
    assert rep["reference_date"] == "2025-01-15"
    facts = {f["fact"]: f["value"] for f in rep["facts"]}
    assert facts["Society"] == SOCIETY and facts["CTS no."] == "567" and facts["Tenure"] == "occupancy_class_2"


def test_missing_required_document_makes_the_case_incomplete():
    rep, by = report(complete_case(satbara=None))
    assert rep["verdict"] == "incomplete" and by["DOC_MISSING"]["slots"] == ["satbara"]
    assert rep["actions"][0].startswith("Upload the 7/12 extract")
    # required by a seeded rule row that is still to_verify: cited and labelled, but a missing document stays blocking
    rule = by["DOC_MISSING"]["rule"]
    assert rule["code"] == "SATBARA_EXTRACT" and rule["rule_set_code"] == "RULES_2019"
    assert rule["status"] == "to_verify" and rule["label"] == "rule not yet verified"
    assert by["DOC_MISSING"]["level"] == "problem"


def test_required_documents_come_from_the_rules():
    # the property card: PROPERTY_CARD_RECENT is mandatory under the 2019 rules
    rep, by = report(complete_case(property_card=None))
    assert rep["verdict"] == "incomplete" and by["DOC_MISSING"]["rule"]["code"] == "PROPERTY_CARD_RECENT"
    assert rep["rules_applied"]["rule_sets"][0]["code"] == "RULES_2019" and rep["rule_facts"] == {"tenure": "occupancy_class_2"}
    # the grant: LEASE_OR_GRANT_DOCUMENT applies only to government-lease land
    rep, by = report(complete_case(grant=None))
    assert rep["verdict"] == "ready" and by["DOC_MISSING"]["level"] == "info"
    assert "only when tenure is Government lease" in by["DOC_MISSING"]["detail"]
    lease = {"tenure": "govt_lease"}
    rep, by = report(complete_case(grant=None, satbara=lease, property_card=lease))
    missing = [f for f in rep["findings"] if f["code"] == "DOC_MISSING"]
    assert rep["verdict"] == "incomplete" and {f["rule"]["code"] for f in missing} == {"LEASE_OR_GRANT_DOCUMENT", "NO_DUES_LEASE_RENT"}
    # tenure not stated anywhere: whether the grant is required cannot be decided
    none = {"tenure": None}
    rep, by = report(complete_case(grant=None, satbara=none, property_card=none))
    assert by["DOC_MISSING"]["level"] == "unchecked" and "tenure" in by["DOC_MISSING"]["detail"]


def test_required_document_without_a_slot_is_found_among_the_additional_documents():
    rep, by = report(complete_case())
    assert by["DOC_PRESENT"]["level"] == "ok" and by["DOC_PRESENT"]["rule"]["code"] == "SGM_RESOLUTION"
    rep, by = report(complete_case(sgm=False))
    assert rep["verdict"] == "incomplete" and by["DOC_MISSING"]["slots"] == ["other"]
    assert by["DOC_MISSING"]["action"].startswith("Upload the Special General Meeting resolution")
    # an additional document read without the AI reader could be it: not checked rather than missing
    rules_only = resolution_doc()
    rules_only.result.update(method="rules", satisfies_requirement=None)
    rep, by = report(complete_case(sgm=False) + [rules_only])
    assert by["DOC_MISSING"]["level"] == "unchecked" and rep["verdict"] == "ready"


def test_no_rules_in_force_is_flagged():
    rep, by = report(complete_case(), stack=seed_stack(dt.date(2018, 1, 1)))
    assert by["RULES_NONE_IN_FORCE"]["level"] == "warning" and rep["rules_applied"]["rule_sets"] == []


@pytest.mark.parametrize("changes, code", [
    ({"index_ii": {"cts_no": "568"}}, "LAND_CTS_MATCH"),
    ({"application": {"area_sqm": "900.00"}}, "LAND_AREA_MATCH"),
    ({"satbara": {"tenure": "occupancy_class_1"}}, "TENURE_ELIGIBLE"),
    ({"property_card": {"issue_date": "2023-06-01"}}, "PROPERTY_CARD_RECENT"),
    ({"tax_receipts": {"arrears": "12500.00"}}, "TAX_ARREARS"),
    ({"property_card": {"holder_name": "श्री. [PERSON_11112222]"}}, "HOLDER_IS_SOCIETY"),
])
def test_problems_need_attention_and_tell_the_applicant_what_to_do(changes, code):
    rep, by = report(complete_case(**changes))
    assert rep["verdict"] == "needs_attention"
    assert by[code]["level"] == "problem" and by[code]["action"] in rep["actions"]
    assert by[code]["evidence"] and all(e["page_seq"] == 1 for e in by[code]["evidence"])


def test_holder_mismatch_on_lease_land_is_a_warning():
    no_dues = resolution_doc()  # lease land: the rules also ask for the no-dues certificate
    no_dues.document_id, no_dues.filename = "noc-id", "no-dues.pdf"
    no_dues.result.update(detected_as="No lease-rent dues certificate", satisfies_requirement="NO_DUES_LEASE_RENT")
    rep, by = report(complete_case(property_card={"holder_name": "महाराष्ट्र शासन", "tenure": "govt_lease"},
                                   satbara={"tenure": "govt_lease"}, grant={"tenure": "govt_lease"}) + [no_dues])
    assert by["HOLDER_IS_SOCIETY"]["level"] == "warning" and rep["verdict"] == "ready"


def test_warnings_do_not_block_but_are_counted():
    rep, by = report(complete_case(aadhaar={"name": "[PERSON_99998888]"}, satbara={"other_rights": "बँक ऑफ महाराष्ट्र बोजा रु. ५,००,०००"}))
    assert rep["verdict"] == "ready" and rep["counts"]["warning"] == 2 and "2 points to note" in rep["headline"]
    assert by["SIGNATORY_IDENTITY"]["level"] == "warning" and by["SATBARA_OTHER_RIGHTS"]["level"] == "warning"


def test_model_observations_become_findings_marked_as_ai():
    docs = complete_case()
    docs[3].result["observations"] = [{"severity": "problem", "text": "The application is not signed.", "page_seq": 1},
                                      {"severity": "info", "text": "Written on society letterhead.", "page_seq": 1}]
    rep, _ = report(docs)
    ai = [f for f in rep["findings"] if f["source"] == "ai"]
    assert rep["verdict"] == "needs_attention" and len(ai) == 1 and ai[0]["title"] == "The application is not signed."


def test_undated_application_is_compared_with_today():
    docs = complete_case()
    del docs[3].result["fields"]["application_date"]
    rep, by = report(docs)
    assert rep["reference_date"] == TODAY.isoformat() and "today" in rep["reference_date_source"]


def test_rules_only_reading_is_flagged():
    docs = complete_case()
    for d in docs:
        d.result["method"] = "rules"
        d.result["notes"] = ["no LLM configured; only pattern rules were applied"]
    rep, by = report(docs)
    assert rep["limited"] and by["READING_LIMITED"]["level"] == "info"


# ------------------------------------------------------------------------------------------------
# Files uploaded without a slot: identification, additional documents
# ------------------------------------------------------------------------------------------------
APPLICATION = "दिनांक : १५/०१/२०२५\nप्रति,\nमा. जिल्हाधिकारी, पुणे\nअर्ज\nविषय : भाडेपट्ट्याने दिलेली शासकीय जमीन भोगवटादार वर्ग‐१ मध्ये रूपांतरित करण्याबाबत.\n"
LEASE = "भाडेपट्टा करारनामा\n(शासकीय जमीन भाडेपट्ट्याने देण्याबाबत)\nहा भाडेपट्टा करारनामा आज दिनांक १०/०८/१९७२ रोजी पुणे येथे महाराष्ट्र शासनाच्या वतीने जिल्हाधिकारी, पुणे यांच्यामध्ये करण्यात येत आहे.\n"
RESOLUTION = "गणेश कृपा सहकारी गृहनिर्माण संस्था मर्यादित\nविशेष सर्वसाधारण सभेचा ठराव\nसंस्थेची विशेष सर्वसाधारण सभा दिनांक २०/०१/२०२५ रोजी झाली.\n"


def test_identify_schema_and_other_scrutiny_schema_are_strict():
    from rci.intake.identify import identify_schema
    from rci.intake.slots import OTHER

    assert_strict_schema(identify_schema())
    schema = scrutiny_schema(OTHER)
    assert_strict_schema(schema)
    assert {"summary", "relevance"} <= set(schema["required"])


@pytest.mark.parametrize("text, filename, slot", [
    (APPLICATION, "scan1.pdf", "application"),
    (LEASE, "scan2.pdf", "grant"),
    (PROPERTY_CARD, "scan3.pdf", "property_card"),
    (RESOLUTION, "scan4.pdf", "other"),
    ("  [PERSON_0a1b2c3d] \n", "Satbara_utara.pdf", "satbara"),  # nothing readable: the filename decides
    ("  [PERSON_0a1b2c3d] \n", "IMG_0042.pdf", "other"),
])
def test_identify_by_keywords_without_llm(text, filename, slot):
    from rci.intake.identify import identify_document

    r = identify_document([page(1, text)], filename, llm=NullLLMClient(), data_class="real")
    assert r["method"] == "rules" and r["slot"] == slot, r


def test_identify_with_llm_reports_bundled_documents():
    from rci.intake.identify import identify_document

    llm = FakeLLMClient(responder=lambda task, user, schema: {
        "slot": "property_card", "detected_as": "Property card (मिळकत पत्रिका)", "reason": "Heading मिळकत पत्रिका",
        "confidence": 1.4, "bundled": ["General body resolution", " "]}, real_data_ok=True)
    r = identify_document([page(1, PROPERTY_CARD), page(2, RESOLUTION)], "all.pdf", llm=llm, data_class="real")
    assert llm.calls[0]["task"] == "intake_identify"
    assert r["method"] == "llm" and r["slot"] == "property_card" and r["confidence"] == 1.0
    assert r["bundled"] == ["General body resolution"]


def test_identify_falls_back_to_keywords_when_the_provider_is_not_allowed():
    from rci.intake.identify import identify_document

    llm = FakeLLMClient(responder=lambda *a: {}, real_data_ok=False)
    r = identify_document([page(1, PROPERTY_CARD)], "x.pdf", llm=llm, data_class="real")
    assert r["method"] == "rules" and r["slot"] == "property_card" and "not approved" in r["notes"][0]


def test_additional_document_scrutiny_keeps_summary_and_relevance():
    from rci.intake.slots import OTHER

    none = {"found": False, "raw": "", "page_seq": 0}
    ans = {"document_matches": True, "detected_as": "General body resolution", "match_reason": "",
           "fields": {f.name: none for f in OTHER.fields}, "observations": [],
           "summary": "The society resolved to apply for conversion.", "relevance": "supporting"}
    llm = fake(ans)
    r = scrutinise_document(OTHER, [page(1, RESOLUTION)], llm=llm, data_class="real", identified=True)
    assert r["slot"] == "other" and r["relevance"] == "supporting" and r["summary"].startswith("The society")


def other_doc(relevance: str, **fields) -> DocIn:
    d = doc("other", **fields)
    d.result.update(detected_as="Society registration certificate", summary="Registration of the society.", relevance=relevance)
    d.identified = {"slot": "other", "method": "llm", "bundled": []}
    return d


def test_additional_documents_are_listed_and_join_the_land_checks():
    rep, by = report(complete_case() + [other_doc("supporting", cts_no="567")])
    assert rep["verdict"] == "ready" and by["DOC_ADDITIONAL"]["level"] == "info"
    assert "Society registration certificate" in by["DOC_ADDITIONAL"]["title"]
    assert "other" in by["LAND_CTS_MATCH"]["slots"]
    rep, by = report(complete_case() + [other_doc("supporting", cts_no="999")])
    assert by["LAND_CTS_MATCH"]["level"] == "problem"


def test_unrelated_document_is_flagged_and_left_out_of_the_checks():
    rep, by = report(complete_case() + [other_doc("unrelated", cts_no="999")])
    assert by["DOC_UNRELATED"]["level"] == "warning" and by["DOC_UNRELATED"]["action"] in rep["actions"]
    assert by["LAND_CTS_MATCH"]["level"] == "ok" and rep["verdict"] == "ready"


def test_identified_document_that_reads_differently_is_uncertain_not_wrong():
    docs = complete_case()
    sat = next(d for d in docs if d.slot == "satbara")
    sat.identified = {"slot": "satbara", "method": "rules", "bundled": []}
    sat.result.update(document_matches=False, detected_as="Mutation entry (फेरफार)")
    rep, by = report(docs)
    assert "DOC_WRONG_TYPE" not in by and by["DOC_TYPE_UNCERTAIN"]["level"] == "warning"
    assert rep["verdict"] == "ready"


def test_bundled_file_asks_for_separate_uploads():
    docs = complete_case()
    docs[0].identified = {"slot": docs[0].slot, "method": "llm", "bundled": ["Index II", "Aadhaar card"]}
    rep, by = report(docs)
    assert by["DOC_BUNDLED"]["level"] == "warning" and "separate file" in by["DOC_BUNDLED"]["action"]


def test_two_files_of_a_single_document_are_noted():
    rep, by = report(complete_case() + [doc("satbara", survey_no="12/3")])
    assert by["DOC_DUPLICATE"]["level"] == "info" and by["DOC_DUPLICATE"]["slots"] == ["satbara"]


def test_failed_file_that_was_never_identified():
    rep, by = report(complete_case() + [DocIn("z", "broken.pdf", "auto", None, "not a PDF")])
    assert by["DOC_FAILED"]["action"] == "Upload broken.pdf again as a PDF or image."


# ------------------------------------------------------------------------------------------------
# Rules in force: document checks sent with the document, verdicts grounded, findings cite the rule
# ------------------------------------------------------------------------------------------------
SIGNED = {"code": "APPLICATION_SIGNED_BY_OFFICE_BEARER", "text_en": "The application is signed by an office-bearer."}
ADDRESSED = {"code": "ADDRESSED", "text_en": "The application is addressed to the Collector."}
DATED = {"code": "DATED", "text_en": "The application is dated."}


def test_rules_to_check_go_into_the_prompt_and_the_schema():
    slot = BY_KEY["application"]
    prompt = system_prompt(slot, False, [SIGNED, ADDRESSED])
    assert "RULES TO CHECK" in prompt and "1. [APPLICATION_SIGNED_BY_OFFICE_BEARER] The application is signed" in prompt
    schema = scrutiny_schema(slot, [SIGNED, ADDRESSED])
    assert_strict_schema(schema)
    item = schema["properties"]["rule_results"]["items"]["properties"]
    assert item["rule_code"]["enum"] == [SIGNED["code"], ADDRESSED["code"]]
    assert item["verdict"]["enum"] == ["met", "not_met", "cannot_tell", "not_applicable"]
    assert "rule_results" not in scrutiny_schema(slot)["properties"] and "RULES TO CHECK" not in system_prompt(slot, False)
    # an additional document is also offered the required documents without a slot of their own
    from rci.intake.slots import OTHER

    reqs = [{"code": "SGM_RESOLUTION", "name_en": "Special General Meeting resolution", "name_mr": "विशेष सर्वसाधारण सभेचा ठराव"}]
    schema = scrutiny_schema(OTHER, [], reqs)
    assert_strict_schema(schema)
    assert schema["properties"]["satisfies_requirement"]["enum"] == ["SGM_RESOLUTION", None]
    assert "SGM_RESOLUTION: Special General Meeting resolution" in system_prompt(OTHER, True, [], reqs)


def test_rule_verdicts_must_be_grounded_on_the_page():
    ans = answer("application", {})
    ans["rule_results"] = [
        {"rule_code": ADDRESSED["code"], "verdict": "met", "quote": "मा. जिल्हाधिकारी, पुणे", "page_seq": 2, "reason": "Addressed to the Collector."},
        {"rule_code": SIGNED["code"], "verdict": "not_met", "quote": "सचिव यांची स्वाक्षरी", "page_seq": 2, "reason": "Signed by the secretary."},
    ]  # DATED is not answered
    seen = []
    llm = FakeLLMClient(responder=lambda task, user, schema: seen.append(schema) or ans, real_data_ok=True)
    r = scrutinise_document(BY_KEY["application"], [page(2, APPLICATION)], llm=llm, data_class="real",
                            checks=[ADDRESSED, SIGNED, DATED])
    assert seen[0]["properties"]["rule_results"]["items"]["properties"]["rule_code"]["enum"] == ["ADDRESSED", SIGNED["code"], "DATED"]
    by = {x["code"]: x for x in r["rule_results"]}
    assert by["ADDRESSED"]["verdict"] == "met" and by["ADDRESSED"]["page_seq"] == 2 and "जिल्हाधिकारी" in by["ADDRESSED"]["quote"]
    assert by["ADDRESSED"]["text"] == ADDRESSED["text_en"]
    # the quote is not on the page: the verdict cannot stand
    assert by[SIGNED["code"]]["verdict"] == "cannot_tell" and by[SIGNED["code"]]["quote"] is None
    assert any(u["field"] == f"rule:{SIGNED['code']}" for u in r["unverified"])
    assert by["DATED"]["verdict"] == "cannot_tell" and "no answer" in by["DATED"]["reason"]


def test_unknown_and_repeated_rule_answers_are_dropped():
    pages = {1: page(1, APPLICATION)}
    results, bad = _rule_results([ADDRESSED], [
        {"rule_code": "NOT_ASKED", "verdict": "not_met", "quote": "", "page_seq": 0, "reason": ""},
        {"rule_code": "ADDRESSED", "verdict": "not_applicable", "quote": "", "page_seq": 0, "reason": "n/a"},
        {"rule_code": "ADDRESSED", "verdict": "met", "quote": "अर्ज", "page_seq": 1, "reason": ""},
    ], pages)
    assert [(x["code"], x["verdict"]) for x in results] == [("ADDRESSED", "not_applicable")] and bad == []


def test_without_llm_the_rules_are_not_checked():
    r = scrutinise_document(BY_KEY["application"], [page(1, APPLICATION)], llm=NullLLMClient(), data_class="real",
                            checks=[ADDRESSED, SIGNED])
    assert [x["verdict"] for x in r["rule_results"]] == ["cannot_tell", "cannot_tell"]
    assert any("2 rules in force" in n for n in r["notes"])


def verified(stack):
    from dataclasses import replace

    stack.rule_sets = [replace(r, status="verified") for r in stack.rule_sets]
    stack.document_checks = [replace(c, status="verified") for c in stack.document_checks]
    return stack


def with_rule_result(docs, slot: str, code: str, verdict: str, quote: str | None = "मा. जिल्हाधिकारी"):
    d = next(d for d in docs if d.slot == slot)
    d.result["rule_results"] = [{"code": code, "text": "(as asked)", "verdict": verdict, "quote": quote,
                                 "page_seq": 1 if quote else None, "snippet": quote, "reason": "Seen on the last page."}]
    return docs


def test_rule_findings_cite_the_rule_and_cap_unverified_rules():
    code = "APPLICATION_SIGNED_BY_OFFICE_BEARER"
    docs = with_rule_result(complete_case(), "application", code, "not_met")
    rep, by = report(docs)
    f = by["RULE_CHECK"]
    assert f["source"] == "rule" and f["title"].startswith("Not met: The application is signed")
    assert f["rule"]["code"] == code and f["rule"]["rule_set_code"] == "RULES_2019" and f["rule"]["label"] == "rule not yet verified"
    assert f["level"] == "warning" and rep["verdict"] == "ready"  # a to_verify rule is applied, at most as a warning
    assert f["evidence"][0]["page_seq"] == 1 and f["action"] in rep["actions"]
    rep, by = report(docs, stack=verified(seed_stack(dt.date(2025, 1, 15))))
    assert by["RULE_CHECK"]["level"] == "problem" and by["RULE_CHECK"]["rule"]["label"] is None
    assert rep["verdict"] == "needs_attention"
    rep, by = report(with_rule_result(complete_case(), "application", code, "met"))
    assert by["RULE_CHECK"]["level"] == "ok"
    rep, by = report(with_rule_result(complete_case(), "application", code, "cannot_tell", quote=None))
    assert by["RULE_CHECK"]["level"] == "unchecked"
    # a rule no longer in force, or checked on a document of another slot, is ignored
    rep, by = report(with_rule_result(complete_case(), "application", "GONE", "not_met"))
    assert "RULE_CHECK" not in by
    rep, by = report(with_rule_result(complete_case(), "grant", code, "not_met"))
    assert "RULE_CHECK" not in by


def test_conditional_rule_findings_follow_the_tenure():
    code = "LEASE_PERIOD_AND_RENT_STATED"  # seeded with condition {tenure: govt_lease}
    rep, by = report(with_rule_result(complete_case(), "grant", code, "not_met"))
    assert "RULE_CHECK" not in by  # occupancy class II land
    lease = {"tenure": "govt_lease"}
    stack = verified(seed_stack(dt.date(2025, 1, 15)))
    docs = complete_case(satbara=lease, property_card=lease, grant=lease)
    rep, by = report(with_rule_result(docs, "grant", code, "not_met"), stack=stack)
    assert by["RULE_CHECK"]["level"] == "warning" and by["RULE_CHECK"]["rule"]["status"] == "verified"
    none = {"tenure": None}
    rep, by = report(with_rule_result(complete_case(satbara=none, property_card=none, grant=none), "grant", code, "not_met"))
    assert by["RULE_CHECK"]["level"] == "warning" and "applies only when tenure is Government lease" in by["RULE_CHECK"]["detail"]
