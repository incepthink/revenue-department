"""Synthetic precedent pool: approved, locked, data_class=synthetic cases for developing and testing Phase 3
while the real approved cards are still being reviewed.

    rci seed-precedents --n 40            # cases SYN-P-001 … SYN-P-040 (idempotent: existing refs are skipped)

EVERYTHING HERE IS INVENTED: societies, dates, areas, ASR rates and the 5 % premium rate (the rate printed on the
synthetic sample dockets). The pool can never influence a real case: matching filters on data_class. Delete it with
`rci seed-precedents --delete` before real precedents are loaded if it is no longer wanted.

Each case is built the way an approved card is: per-exhibit field evidence (one text page per exhibit, so every
citation opens a real page), validators, `assemble_card` with the reviewed query mapping, then the approval
bookkeeping of review/service.approve (locked card, case_requirements, case_events, facet embeddings). Scenario
probabilities are chosen to give the engine something to find, e.g. occupancy certificates were asked for mostly
before the 2023 amendment and society registration certificates mostly after it (PRACTICE vs PRACTICE_OLD_RULES).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from rci.config import Parameters, get_settings
from rci.db.models import (
    Case,
    CaseCard,
    CaseEvent,
    CaseRequirement,
    Document,
    EventCatalog,
    Exhibit,
    ExhibitPage,
    Office,
    Page,
    RequirementCatalog,
)
from rci.pipeline.card import CardExhibit, assemble_card, content_hash, doc_date_of
from rci.pipeline.normalise import ascii_digits, devanagari_digits
from rci.pipeline.runner import audit
from rci.pipeline.validators import ExhibitFacts, run_validators
from rci.review.summary import summary

D = devanagari_digits
GENERATOR = "synthetic-generator@rci.local"
PIPELINE_VERSION = "synthetic-1"
AMENDMENT_2023 = dt.date(2023, 3, 27)
# (village, taluka, district, ASR open-land rate ₹/sq m in ASR year 2024-25) — same invented figures as
# eval/samples/asr/asr_synthetic_pune.csv; other years scale by 5 % a year.
VILLAGES = [("कोथरूड", "हवेली", "पुणे", 48500), ("एरंडवणे", "हवेली", "पुणे", 52300), ("बाणेर", "हवेली", "पुणे", 39800)]
SOCIETY_WORDS = ["शिवनेरी", "सह्याद्री", "गंगोत्री", "अमृत", "निसर्ग", "सिद्धिविनायक", "पार्वती", "वसंत", "श्रीराम", "सुखशांती",
                 "आनंदवन", "गुलमोहर", "चैतन्य", "ओंकार", "मंगलमूर्ती", "सरस्वती", "तुळशी", "केदार", "गौरी", "मल्हार",
                 "नंदनवन", "पुष्पक", "राजहंस", "स्वप्नपूर्ती", "यशोदा", "कल्पतरू", "हिमालय", "सागर", "प्रगती", "संकल्प"]
SOCIETY_SUFFIX = " सहकारी गृहनिर्माण संस्था मर्यादित"
SAME_AS_SAMPLE = "गणेश कृपा" + SOCIETY_SUFFIX  # the society of sample docket DKT-002 (shows same-society exclusion)
RATE_PCT = Decimal("5")
PREMIUM_HEAD = "0029-00-800-01 Other Receipts (Land Revenue)"
QUERY_TEXT = {
    "PROPERTY_CARD_RECENT": "अद्ययावत मिळकत पत्रिका सादर करावी.",
    "NO_DUES_LEASE_RENT": "भाडेपट्टा थकबाकी नसल्याचा दाखला सादर करावा.",
    "SGM_RESOLUTION": "सर्वसाधारण सभा अर्जाच्या दिनांकानंतर घेतल्याने सुधारित ठराव सादर करावा.",
    "MEMBER_LIST": "प्रमाणित सभासद यादी सादर करावी.",
    "OCCUPANCY_CERTIFICATE": "इमारतीचे भोगवटा प्रमाणपत्र सादर करावे.",
    "SOCIETY_REGISTRATION": "संस्था नोंदणी प्रमाणपत्राची प्रत सादर करावी.",
    "UNDERTAKING_AFFIDAVIT": "संस्थेचे प्रतिज्ञापत्र सादर करावे.",
    "CTSO_MEASUREMENT": "नगर भूमापन अधिकारी यांचेकडून जागेची मोजणी करून घ्यावी.",
    None: "जागेवरील अतिक्रमणाबाबत खुलासा सादर करावा.",  # mapped "not a requirement"
}
LETTERHEAD = [(None, "", "जिल्हाधिकारी कार्यालय, पुणे"), (None, "", "(महसूल शाखा)")]
SIGNATURE = ["उपजिल्हाधिकारी (महसूल)", "करिता जिल्हाधिकारी, पुणे"]
SUBJECT = {"govt_lease": "भाडेपट्ट्याने दिलेली शासकीय जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत.",
           "occupancy_class_2": "भोगवटादार वर्ग-२ मधील जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत."}
SUPPLY_TYPE = {"MEMBER_LIST": "MEMBER_LIST", "OCCUPANCY_CERTIFICATE": "OCCUPANCY_CERT", "SOCIETY_REGISTRATION": "SOCIETY_REG_CERT",
               "UNDERTAKING_AFFIDAVIT": "AFFIDAVIT", "NO_DUES_LEASE_RENT": "NOC", "PROPERTY_CARD_RECENT": "PROPERTY_CARD"}


def _dmy(d: dt.date) -> str:
    return D(d.strftime("%d/%m/%Y"))


def _inr(x: Decimal) -> str:
    """Indian digit grouping, e.g. 4564325 → ४५,६४,३२५."""
    s = str(int(x))
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return D(",".join([g for g in [head] + groups if g] + [tail]) if head or groups else tail)


def asr_rate(base_2024: int, year: int) -> Decimal:
    return (Decimal(base_2024) * Decimal("1.05") ** (year - 2024)).quantize(Decimal("100"), rounding=ROUND_HALF_UP)


def fiscal_year(d: dt.date) -> int:
    return d.year if d.month >= 4 else d.year - 1


class _Docket:
    """Collects exhibits (one text page each) with field evidence in the card's evidence format."""

    def __init__(self, case_ref: str):
        self.case_ref = case_ref
        self.exhibits: list[dict] = []

    def add(self, etype: str, stage: str, lines: list[tuple[str | None, str, object]], lists: dict | None = None,
            tail: list[str] | None = None) -> dict:
        """lines = [(field or None, label text, (value, raw) or raw)]; lists = {field: [(value, raw), ...]};
        tail = plain lines after the lists (closing sentence, signature)."""
        seq = len(self.exhibits) + 1
        text_lines, fields = [], {}
        for fname, label, v in lines:
            value, raw = v if isinstance(v, tuple) else (v, str(v))
            line = f"{label} : {raw}" if label else raw
            text_lines.append(line)
            if fname:
                fields[fname] = self._ev(value, raw, seq, line)
        for fname, items in (lists or {}).items():
            evs = []
            for i, (value, raw) in enumerate(items, start=1):
                line = f"{D(str(i))}. {raw}"
                text_lines.append(line)
                evs.append(self._ev(value, raw, seq, line))
            fields[fname] = evs
        text_lines += tail or []
        ex = {"seq": seq, "type": etype, "stage": stage, "fields": fields, "text": "\n".join(text_lines)}
        self.exhibits.append(ex)
        return ex

    @staticmethod
    def _ev(value, raw: str, seq: int, snippet: str) -> dict:
        return {"value": value, "raw": raw, "page_seq": seq, "doc_page_no": seq, "document": "synthetic-docket.pdf",
                "page_id": None, "snippet": snippet, "method": "manual", "confidence": 1.0, "alternatives": []}


def _scenario(rng: random.Random, i: int, start: dt.date, span_days: int) -> dict:
    lease = rng.random() < 0.45
    app = start + dt.timedelta(days=rng.randrange(span_days))
    before = app < AMENDMENT_2023
    village = VILLAGES[rng.randrange(len(VILLAGES))]
    return {
        "tenure": "govt_lease" if lease else "occupancy_class_2",
        "village": village, "app": app,
        "area": Decimal(rng.randrange(40000, 250000)) / 100,
        "stale_card": rng.random() < 0.4,
        "area_mismatch": rng.random() < 0.3,
        "sgm_late": rng.random() < 0.12,
        "member_q": rng.random() < 0.3,
        "occupancy_q": rng.random() < (0.6 if before else 0.1),
        "regcert_q": rng.random() < (0.1 if before else 0.5),
        "affidavit_q": rng.random() < 0.25,
        "dues_q": lease and rng.random() < 0.75,
        "encroachment": rng.random() < 0.15,
        "rejected": rng.random() < 0.08,
        "society": SAME_AS_SAMPLE if i == 3 else SOCIETY_WORDS[i % len(SOCIETY_WORDS)] + (f" {D(str(i // len(SOCIETY_WORDS) + 1))}" if i >= len(SOCIETY_WORDS) else "") + SOCIETY_SUFFIX,
    }


def build_docket(case_ref: str, s: dict, rng: random.Random) -> tuple[_Docket, dict[int, list[str]], dict]:
    """(docket, query-item index -> requirement codes, summary facts for the caller)."""
    k = _Docket(case_ref)
    village, taluka, district, asr_2024 = s["village"]
    soc, app, area = s["society"], s["app"], s["area"]
    area_raw = D(f"{area:.2f}") + " चौ.मी."
    cts = str(rng.randrange(100, 2000))
    reg = f"PNA/HSG/(TC)/{rng.randrange(100, 9999)}/{rng.randrange(1965, 2005)}"
    tenure_mr = {"govt_lease": "शासकीय भाडेपट्टा", "occupancy_class_2": "भोगवटादार वर्ग-२"}[s["tenure"]]
    day = lambda base, lo, hi: base + dt.timedelta(days=rng.randrange(lo, hi))  # noqa: E731

    k.add("APPLICATION", "filing", [
        ("application_date", "दिनांक", (app.isoformat(), _dmy(app))), (None, "", "प्रति, मा. जिल्हाधिकारी, पुणे"),
        ("society_name", "अर्जदार संस्था", (soc, soc)), ("registration_no", "नोंदणी क्रमांक", (reg, reg)),
        ("village", "मौजे", (village, village)), ("taluka", "ता.", (taluka, taluka)), ("district", "जि.", (district, district)),
        ("cts_no", "नगर भूमापन क्रमांक", (cts, D(cts))), ("area_sqm", "क्षेत्र", (f"{area:.2f}", area_raw)),
        ("tenure", "सध्याचा धारणाधिकार", (s["tenure"], tenure_mr)),
        (None, "", "सदर जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्यास परवानगी मिळावी, ही विनंती."),
    ])
    card_issue = day(app, -420, -200) if s["stale_card"] else day(app, -80, -5)
    k.add("PROPERTY_CARD", "filing", [
        (None, "", "मिळकत पत्रिका"), ("village", "गाव / पेठ", (village, village)), ("cts_no", "नगर भूमापन क्रमांक", (cts, D(cts))),
        ("area_sqm", "क्षेत्र", (f"{area:.2f}", area_raw)), ("tenure", "सत्ता प्रकार", (s["tenure"], tenure_mr)),
        ("holder_name", "धारकाचे नाव", (soc, soc)), ("issue_date", "प्रत दिल्याचा दिनांक", (card_issue.isoformat(), _dmy(card_issue))),
    ])
    sgm = day(app, 3, 20) if s["sgm_late"] else day(app, -60, -5)
    total = rng.randrange(12, 80)
    present = rng.randrange(total // 2 + 1, total + 1)
    k.add("SGM_RESOLUTION", "filing", [
        ("society_name", "संस्था", (soc, soc)), ("meeting_date", "विशेष सर्वसाधारण सभा दिनांक", (sgm.isoformat(), _dmy(sgm))),
        ("members_total", "एकूण सभासद संख्या", (total, D(str(total)))), ("members_present", "उपस्थित सभासद संख्या", (present, D(str(present)))),
        ("resolution_no", "ठराव क्रमांक", ("1", D("1"))),
    ])
    if s["tenure"] == "govt_lease":
        ld = dt.date(rng.randrange(1960, 1992), rng.randrange(1, 13), rng.randrange(1, 28))
        rent = Decimal(rng.randrange(6, 40) * 100)
        k.add("LEASE_SANAD", "filing", [
            ("lease_date", "भाडेपट्टा करारनामा दिनांक", (ld.isoformat(), _dmy(ld))), ("lessee", "भाडेपट्टेदार", (soc, soc)),
            ("cts_no", "नगर भूमापन क्रमांक", (cts, D(cts))), ("area_sqm", "क्षेत्र", (f"{area:.2f}", area_raw)),
            ("lease_period_years", "कालावधी (वर्षे)", (99, D("99"))), ("annual_rent", "वार्षिक भाडे रु.", (f"{rent:.2f}", _inr(rent))),
        ])
    # documents filed with the application unless the office later asked for them
    for code, etype, p_filed in (("MEMBER_LIST", "MEMBER_LIST", 0.9), ("SOCIETY_REGISTRATION", "SOCIETY_REG_CERT", 0.85),
                                 ("OCCUPANCY_CERTIFICATE", "OCCUPANCY_CERT", 0.35), ("UNDERTAKING_AFFIDAVIT", "AFFIDAVIT", 0.8),
                                 ("NO_DUES_LEASE_RENT", "NOC", 0.9)):
        queried = {"MEMBER_LIST": s["member_q"], "SOCIETY_REGISTRATION": s["regcert_q"], "OCCUPANCY_CERTIFICATE": s["occupancy_q"],
                   "UNDERTAKING_AFFIDAVIT": s["affidavit_q"], "NO_DUES_LEASE_RENT": s["dues_q"]}[code]
        if code == "NO_DUES_LEASE_RENT" and s["tenure"] != "govt_lease":
            continue
        if not queried and rng.random() < p_filed:
            k.add(etype, "filing", [(None, "", f"{etype} — {soc}")])

    measured = (area * Decimal("1.05")).quantize(Decimal("0.01")) if s["area_mismatch"] else area
    insp = day(app, 15, 45)
    k.add("FIELD_REPORT", "internal", [
        ("inspection_date", "स्थळ पाहणी दिनांक", (insp.isoformat(), _dmy(insp))), ("cts_no", "नगर भूमापन क्रमांक", (cts, D(cts))),
        ("area_sqm", "प्रत्यक्ष मोजणी क्षेत्र", (f"{measured:.2f}", D(f"{measured:.2f}") + " चौ.मी.")),
        ("encroachment", "अतिक्रमण", (s["encroachment"], "अतिक्रमण आढळून आले" if s["encroachment"] else "अतिक्रमण आढळले नाही")),
    ])
    # query letter
    codes = [c for c, on in (("PROPERTY_CARD_RECENT", s["stale_card"]), ("NO_DUES_LEASE_RENT", s["dues_q"]),
                             ("SGM_RESOLUTION", s["sgm_late"]), ("MEMBER_LIST", s["member_q"]),
                             ("OCCUPANCY_CERTIFICATE", s["occupancy_q"]), ("SOCIETY_REGISTRATION", s["regcert_q"]),
                             ("UNDERTAKING_AFFIDAVIT", s["affidavit_q"]),
                             ("CTSO_MEASUREMENT", s["area_mismatch"] and rng.random() < 0.5)) if on]
    if s["encroachment"]:
        codes.append(None)
    item_codes: dict[int, list[str]] = {}
    last = insp
    qd = cd = None
    to = [(None, "", "प्रति,"), (None, "", "अध्यक्ष / सचिव,"), (None, "", f"{soc}, {village}, पुणे")]
    if codes:
        qd = day(insp, 5, 30)
        items = [(QUERY_TEXT[c], QUERY_TEXT[c]) for c in codes]
        item_codes = {i: ([c] if c else []) for i, c in enumerate(codes)}
        ref = f"महसूल/कक्ष-२/रूपांतर/{case_ref}"
        k.add("QUERY_LETTER", "internal", LETTERHEAD + [
            ("ref_no", "क्रमांक", (ref, ref)), ("letter_date", "दिनांक", (qd.isoformat(), _dmy(qd))), (None, "", "त्रुटी पत्र"), *to,
            (None, "विषय", SUBJECT[s["tenure"]]), (None, "संदर्भ", f"आपला अर्ज दिनांक {_dmy(app)}."),
            (None, "", "उपरोक्त संदर्भीय अर्जाची छाननी केली असता खालील त्रुटी आढळून आल्या आहेत :")], {"query_items": items},
            tail=["तरी वरील त्रुटींची पूर्तता तीस दिवसांत करावी, अन्यथा प्रकरण नस्तीबंद करण्यात येईल.", *SIGNATURE])
        rd = day(qd, 15, 60)
        k.add("QUERY_REPLY", "query_response", [("letter_date", "दिनांक", (rd.isoformat(), _dmy(rd))),
                                                (None, "", "उपरोक्त पत्रातील त्रुटींची पूर्तता करीत आहोत.")])
        for c in codes:
            if c == "PROPERTY_CARD_RECENT":
                ni = day(rd, -20, -3)
                k.add("PROPERTY_CARD", "query_response", [
                    (None, "", "मिळकत पत्रिका (अद्ययावत)"), ("village", "गाव / पेठ", (village, village)),
                    ("cts_no", "नगर भूमापन क्रमांक", (cts, D(cts))), ("area_sqm", "क्षेत्र", (f"{area:.2f}", area_raw)),
                    ("tenure", "सत्ता प्रकार", (s["tenure"], tenure_mr)), ("holder_name", "धारकाचे नाव", (soc, soc)),
                    ("issue_date", "प्रत दिल्याचा दिनांक", (ni.isoformat(), _dmy(ni)))])
            elif c in SUPPLY_TYPE and rng.random() < 0.85:
                k.add(SUPPLY_TYPE[c], "query_response", [(None, "", f"{SUPPLY_TYPE[c]} — {soc}")])
        last = rd
    if s["area_mismatch"]:
        cd = day(last, 10, 40)
        k.add("CTSO_REPORT", "internal", [("inspection_date", "मोजणी दिनांक", (cd.isoformat(), _dmy(cd))),
                                          ("cts_no", "नगर भूमापन क्रमांक", (cts, D(cts))),
                                          ("area_sqm", "मोजणी क्षेत्र", (f"{measured:.2f}", D(f"{measured:.2f}") + " चौ.मी."))])
        last = cd
    fy = fiscal_year(app)
    rate = asr_rate(asr_2024, fy)
    premium = (area * rate * RATE_PCT / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    order_no = f"महसूल/कक्ष-२/आदेश/{case_ref}"
    if s["rejected"]:
        od = day(last, 30, 90)
        k.add("COLLECTOR_ORDER", "final", LETTERHEAD + [
            ("order_no", "क्रमांक", (order_no, order_no)), ("order_date", "दिनांक", (od.isoformat(), _dmy(od))), (None, "", "आदेश"),
            (None, "", "वाचले :"), *_read(soc, village, app, qd, insp, cd, None, None, None),
            (None, "", f"ज्याअर्थी, {soc} यांनी मौजे {village} येथील नगर भूमापन क्रमांक {D(cts)} ही जमीन भोगवटादार वर्ग-१ मध्ये "
                       "रूपांतरित करण्याबाबत अर्ज केला आहे;"),
            (None, "", "आणि ज्याअर्थी, संस्थेने त्रुटींची विहित मुदतीत पूर्तता केलेली नाही;"),
            ("decision", "", ("rejected", "त्याअर्थी, संस्थेचा अर्ज नामंजूर करण्यात येत आहे."))],
            tail=["जिल्हाधिकारी, पुणे"])
    else:
        dd = day(last, 10, 40)
        k.add("DEMAND_LETTER", "internal", LETTERHEAD + [
            (None, "क्रमांक", f"महसूल/कक्ष-२/रूपांतर/{case_ref}"), ("letter_date", "दिनांक", (dd.isoformat(), _dmy(dd))),
            (None, "", "मागणी पत्र"), *to, (None, "विषय", "नजराणा रक्कम शासन जमा करण्याबाबत."),
            (None, "", "उपरोक्त विषयान्वये आपल्या संस्थेची जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्यासाठी देय नजराणा रक्कम "
                       "खालीलप्रमाणे निश्चित करण्यात आली आहे :"),
            (None, "वार्षिक बाजारमूल्य दर तक्ता", f"{D(str(fy))}-{D(str(fy + 1)[-2:])} नुसार दर रु. {_inr(rate)}/- प्रति चौ.मी."),
            (None, "क्षेत्र", area_raw), (None, "नजराणा दर", f"{D(str(RATE_PCT))} टक्के"),
            ("amount", "एकूण रक्कम रु.", (f"{premium:.2f}", _inr(premium) + "/-"))],
            tail=["सदर रक्कम तीस दिवसांत शासन जमा करावी व चलनाची प्रत या कार्यालयास सादर करावी.", *SIGNATURE])
        pd = day(dd, 5, 30)
        grn = f"MH00{rng.randrange(10**11, 10**12)}{fy}{str(fy + 1)[-2:]}"
        k.add("CHALLAN", "final", [("grn", "GRN", (grn, grn)), ("payment_date", "Payment Date", (pd.isoformat(), pd.strftime("%d/%m/%Y"))),
                                   ("amount", "Amount", (f"{premium:.2f}", f"{premium:,.2f}")),
                                   ("head_of_account", "Head of Account", (PREMIUM_HEAD, PREMIUM_HEAD))])
        od = day(pd, 7, 30)
        conditions = ["मिळकत पत्रिकेवरील धारणाधिकाराची नोंद भोगवटादार वर्ग-१ अशी करण्यात यावी.",
                      "सदर जमिनीचा वापर निवासी प्रयोजनासाठीच करण्यात यावा."]
        if s["tenure"] == "govt_lease":
            conditions.append("भाडेपट्ट्याची कोणतीही थकबाकी असल्यास ती संस्थेने शासन जमा करणे बंधनकारक राहील.")
        k.add("COLLECTOR_ORDER", "final", LETTERHEAD + [
            ("order_no", "क्रमांक", (order_no, order_no)), ("order_date", "दिनांक", (od.isoformat(), _dmy(od))), (None, "", "आदेश"),
            (None, "", "वाचले :"), *_read(soc, village, app, qd, insp, cd, dd, grn, pd),
            (None, "", f"ज्याअर्थी, {soc} यांनी मौजे {village}, ता. {taluka}, जि. {district} येथील नगर भूमापन क्रमांक {D(cts)}, "
                       f"क्षेत्र {area_raw} ही जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत अर्ज केला आहे;"),
            ("premium_amount", "", (f"{premium:.2f}", f"आणि ज्याअर्थी, संस्थेने देय नजराणा रक्कम रु. {_inr(premium)}/- दिनांक "
                                                      f"{_dmy(pd)} रोजी चलनाद्वारे शासन जमा केली आहे;")),
            ("decision", "", ("granted", "त्याअर्थी, मी, जिल्हाधिकारी, पुणे, महाराष्ट्र जमीन महसूल (भोगवटादार वर्ग-२ व भाडेपट्ट्याने दिलेल्या "
                                         "जमिनींचे रूपांतरण) नियम, २०१९ मधील तरतुदींनुसार याद्वारे सदर जमीन भोगवटादार वर्ग-१ मध्ये "
                                         "रूपांतरित करण्यास परवानगी देण्यात येत आहे."))],
            tail=["अटी व शर्ती :", *[f"{D(str(i))}. {c}" for i, c in enumerate(conditions, start=1)], "जिल्हाधिकारी, पुणे",
                  "प्रत : नगर भूमापन अधिकारी, पुणे यांना मिळकत पत्रिकेवर नोंद घेण्यासाठी."])
    return k, item_codes, {"premium": premium, "asr_rate": rate, "asr_year": fy}


def _read(soc, village, app, qd, insp, cd, dd, grn, pd) -> list[tuple]:
    """The order's "वाचले" list (static text; the dates are already drawn)."""
    items = [f"{soc} यांचा अर्ज दिनांक {_dmy(app)}."]
    if qd:
        items.append(f"या कार्यालयाचे त्रुटी पत्र दिनांक {_dmy(qd)}.")
    items.append(f"तलाठी, सजा {village} यांचा स्थळ पाहणी अहवाल दिनांक {_dmy(insp)}.")
    if cd:
        items.append(f"नगर भूमापन अधिकारी यांचा मोजणी अहवाल दिनांक {_dmy(cd)}.")
    if dd:
        items.append(f"या कार्यालयाचे मागणी पत्र दिनांक {_dmy(dd)}.")
    if grn:
        items.append(f"संस्थेने शासन जमा केलेले चलन (GRN {grn}) दिनांक {_dmy(pd)}.")
    return [(None, "", f"{D(str(i))}) {t}") for i, t in enumerate(items, start=1)]


def _catalogs(session: Session) -> tuple[list[dict], list[dict]]:
    reqs = [{"code": r.code, "satisfied_by": r.satisfied_by, "query_keywords": r.query_keywords, "applies_when": r.applies_when,
             "status": r.status} for r in session.scalars(select(RequirementCatalog).where(RequirementCatalog.active).order_by(RequirementCatalog.code))]
    events = [{"code": e.code, "source_exhibit_types": e.source_exhibit_types, "date_field": e.date_field, "typical_order": e.typical_order}
              for e in session.scalars(select(EventCatalog).where(EventCatalog.active).order_by(EventCatalog.typical_order))]
    return reqs, events


def create_case(session: Session, case_ref: str, s: dict, rng: random.Random, *, role: str = "precedent",
                office_code: str = "PILOT") -> Case:
    from rci.review.service import build_context, write_embeddings

    office = session.scalar(select(Office).where(Office.code == office_code))
    if office is None:
        raise ValueError(f"unknown office {office_code!r} — run `rci seed`")
    docket, item_codes, _ = build_docket(case_ref, s, rng)
    case = Case(office_id=office.id, case_ref=case_ref, title="SYNTHETIC precedent (generated)", data_class="synthetic",
                role=role, status="approved")
    session.add(case)
    session.flush()
    digest = hashlib.sha256(f"synthetic:{case_ref}".encode()).hexdigest()
    doc = Document(case_id=case.id, seq=1, sha256=digest, filename="synthetic-docket.pdf", mime_type="application/pdf",
                   size_bytes=0, blob_key=f"synthetic/{case_ref}", page_count=len(docket.exhibits), status="processed")
    session.add(doc)
    session.flush()
    card_exhibits = []
    for ex in docket.exhibits:
        page = Page(document_id=doc.id, case_id=case.id, page_no=ex["seq"], seq=ex["seq"], text_redacted=ex["text"],
                    text_redacted_ascii=ascii_digits(ex["text"]), text_sha256=hashlib.sha256(ex["text"].encode()).hexdigest(),
                    ocr_engine="synthetic", ocr_status="done", ocr_confidence=1.0, text_layer_status="unicode",
                    predicted_type=ex["type"], predicted_confidence=1.0, pipeline_version=PIPELINE_VERSION, pii_counts={})
        session.add(page)
        session.flush()
        for f in ex["fields"].values():
            for ev in (f if isinstance(f, list) else [f]):
                ev["page_id"] = str(page.id)
        eid = uuid.uuid5(case.id, f"{ex['type']}:{ex['seq']}-{ex['seq']}")
        dd = doc_date_of(ex["type"], ex["fields"])
        e = Exhibit(id=eid, case_id=case.id, seq=ex["seq"], exhibit_type=ex["type"], stage=ex["stage"], start_page_seq=ex["seq"],
                    end_page_seq=ex["seq"], confidence=1.0, classification_method="manual", origin="manual", status="approved",
                    doc_date=dd, extraction={"fields": ex["fields"], "reviewed": True, "synthetic": True, "methods": ["manual"]})
        session.add(e)
        session.flush()
        session.add(ExhibitPage(exhibit_id=eid, page_id=page.id, position=0))
        card_exhibits.append(CardExhibit(ExhibitFacts(str(eid), ex["seq"], ex["type"], ex["stage"], ex["fields"]), [ex["seq"]],
                                         1.0, "manual", dd))
    q_exhibit = next((ce.facts.exhibit_id for ce in card_exhibits if ce.facts.exhibit_type == "QUERY_LETTER"), None)
    reqs, events = _catalogs(session)
    known = {r["code"] for r in reqs}
    params = Parameters.load(get_settings().parameters_file)
    validations = run_validators([ce.facts for ce in card_exhibits], params)
    card, requirements, evs = assemble_card(
        case={"case_id": str(case.id), "case_ref": case_ref, "office_code": office.code, "data_class": "synthetic",
              "case_type": case.case_type or "CHS_CLASS2_TO_CLASS1"},
        exhibits=card_exhibits, validations=validations, requirement_catalog=reqs, event_catalog=events,
        pipeline_version=PIPELINE_VERSION,
        item_codes=lambda qid, idx, it: [c for c in item_codes.get(idx, []) if c in known] if qid == q_exhibit else [],
    )
    for q in card["facts"]["queries"]:
        for i, it in enumerate(q["items"]):
            it["requirement_codes"] = [c for c in item_codes.get(i, []) if c in known]
            it["mapping"] = "codes" if it["requirement_codes"] else "not_requirement"
    now = dt.datetime.now(dt.timezone.utc)
    card["status"] = "approved"
    card["summary"] = summary(card)
    card["approval"] = {"approved_by": GENERATOR, "approved_at": now.isoformat(), "review_mode": "single", "review_round": 1,
                        "base_card_version": 1, "reviewers": [GENERATOR]}
    card["review"]["open_issues"] = [i for i in card["review"]["open_issues"] if i["kind"] in ("validation_failed", "unverified_parameter")]
    row = CaseCard(case_id=case.id, version=1, card=card, content_hash=content_hash(card), status="approved", locked=True,
                   approved_at=now)
    session.add(row)
    session.flush()
    for r in requirements:
        session.add(CaseRequirement(case_id=case.id, card_id=row.id, requirement_code=r["code"], status=r["status"],
                                    satisfied_by_exhibit_id=uuid.UUID(r["satisfied_by_exhibit_id"]) if r["satisfied_by_exhibit_id"] else None,
                                    was_queried_in=uuid.UUID(r["was_queried_in"]) if r["was_queried_in"] else None,
                                    evidence=r["evidence"], origin="manual"))
    for e in evs:
        session.add(CaseEvent(case_id=case.id, card_id=row.id, event_code=e["code"], seq=e["seq"],
                              event_date=dt.date.fromisoformat(e["date"]) if e["date"] else None, exhibit_id=uuid.UUID(e["exhibit_id"]),
                              evidence=[e["evidence"]] if e["evidence"] else [], origin="manual"))
    f = card["facts"]
    case.society_name = s["society"]
    case.filed_on = s["app"]
    case.decided_on = dt.date.fromisoformat(f["order"]["order_date"]["value"]) if f["order"].get("order_date") else None
    session.flush()
    write_embeddings(session, case, row, build_context(session, case, row, with_llm=False))
    return case


def generate_pool(session: Session, n: int = 40, *, seed: int = 7, prefix: str = "SYN-P", start: dt.date = dt.date(2019, 6, 1),
                  end: dt.date = dt.date(2026, 3, 31)) -> dict:
    created, skipped = [], []
    for i in range(1, n + 1):
        ref = f"{prefix}-{i:03d}"
        rng = random.Random(f"{seed}:{i}")
        s = _scenario(rng, i, start, (end - start).days)
        if session.scalar(select(Case.id).where(Case.case_ref == ref)):
            skipped.append(ref)
            continue
        create_case(session, ref, s, rng)
        created.append(ref)
    audit(session, "synthetic.precedents", actor_kind="cli", created=len(created), skipped=len(skipped), seed=seed, prefix=prefix)
    return {"created": created, "skipped": skipped}


def delete_pool(session: Session, prefix: str = "SYN-P") -> int:
    ids = list(session.scalars(select(Case.id).where(Case.case_ref.like(f"{prefix}-%"), Case.data_class == "synthetic")))
    if ids:
        session.execute(delete(Case).where(Case.id.in_(ids)))
    return len(ids)
