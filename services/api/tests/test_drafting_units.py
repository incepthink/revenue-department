"""Drafting without a database: clause validation / selection / seed wording, mining (split + delexicalise) on the
sample-docket letter formats, the numeric / date / ID cross-check, the narrative slot with a fake LLM, all four templates
in both render modes (watermark, provenance, leak check, Devanagari font) and the officer-edit diff."""
import datetime as dt
import io
import uuid

import pytest
from docx import Document

from conftest import synthetic_card
from rci.adapters.llm import FakeLLMClient
from rci.drafting import clauses as cl
from rci.drafting import crosscheck, narrative, render
from rci.drafting.config import DraftingConfig
from rci.drafting.context import MARK_OPEN, DraftInputs, build
from rci.drafting.docxbuild import build_all
from rci.seed import _load

OTHER_ID = str(uuid.UUID(int=99))
OTHER_EX = str(uuid.UUID(int=98))
OTHER_SOC = "नंदनवन सहकारी गृहनिर्माण संस्था मर्यादित"
TEMPLATES = ("SCRUTINY_NOTE", "QUERY_LETTER", "DEMAND_LETTER", "COLLECTOR_ORDER")


def seed_rows() -> list[dict]:
    rows = []
    for n, spec in enumerate(_load("clauses.yaml")["clauses"]):
        rows.append({"id": f"seed-{n}", "clause_key": spec["key"], "kind": spec["kind"], "template": spec["template"],
                     "text_mr": spec["template"], "conditions": spec.get("conditions") or {}, "rule_set_codes": spec.get("rule_sets") or [],
                     "data_class": "any", "origin": "seed", "verification": "to_verify", "status": "canonical",
                     "source_case_ids": [], "source_refs": [], "updated_at": ""})
    return rows


def catalogs() -> dict:
    return {"requirements": {r["code"]: r for r in _load("requirement_catalog.yaml")["requirements"]},
            "exhibit_types": {t["code"]: t for t in _load("exhibit_types.yaml")["exhibit_types"]},
            "events": {e["code"]: e for e in _load("event_catalog.yaml")["events"]}}


def find_card(pred) -> dict:
    for i in range(1, 80):
        card, _ = synthetic_card(i)
        if pred(card):
            return card
    raise AssertionError("no synthetic scenario matches")


def fee(total="4564325.00"):
    if total is None:
        return {"status": "blocked", "total": None, "components": {}, "inputs": {"asr_year": 2024, "area_sqm": "1250.50"},
                "blockers": ["rate not filled in on slab X"], "unverified": [], "engine_version": "fee.1.0+test", "rule_set_code": None}
    return {"status": "unverified", "total": total, "inputs": {"asr_year": 2024, "asr_rate_per_sqm": "73000", "area_sqm": "1250.50",
                                                                "slab": {"rate_pct": "5.0000"}},
            "components": {"market_value": "91286500.00", "premium": total, "premium_after_limits": total, "total": total},
            "blockers": [], "unverified": ["parameter rounding is to_verify"], "engine_version": "fee.1.0+test", "rule_set_code": "SYN"}


def analysis(total="4564325.00") -> dict:
    other = {"case_id": OTHER_ID, "case_ref": "SYN-P-099", "exhibit_id": OTHER_EX, "page_seq": 7, "society_name": OTHER_SOC}
    return {
        "analysis_id": None, "card": {"id": None, "version": 1, "status": "approved"}, "regime": ["RULES_2019", "CIRCULAR_2021"],
        "precedents": [{"case_id": OTHER_ID, "case_ref": "SYN-P-099", "card_id": str(uuid.UUID(int=97)), "society_name": OTHER_SOC,
                        "link": other}],
        "gaps": [
            {"code": "PROPERTY_CARD_RECENT", "name_mr": "अद्ययावत मिळकत पत्रिका", "basis": "STATUTORY", "query_status": "invalid",
             "n_applicable": 10, "n_supporting": 9, "n_queried": 5, "was_queried_in": [other], "citations": [other],
             "rules": [{"rule_set_code": "RULES_2019", "status": "to_verify"}], "validators": [{"code": "PROPERTY_CARD_RECENCY", "evidence": []}],
             "explanation_mr": "१० पैकी ९ समान प्रकरणांत आवश्यक."},
            {"code": "MEMBER_LIST", "name_mr": "प्रमाणित सभासद यादी", "basis": "PRACTICE", "query_status": "missing", "n_applicable": 8,
             "n_supporting": 5, "n_queried": 4, "was_queried_in": [other], "citations": [other], "rules": [], "validators": [],
             "explanation_mr": "प्रथा."},
            {"code": "OCCUPANCY_CERTIFICATE", "name_mr": "भोगवटा प्रमाणपत्र", "basis": "SUPERSEDED", "query_status": "missing",
             "n_applicable": 4, "n_supporting": 2, "n_queried": 2, "was_queried_in": [], "citations": [], "rules": [], "validators": [],
             "explanation_mr": "जुने."},
        ],
        "steps": {"next": [{"code": "DEMAND_ISSUED", "name_mr": "मागणी पत्र जारी", "expected_by": "2025-05-01", "citations": [other]}],
                  "done": []},
        "fee": fee(total),
    }


def inputs(card, a=None, clauses=None, llm=None, cfg=None) -> DraftInputs:
    return DraftInputs(case={"id": card["case"]["case_id"], "case_ref": card["case"]["case_ref"], "data_class": "synthetic"},
                       office={"code": "PILOT", "name_mr": "जिल्हाधिकारी कार्यालय (पायलट)", "district": "पुणे"}, card=card,
                       analysis=a or analysis(), catalogs=catalogs(),
                       rule_sets={"RULES_2019": {"name": "Rules 2019", "kind": "base", "status": "to_verify"}},
                       clauses=clauses if clauses is not None else seed_rows(), cfg=cfg or DraftingConfig.load(), today=dt.date(2026, 9, 27),
                       llm=llm)


# ------------------------------------------------------------------------------------------------
# Clauses
# ------------------------------------------------------------------------------------------------
def test_seed_wording_is_valid_and_covers_every_requirement():
    rows = seed_rows()
    for r in rows:
        assert cl.validate_template(r["template"], key=r["clause_key"]) == [], r["clause_key"]
    keys = {r["clause_key"] for r in rows}
    applicant = [r["code"] for r in _load("requirement_catalog.yaml")["requirements"]
                 if r["category"] != "office_process" and r.get("active", True)]
    assert all(f"query_item.{c}" in keys for c in applicant) and "query_item.generic" in keys


@pytest.mark.parametrize("template,problem", [
    ("अर्ज दिनांक ०५/०२/२०२४.", "literal digits"), ("रु. 45,000 जमा करावे", "literal digits"),
    ("{{ society_name }} [PERSON_263c6dfd] यांचा", "PII token"), ("{{ secret }} सादर करावे", "unknown slots"),
    ("{{ society_name|upper }}", "filters not allowed"), ("{{ society_name ", "syntax"), ("<NUM> टक्के", "marker"),
])
def test_validate_template_rejects(template, problem):
    assert any(problem in e for e in cl.validate_template(template, key="query.intro"))


def test_validate_template_accepts_names_with_digits_and_item_slots():
    assert cl.validate_template("भोगवटादार वर्ग-२ मधून वर्ग-१ मध्ये {{ area_sqm|mr_num }} चौ.मी.", key="demand.intro") == []
    assert cl.validate_template("{{ requirement_name_mr }} सादर करावे.", key="query_item.generic") == []
    assert cl.validate_template("{{ requirement_name_mr }}", key="query.intro")  # item slot outside items


def test_render_blanks_missing_slots_and_filters_pass_them_through():
    text, missing = cl.render("रक्कम रु. {{ premium|inr|mr_num }}/- दिनांक {{ payment_date|mr_date }} ({{ premium|mr_rupees }})",
                              {"premium": "4564325", "payment_date": None})
    assert text == "रक्कम रु. ४५,६४,३२५/- दिनांक ________ (रुपये पंचेचाळीस लाख चौसष्ट हजार तीनशे पंचवीस फक्त)" and missing == ["payment_date"]


def test_pick_most_specific_matching_clause():
    base = {"template": "x", "status": "canonical", "data_class": "any", "origin": "seed", "verification": "to_verify",
            "source_refs": [], "source_case_ids": [], "updated_at": "", "rule_set_codes": [], "conditions": {}}
    rows = [{**base, "id": "a", "clause_key": "k"},
            {**base, "id": "b", "clause_key": "k", "conditions": {"tenure": "govt_lease"}},
            {**base, "id": "c", "clause_key": "k", "conditions": {"tenure": "govt_lease"}, "rule_set_codes": ["AMEND_2023_03_27"]},
            {**base, "id": "d", "clause_key": "k", "data_class": "synthetic", "origin": "curated"},
            {**base, "id": "e", "clause_key": "k", "conditions": {"unknown_fact": 1}},
            {**base, "id": "f", "clause_key": "k", "status": "candidate", "conditions": {"tenure": "govt_lease", "premium_paid": True}}]
    lease = {"tenure": "govt_lease", "validators_failed": set()}
    assert cl.pick(rows, "k", lease, {"RULES_2019"}, "synthetic")["id"] == "b"
    assert cl.pick(rows, "k", lease, {"RULES_2019", "AMEND_2023_03_27"}, "synthetic")["id"] == "c"
    assert cl.pick(rows, "k", {"tenure": "occupancy_class_2"}, set(), "synthetic")["id"] == "d"  # curated class-specific beats seed
    assert cl.pick(rows, "k", {"tenure": "occupancy_class_2"}, set(), "real")["id"] == "a"  # synthetic wording never for real
    assert cl.pick(rows, "missing", lease, set(), "real") is None


# ------------------------------------------------------------------------------------------------
# Mining
# ------------------------------------------------------------------------------------------------
QUERY_TEXT = """जिल्हाधिकारी कार्यालय, पुणे
(महसूल शाखा)
क्रमांक : महसूल/कक्ष-२/रूपांतर/एसआर-४५/२०२४
दिनांक : ११/०३/२०२४
त्रुटी पत्र
प्रति,
अध्यक्ष / सचिव,
सुयोग सहकारी गृहनिर्माण संस्था मर्यादित,
विषय : भोगवटादार वर्ग-२ मधील जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत.
संदर्भ : आपला अर्ज दिनांक ०५/०२/२०२४.
उपरोक्त संदर्भीय अर्जाची छाननी केली असता खालील त्रुटी आढळून आल्या आहेत :
१. संस्थेच्या सभासदांची यादी सचिवांच्या स्वाक्षरीने प्रमाणित करून सादर करावी.
२. [PERSON_263c6dfd]
तरी वरील त्रुटींची पूर्तता करावी, अन्यथा प्रकरण नस्तीबंद करण्यात येईल.
उपजिल्हाधिकारी (महसूल)
करिता जिल्हाधिकारी, पुणे"""
ORDER_TEXT = """जिल्हाधिकारी कार्यालय, पुणे
आदेश
वाचले :
१) सुयोग सहकारी गृहनिर्माण संस्था मर्यादित यांचा अर्ज दिनांक ०५/०२/२०२४.
२) संस्थेने शासन जमा केलेले चलन (GRN MH004512345678202425) दिनांक १०/०६/२०२४.
ज्याअर्थी, सुयोग सहकारी गृहनिर्माण संस्था मर्यादित यांनी मौजे कोथरूड येथील नगर भूमापन क्रमांक १२३४/अ, क्षेत्र १२५०.५० चौ.मी. ही जमीन रूपांतरित करण्याबाबत अर्ज केला आहे;
आणि ज्याअर्थी, संस्थेने देय नजराणा रक्कम रु. ४५,६४,३२५/- दिनांक १०/०६/२०२४ रोजी चलनाद्वारे शासन जमा केली आहे;
त्याअर्थी, मी, जिल्हाधिकारी, पुणे, नियम, २०१९ मधील तरतुदींनुसार परवानगी देण्यात येत आहे.
अटी व शर्ती :
१. मिळकत पत्रिकेवरील धारणाधिकाराची नोंद भोगवटादार वर्ग-१ अशी करण्यात यावी.
जिल्हाधिकारी, पुणे
प्रत : नगर भूमापन अधिकारी, पुणे यांना मिळकत पत्रिकेवर नोंद घेण्यासाठी."""


def dkt001_card() -> dict:
    ev = lambda v, t="APPLICATION": {"value": v, "exhibit_type": t}  # noqa: E731
    return {"case": {"case_id": "x", "case_ref": "DKT-001"},
            "facts": {"society": {"name": ev("सुयोग सहकारी गृहनिर्माण संस्था मर्यादित")},
                      "land": {"village": ev("कोथरूड"), "district": ev("पुणे"), "taluka": ev("हवेली"), "cts_no": ev("1234/अ"),
                               "area_sqm": ev("1250.50")},
                      "application": {"date": ev("2024-02-05")}, "order": {"premium_amount": ev("4564325.00")},
                      "queries": [{"exhibit_id": "q1", "letter_date": ev("2024-03-11"), "items": [
                          {"raw": "संस्थेच्या सभासदांची यादी सचिवांच्या स्वाक्षरीने प्रमाणित करून सादर करावी.", "requirement_codes": ["MEMBER_LIST"]},
                          {"raw": "[PERSON_263c6dfd]", "requirement_codes": [], "mapping": "not_requirement"}]}],
                      "payments": [{"grn": ev("MH004512345678202425"), "payment_date": ev("2024-06-10"), "amount": ev("4564325.00")}],
                      "inspections": []},
            "events": []}


def test_mining_splits_letters_by_structure_and_delexicalises_case_facts():
    card = dkt001_card()
    q = cl.mine_exhibit(card, {"exhibit_id": "q1", "type": "QUERY_LETTER", "pages": [8]}, {8: QUERY_TEXT})
    by = {m.key: m for m in q}
    assert set(by) == {"query.subject", "query.reference", "query.intro", "query_item.MEMBER_LIST", "query_item.not_requirement",
                       "query.closing"}  # letterhead, number, date, addressee and signature are not clauses
    assert by["query.reference"].template == "आपला अर्ज दिनांक {{ application_date|mr_date }}."
    assert by["query_item.not_requirement"].flags["has_pii"]
    assert not by["query_item.MEMBER_LIST"].flags
    o = cl.mine_exhibit(card, {"exhibit_id": "o1", "type": "COLLECTOR_ORDER", "pages": [14]}, {14: ORDER_TEXT})
    by = {m.key: m for m in o}
    assert {"order.read.application", "order.read.challan", "order.whereas.application", "order.whereas.payment", "order.operative",
            "order.condition.mutation", "order.copy_to.ctso"} <= set(by)
    assert by["order.read.challan"].template == "संस्थेने शासन जमा केलेले चलन (GRN {{ grn }}) दिनांक {{ payment_date|mr_date }}."
    pay = by["order.whereas.payment"].template
    assert "{{ premium|inr|mr_num }}" in pay and "{{ payment_date|mr_date }}" in pay and not cl.literal_digits(pay)
    whereas = by["order.whereas.application"].template
    assert "{{ society_name }}" in whereas and "{{ cts_no|mr_num }}" in whereas and "{{ area_sqm|mr_num }}" in whereas
    assert by["order.operative"].flags.get("needs_slots")  # "२०१९" is not a case fact: the curator must slot it


def test_cluster_groups_near_duplicates():
    from rci.adapters.embeddings import LocalHashEmbedder

    texts = ["तरी वरील त्रुटींची पूर्तता तीस दिवसांत करावी.", "तरी वरील त्रुटींची पूर्तता पंधरा दिवसांत करावी.",
             "सदर जमिनीचा वापर निवासी प्रयोजनासाठीच करण्यात यावा."]
    vecs = LocalHashEmbedder()._embed(texts)
    ids = cl.cluster(vecs, [0, 1, 2], 0.6)
    assert ids[0] == ids[1] != ids[2]


# ------------------------------------------------------------------------------------------------
# Cross-check and narrative
# ------------------------------------------------------------------------------------------------
def test_crosscheck_traces_every_figure():
    card = dkt001_card()
    allowed = crosscheck.allowed_from(card, fee=fee(), extra=[2])
    ok = ("दिनांक ०५/०२/२०२४ रोजी अर्ज; ५ फेब्रुवारी २०२४; नगर भूमापन क्रमांक १२३४/अ, क्षेत्र १२५०.५० चौ.मी.; रु. ४५,६४,३२५/-; "
          "GRN MH004512345678202425; भोगवटादार वर्ग-२; २ मुद्दे; 2024-06-10")
    assert crosscheck.check(ok, allowed) == []
    bad = crosscheck.check("दिनांक ०६/०२/२०२४ रोजी रु. ४५,६४,३२६/- व क्षेत्र १२५१ चौ.मी.; ७ मार्च २०२४", allowed)
    assert {(v["kind"], v["value"]) for v in bad} == {("date", "06/02/2024"), ("number", "45,64,326"), ("number", "1251"),
                                                       ("date", "7 मार्च 2024")}


def test_narrative_llm_is_checked_against_the_facts():
    card = find_card(lambda c: bool(c["facts"]["payments"]) and bool(c["facts"]["queries"]))
    base = narrative.template(card)
    dates = [e for e in (card["facts"]["queries"][0]["letter_date"]["value"], card["facts"]["payments"][0]["payment_date"]["value"])]
    assert base.index(narrative.mr_date(dates[0])) < base.index(narrative.mr_date(dates[1]))  # chronological
    same = FakeLLMClient(responder=lambda t, u, s: {"text_mr": "सुधारित: " + base}, real_data_ok=True)
    assert narrative.narrative(card, fee=None, llm=same, data_class="synthetic")["source"] == "llm"
    invented = FakeLLMClient(responder=lambda t, u, s: {"text_mr": base + " दंड रु. ९९,९९९/- आकारला."})
    r = narrative.narrative(card, fee=None, llm=invented, data_class="synthetic")
    assert r["source"] == "template" and "untraceable" in r["check"] and r["text"] == base
    dropped = FakeLLMClient(responder=lambda t, u, s: {"text_mr": "संस्थेने अर्ज केला आहे."})
    assert "dropped" in narrative.narrative(card, fee=None, llm=dropped, data_class="synthetic")["check"]
    pii = FakeLLMClient(responder=lambda t, u, s: {"text_mr": base + " [PERSON_263c6dfd]"})
    assert "PII" in narrative.narrative(card, fee=None, llm=pii, data_class="synthetic")["check"]
    # residency: a public-API client never sees real case facts
    assert "llm error" in narrative.narrative({**card, "case": {**card["case"], "data_class": "real"}}, fee=None, llm=invented,
                                              data_class="real")["check"]


# ------------------------------------------------------------------------------------------------
# The four templates
# ------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def paid_card() -> dict:
    return find_card(lambda c: bool(c["facts"]["payments"]) and c["facts"]["land"]["tenure"]["value"] == "govt_lease"
                     and bool(c["facts"]["queries"]))


def test_all_four_drafts_render_in_both_modes(paid_card, tmp_path):
    inp = inputs(paid_card)
    for code in TEMPLATES:
        internal = build(code, inp, "internal")
        issue = build(code, inp, "issue")
        di = render.render_docx(code, internal.context, internal=True, cfg=inp.cfg)
        ds = render.render_docx(code, issue.context, internal=False, cfg=inp.cfg)
        ti, ts = render.docx_all_text(di), render.docx_all_text(ds)
        # internal: watermark, provenance markers + notes naming the precedents; issue: none of it
        assert "AI-सहाय्यित मसुदा" in ti and MARK_OPEN in ti and internal.notes
        assert "AI-सहाय्यित" not in ts and MARK_OPEN not in ts and not issue.notes
        leak = render.leak_check(ds, issue.forbidden, lambda t: False, cfg=inp.cfg)
        assert leak["ok"], (code, leak)
        assert "SYN-P-099" in issue.forbidden and OTHER_SOC in issue.forbidden
        assert not render.leak_check(di, internal.forbidden, lambda t: False, cfg=inp.cfg)["ok"]  # the internal render is not issuable
        assert issue.crosscheck == [] and render.docx_paragraphs(ds)
        # the Devanagari font is on the runs (complex-script slot)
        assert f'w:cs="{inp.cfg.value("font")}"' in Document(io.BytesIO(ds)).styles.element.xml
    q = build("QUERY_LETTER", inp, "issue")
    items = q.context["items"]
    assert len(items) == 2 and "मिळकत पत्रिका" in items[0]["text"] and "सभासदांची यादी" in items[1]["text"]  # SUPERSEDED left out
    d = build("DEMAND_LETTER", inp, "issue")
    paras = " ".join(render.docx_paragraphs(render.render_docx("DEMAND_LETTER", d.context, internal=False, cfg=inp.cfg)))
    assert "रु. ४५,६४,३२५/-" in paras and "(रुपये पंचेचाळीस लाख चौसष्ट हजार तीनशे पंचवीस फक्त)" in paras
    assert "२०२४-२५" in paras and "७३,०००" in paras and "५ टक्के" in paras and d.complete
    o = build("COLLECTOR_ORDER", inp, "issue")
    assert o.complete and paid_card["facts"]["payments"][0]["grn"]["value"] in " ".join(o.context["whereas"] + [r["text"] for r in o.context["read"]])


def test_missing_inputs_render_as_blanks_not_guesses():
    card = find_card(lambda c: not c["facts"]["payments"])
    inp = inputs(card, analysis(total=None))
    d = build("DEMAND_LETTER", inp, "issue")
    assert not d.complete and "premium" in d.missing_slots and any("premium not computed" in w for w in d.warnings)
    o = build("COLLECTOR_ORDER", inp, "issue")
    assert not o.complete and {"grn", "payment_date", "paid_amount"} <= set(o.missing_slots)
    n = build("SCRUTINY_NOTE", inp, "issue")
    assert n.complete  # a note may be put up before the fee exists
    none = inputs(card, {**analysis(), "gaps": []})
    q = build("QUERY_LETTER", none, "issue")
    assert not q.complete and any("no gaps" in w for w in q.warnings)


def test_leak_check_catches_a_clause_that_names_another_case(paid_card):
    rows = seed_rows()
    bad = next(r for r in rows if r["clause_key"] == "query.intro")
    bad.update(template="उपरोक्त अर्जाची छाननी केली असता (" + OTHER_SOC + " प्रमाणे) खालील त्रुटी आढळल्या :",
               source_refs=[{"case_id": OTHER_ID, "case_ref": "SYN-P-099", "society_name": OTHER_SOC}], data_class="synthetic",
               origin="curated")
    inp = inputs(paid_card, clauses=rows)
    m = build("QUERY_LETTER", inp, "issue")
    data = render.render_docx("QUERY_LETTER", m.context, internal=False, cfg=inp.cfg)
    leak = render.leak_check(data, m.forbidden, lambda t: False, cfg=inp.cfg)
    assert not leak["ok"] and {"kind": "other_case", "value": OTHER_SOC} in leak["found"]
    # a PII token of this case is allowed (it is detokenised on download); one that is not this case's is a leak
    ctx = dict(build("QUERY_LETTER", inputs(paid_card), "issue").context)
    ctx["to"] = ["अध्यक्ष [PERSON_aaaaaaaa],"] + ctx["to"]
    data = render.render_docx("QUERY_LETTER", ctx, internal=False, cfg=inp.cfg)
    assert render.leak_check(data, [], lambda t: t == "[PERSON_aaaaaaaa]", cfg=inp.cfg)["ok"]
    foreign = render.leak_check(data, [], lambda t: False, cfg=inp.cfg)
    assert foreign["found"] == [{"kind": "foreign_pii_token", "value": "[PERSON_aaaaaaaa]"}]


def test_templates_build_from_code(tmp_path):
    paths = build_all(tmp_path, "Mangal", 11)
    assert len(paths) == 4
    for p in paths:
        xml = Document(str(p)).styles.element.xml
        assert 'w:cs="Mangal"' in xml and 'w:bidi="mr-IN"' in xml


def test_officer_diff():
    gen = ["क्रमांक : X", "१. अद्ययावत मिळकत पत्रिका सादर करावी.", "२. सभासद यादी सादर करावी.", "तरी वरील त्रुटींची पूर्तता तीस दिवसांत करावी."]
    up = ["क्रमांक : X", "१. अद्ययावत मिळकत पत्रिका सादर करावी.", "२. प्रमाणित सभासद यादी सादर करावी.", "३. नवीन मुद्दा.",
          "तरी वरील त्रुटींची पूर्तता पंधरा दिवसांत करावी. दिनांक १५/१०/२०२६"]
    d = render.diff(gen, up, [{"clause_id": "a", "clause_key": "query_item.PROPERTY_CARD_RECENT", "text": "अद्ययावत मिळकत पत्रिका सादर करावी."},
                              {"clause_id": "b", "clause_key": "query_item.MEMBER_LIST", "text": "सभासद यादी सादर करावी."},
                              {"clause_id": "c", "clause_key": "query.closing", "text": "तरी वरील त्रुटींची पूर्तता तीस दिवसांत करावी."}])
    assert d["paragraphs"]["unchanged"] == 2 and d["paragraphs"]["changed"] + d["paragraphs"]["inserted"] >= 2
    assert {c["clause_key"]: c["kept"] for c in d["clauses"]} == {"query_item.PROPERTY_CARD_RECENT": True,
                                                                  "query_item.MEMBER_LIST": True, "query.closing": False}
    assert d["clauses_edited"] == 1 and "15/10/2026" in d["numbers_added"] and 0 < d["similarity"] < 1
