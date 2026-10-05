"""Generate synthetic CHS Class-II → Class-I dockets with ground truth.

All people, numbers and societies are fictitious. Aadhaar/VID numbers are Verhoeff-valid on purpose so
the redaction tests exercise the real checksum path.

Pages are built like searchable scans: a rendered page image (shaped Devanagari) plus an invisible Unicode
text layer. That keeps the samples usable fully offline (no OCR engine needed) while the OCR benchmark can
still run engines against the page images.

    python eval/samples/generate_samples.py            # writes dockets/, expected/, ../ocr_bench/manifest.json

Requires a Devanagari font: RCI_DEVANAGARI_FONT, else Nirmala UI (Windows) or Noto Sans Devanagari (Linux).
"""
from __future__ import annotations

import html
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from rci.pipeline.normalise import devanagari_digits as D
from rci.pipeline.redact import verhoeff

HERE = Path(__file__).resolve().parent
DOCKETS = HERE / "dockets"
EXPECTED = HERE / "expected"
BENCH = HERE.parent / "ocr_bench"

FONT_CANDIDATES = [
    os.environ.get("RCI_DEVANAGARI_FONT"),
    "C:/Windows/Fonts/Nirmala.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
]
PAGE_W, PAGE_H, MARGIN = 595, 842, 50
BODY_PT = 11


def font_path() -> Path:
    for c in FONT_CANDIDATES:
        if c and Path(c).exists():
            return Path(c)
    sys.exit("No Devanagari font found; set RCI_DEVANAGARI_FONT")


def aadhaar(seed: str) -> str:
    """12-digit Verhoeff-valid number, formatted 4-4-4."""
    body = seed[:11]
    n = body + verhoeff.check_digit(body)
    return f"{n[:4]} {n[4:8]} {n[8:]}"


def vid(seed: str) -> str:
    body = seed[:15]
    n = body + verhoeff.check_digit(body)
    return f"{n[:4]} {n[4:8]} {n[8:12]} {n[12:]}"


# ------------------------------------------------------------------------------------------------
# Rendering
# ------------------------------------------------------------------------------------------------
Line = str | tuple[str, str]  # plain text, or (style, text) with style in title|center|right|small


class Renderer:
    def __init__(self, font: Path):
        self.font = font
        self.archive = pymupdf.Archive(str(font.parent))
        self.css = (
            f"@font-face {{font-family: dev; src: url({font.name});}} "
            f"body {{font-family: dev; font-size: {BODY_PT}pt; line-height: 1.35;}} p {{margin: 0;}}"
        )
        self.measure_font = pymupdf.Font(fontfile=str(font))

    def page(self, out: pymupdf.Document, lines: list[Line]) -> list[str]:
        tmp = pymupdf.open()
        tp = tmp.new_page(width=PAGE_W, height=PAGE_H)
        y = MARGIN
        placed: list[tuple[str, float, float]] = []
        for ln in lines:
            style, text = ln if isinstance(ln, tuple) else ("normal", ln)
            if not text:
                y += BODY_PT * 0.8
                continue
            css = {"title": "text-align:center;font-weight:bold;font-size:14pt;",
                   "center": "text-align:center;", "right": "text-align:right;",
                   "small": "font-size:9pt;"}.get(style, "")
            rect = pymupdf.Rect(MARGIN, y, PAGE_W - MARGIN, y + 300)
            spare, _ = tp.insert_htmlbox(rect, f'<p style="{css}">{html.escape(text)}</p>', css=self.css,
                                         archive=self.archive, scale_low=1)
            used = 300 - spare
            placed.append((text, y, 14 if style == "title" else (9 if style == "small" else BODY_PT)))
            y += used + 3
            if y > PAGE_H - MARGIN:
                raise ValueError(f"page overflow at: {text[:40]}")
        pix = tp.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        page = out.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_image(page.rect, stream=pix.tobytes("jpg", jpg_quality=70))
        page.insert_font(fontname="dev", fontfile=str(self.font))
        width = PAGE_W - 2 * MARGIN
        for text, top, size in placed:
            fs = size
            length = self.measure_font.text_length(text, fontsize=fs)
            if length > width:
                fs = max(2.0, fs * width / length)
            page.insert_text((MARGIN, top + size), text, fontname="dev", fontsize=fs, render_mode=3)
        return [t for t, _, _ in placed]


# ------------------------------------------------------------------------------------------------
# Docket model
# ------------------------------------------------------------------------------------------------
@dataclass
class Exhibit:
    type: str
    stage: str
    pages: list[list[Line]]


@dataclass
class Docket:
    case_ref: str
    files: list[tuple[str, list[Exhibit]]]
    facts: dict
    validations: dict
    requirements: dict
    events: list[str]
    pii: list[str]
    notes: str = ""
    field_checks: list[dict] = field(default_factory=list)


def office_header(ref: str, date: str) -> list[Line]:
    return [("center", "जिल्हाधिकारी कार्यालय, पुणे"), ("center", "(महसूल शाखा)"),
            f"क्रमांक : {ref}", f"दिनांक : {D(date)}"]


# ================================================================================================
# DKT-001 — Occupancy Class-II, full lifecycle, granted. Single combined PDF.
# ================================================================================================
def dkt001() -> Docket:
    S = "सुयोग सहकारी गृहनिर्माण संस्था मर्यादित"
    REG = "PNA/HSG/(TC)/1234/1998-99"
    CTS = "१२३४/अ"
    REF = "महसूल/कक्ष-२/रूपांतर/एसआर-४५/२०२४"
    a_chair, a_sec = aadhaar("56781234901"), aadhaar("45671289034")
    a_m1, a_m2, a_m3 = aadhaar("23456789012"), aadhaar("34567890123"), aadhaar("67890123451")
    mob_chair, mob_sec, mob_m1, mob_m2, mob_m3 = "9822012345", "9890123456", "9765432109", "9922334455", "8806612345"
    pan_sec, email_sec = "BQKPP4821M", "sunita.patil84@gmail.com"

    application = Exhibit("APPLICATION", "filing", [[
        ("right", f"दिनांक : {D('05/02/2024')}"),
        "प्रति,", "मा. जिल्हाधिकारी, पुणे",
        ("title", "अर्ज"),
        "विषय : शासकीय जमीन भोगवटादार वर्ग-२ मधून भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत.",
        f"अर्जदार संस्था : {S}",
        f"नोंदणी क्रमांक : {REG}",
        "महोदय,",
        f"सविनय विनंती अर्ज करण्यात येतो की, आमच्या संस्थेची इमारत मौजे कोथरूड, ता. हवेली, जि. पुणे येथील नगर भूमापन क्रमांक {CTS}, क्षेत्र {D('1250.50')} चौ.मी. या जमिनीवर आहे.",
        "सदर जमीन शासनाने संस्थेस भोगवटादार वर्ग-२ म्हणून दिलेली असून मिळकत पत्रिकेवर धारणाधिकार भोगवटादार वर्ग-२ असा नमूद आहे.",
        f"संस्थेच्या दिनांक {D('12/01/2024')} रोजीच्या विशेष सर्वसाधारण सभेत सदर जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याचा ठराव मंजूर करण्यात आला आहे.",
        "तरी महाराष्ट्र जमीन महसूल (भोगवटादार वर्ग-२ व भाडेपट्ट्याने दिलेल्या जमिनींचे रूपांतरण) नियम, २०१९ नुसार देय नजराणा रक्कम भरण्यास संस्था तयार असून सदर जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्यास परवानगी मिळावी, ही विनंती.",
        "सोबत : १) मिळकत पत्रिका २) संस्था नोंदणी प्रमाणपत्र ३) विशेष सर्वसाधारण सभेचा ठराव ४) सभासदांची यादी ५) प्रतिज्ञापत्र",
    ], [
        ("right", "पान क्र. २/२"),
        "संस्थेच्या वतीने,",
        "अध्यक्ष : श्री. रमेश गणपत देशपांडे",
        f"मोबाईल : {D(mob_chair)}",
        "सचिव : श्रीमती सुनिता विजय पाटील",
        f"मोबाईल : {D(mob_sec)}",
        f"ई-मेल : {email_sec}",
        f"संस्थेचा पत्ता : {S}, नगर भूमापन क्रमांक {CTS}, कोथरूड, पुणे - {D('411038')}",
    ]])
    reg_cert = Exhibit("SOCIETY_REG_CERT", "filing", [[
        ("center", "महाराष्ट्र शासन"), ("center", "सहकार विभाग"),
        ("title", "नोंदणी प्रमाणपत्र"),
        ("center", "(महाराष्ट्र सहकारी संस्था अधिनियम, १९६० चे कलम ९(१) अन्वये)"),
        f"प्रमाणित करण्यात येते की, {S}, कोथरूड, पुणे ही संस्था महाराष्ट्र सहकारी संस्था अधिनियम, १९६० अन्वये नोंदविण्यात आली असून तिचा नोंदणी क्रमांक {REG} असा आहे.",
        "संस्थेचे वर्गीकरण : गृहनिर्माण संस्था (भाडेकरू सहभागीदारी)",
        f"दिनांक : {D('14/07/1998')}",
        ("right", "उपनिबंधक, सहकारी संस्था, हवेली तालुका, पुणे"),
    ]])
    property_card = Exhibit("PROPERTY_CARD", "filing", [[
        ("center", "महाराष्ट्र शासन"),
        ("title", "मिळकत पत्रिका"),
        ("center", "(महाराष्ट्र जमीन महसूल अधिनियम, १९६६)"),
        "गाव / पेठ : कोथरूड      तालुका : हवेली      जिल्हा : पुणे",
        f"नगर भूमापन क्रमांक : {CTS}      शीट क्रमांक : {D('56')}",
        f"क्षेत्र : {D('1250.50')} चौ.मी.",
        "धारणाधिकार : भोगवटादार वर्ग-२",
        f"धारकाचे नाव : {S}",
        f"नोंदी : फेरफार क्र. {D('4567')} दिनांक {D('03/04/2001')} अन्वये संस्थेचे नाव धारक म्हणून दाखल.",
        "शेरा : शासनाच्या पूर्वपरवानगीशिवाय हस्तांतरणास प्रतिबंध.",
        f"प्रत दिल्याचा दिनांक : {D('20/01/2024')}",
        ("right", "नगर भूमापन अधिकारी, पुणे"),
    ]])
    sgm = Exhibit("SGM_RESOLUTION", "filing", [[
        ("center", S),
        ("center", f"नोंदणी क्रमांक : {REG}"),
        ("title", "विशेष सर्वसाधारण सभेचा ठराव"),
        f"संस्थेची विशेष सर्वसाधारण सभा दिनांक {D('12/01/2024')} रोजी सायंकाळी ६ वाजता संस्थेच्या कार्यालयात झाली.",
        f"एकूण सभासद संख्या : {D('48')}",
        f"उपस्थित सभासद संख्या : {D('36')}",
        f"ठराव क्रमांक : {D('3')}",
        "विषय : शासकीय जमीन भोगवटादार वर्ग-२ मधून वर्ग-१ मध्ये रूपांतरित करण्याबाबत.",
        "ठराव : संस्थेची जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्यासाठी मा. जिल्हाधिकारी, पुणे यांच्याकडे अर्ज करण्यास व नियमानुसार देय नजराणा रक्कम भरण्यास अध्यक्ष व सचिव यांना अधिकार देण्यात येत आहेत.",
        "सूचक : श्री. अनिल दत्तात्रय जोशी",
        "अनुमोदक : श्रीमती मेघा सुरेश कुलकर्णी",
        "ठराव सर्वानुमते मंजूर करण्यात आला.",
        "अध्यक्ष : श्री. रमेश गणपत देशपांडे",
        "सचिव : श्रीमती सुनिता विजय पाटील",
    ]])
    members = Exhibit("MEMBER_LIST", "filing", [[
        ("center", S),
        ("title", "सभासदांची यादी"),
        "अ.क्र.   सभासदाचे नाव   सदनिका क्र.   आधार क्रमांक   मोबाईल",
        f"१   श्री. प्रकाश नारायण जोशी   ए-१०१   {D(a_m1)}   {D(mob_m1)}",
        f"२   श्रीमती आशा दिलीप मोरे   ए-१०२   {a_m2}   {mob_m2}",
        f"३   श्री. रमेश गणपत देशपांडे   बी-२०१   {D(a_chair)}   {D(mob_chair)}",
        f"४   श्रीमती सुनिता विजय पाटील   बी-२०४   {D(a_sec)}   {D(mob_sec)}",
        f"५   श्री. अनिल दत्तात्रय जोशी   सी-३०२   {D(a_m3)}   {D(mob_m3)}",
        "प्रमाणित करण्यात येते की वरील यादी संस्थेच्या सभासद नोंदवहीनुसार आहे.",
        ("right", "सचिव"),
    ]])
    affidavit = Exhibit("AFFIDAVIT", "filing", [[
        ("title", "प्रतिज्ञापत्र"),
        f"मी, श्रीमती सुनिता विजय पाटील, वय {D('48')} वर्षे, राहणार सदनिका क्र. बी-२०४, {S}, कोथरूड, पुणे,",
        f"आधार क्रमांक : {D(a_sec)}, पॅन : {pan_sec}",
        "संस्थेची सचिव या नात्याने शपथपूर्वक लिहून देते की,",
        "१. संस्थेच्या जमिनीबाबत कोणत्याही न्यायालयात दावा प्रलंबित नाही.",
        "२. शासनाने निश्चित केलेली नजराणा रक्कम भरण्यास संस्था तयार आहे.",
        "३. अर्जासोबत सादर केलेली सर्व माहिती व कागदपत्रे खरी आहेत.",
        f"ठिकाण : पुणे      दिनांक : {D('05/02/2024')}",
        ("right", "प्रतिज्ञापत्र करणार"),
    ]])
    query = Exhibit("QUERY_LETTER", "internal", [office_header(REF, "11/03/2024") + [
        ("title", "त्रुटी पत्र"),
        "प्रति,", "अध्यक्ष / सचिव,", f"{S},",
        f"नगर भूमापन क्रमांक {CTS}, मौजे कोथरूड, ता. हवेली, जि. पुणे.",
        "विषय : भोगवटादार वर्ग-२ मधील जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत.",
        f"संदर्भ : आपला अर्ज दिनांक {D('05/02/2024')}.",
        "उपरोक्त संदर्भीय अर्जाची छाननी केली असता खालील त्रुटी आढळून आल्या आहेत :",
        "१. संस्थेच्या सभासदांची यादी सचिवांच्या स्वाक्षरीने प्रमाणित करून सादर करावी.",
        "२. इमारतीचे भोगवटा प्रमाणपत्र सादर करावे.",
        "तरी वरील त्रुटींची पूर्तता करावी, अन्यथा प्रकरण नस्तीबंद करण्यात येईल.",
        ("right", "उपजिल्हाधिकारी (महसूल)"), ("right", "करिता जिल्हाधिकारी, पुणे"),
    ]])
    reply = Exhibit("QUERY_REPLY", "query_response", [[
        ("center", S),
        ("center", f"नोंदणी क्रमांक : {REG}"),
        f"दिनांक : {D('02/04/2024')}",
        ("title", "त्रुटी पूर्तता पत्र"),
        "प्रति,", "मा. जिल्हाधिकारी, पुणे",
        "विषय : भोगवटादार वर्ग-२ मधील जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत.",
        f"संदर्भ : आपले पत्र क्र. {REF} दिनांक {D('11/03/2024')}.",
        "महोदय,",
        "उपरोक्त संदर्भीय पत्रातील त्रुटींची पूर्तता करीत आहोत :",
        "१. संस्थेच्या सभासदांची यादी सचिवांच्या स्वाक्षरीने प्रमाणित करून यापूर्वीच सादर केली असून तिची प्रत पुन्हा जोडली आहे.",
        "२. इमारतीचे भोगवटा प्रमाणपत्र सोबत जोडले आहे.",
        ("right", "आपला विश्वासू,"), ("right", "सचिव"),
    ]])
    oc = Exhibit("OCCUPANCY_CERT", "query_response", [[
        ("center", "पुणे महानगरपालिका"), ("center", "बांधकाम विकास विभाग"),
        ("title", "भोगवटा प्रमाणपत्र"),
        "क्रमांक : BP/OC/2001/345",
        f"दिनांक : {D('18/09/2001')}",
        f"प्रमाणित करण्यात येते की, नगर भूमापन क्रमांक {CTS}, मौजे कोथरूड येथील {S} यांच्या इमारतीचे बांधकाम मंजूर नकाशाप्रमाणे पूर्ण झाले असून इमारतीचा निवासी वापर करण्यास हरकत नाही.",
        ("right", "कार्यकारी अभियंता, बांधकाम विकास विभाग"),
    ]])
    field_report = Exhibit("FIELD_REPORT", "internal", [[
        ("center", "तलाठी कार्यालय, सजा कोथरूड, ता. हवेली, जि. पुणे"),
        ("title", "स्थळ पाहणी अहवाल"),
        ("center", "(पंचनामा)"),
        f"दिनांक : {D('15/04/2024')}",
        "प्रति, तहसीलदार, हवेली",
        "विषय : भोगवटादार वर्ग-२ मधील जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत स्थळ पाहणी.",
        f"आदेशानुसार आज दिनांक {D('15/04/2024')} रोजी नगर भूमापन क्रमांक {CTS}, मौजे कोथरूड येथील जागेची स्थळ पाहणी करण्यात आली.",
        f"१. जागेवर {S} यांची तळमजला अधिक चार मजली निवासी इमारत आहे.",
        f"२. प्रत्यक्ष मोजणी क्षेत्र {D('1248.00')} चौ.मी. असून ते मिळकत पत्रिकेवरील क्षेत्राशी सुसंगत आहे.",
        "३. जागेचा वापर निवासी प्रयोजनासाठी होत आहे.",
        "४. जागेवर कोणतेही अतिक्रमण आढळून आले नाही.",
        "पंच : १. श्री. संजय मारुती शिंदे  २. श्री. दिनेश बाबूराव गायकवाड",
        ("right", "तलाठी, सजा कोथरूड"), ("right", "मंडळ अधिकारी, कोथरूड"),
    ]])
    demand = Exhibit("DEMAND_LETTER", "internal", [office_header(REF, "20/05/2024") + [
        ("title", "मागणी पत्र"),
        "प्रति,", "अध्यक्ष / सचिव,", f"{S}, कोथरूड, पुणे",
        "विषय : नजराणा रक्कम शासन जमा करण्याबाबत.",
        "उपरोक्त विषयान्वये आपल्या संस्थेची जमीन भोगवटादार वर्ग-२ मधून वर्ग-१ मध्ये रूपांतरित करण्यासाठी देय नजराणा रक्कम खालीलप्रमाणे निश्चित करण्यात आली आहे :",
        f"वार्षिक बाजारमूल्य दर तक्ता २०२४-२५ नुसार दर : रु. {D('73,000')}/- प्रति चौ.मी.",
        f"क्षेत्र : {D('1250.50')} चौ.मी.",
        "नजराणा दर : ५ टक्के",
        f"एकूण रक्कम : रु. {D('45,64,325')}/-",
        "सदर रक्कम शासन जमा करावी व चलनाची प्रत या कार्यालयास सादर करावी.",
        ("right", "उपजिल्हाधिकारी (महसूल)"), ("right", "करिता जिल्हाधिकारी, पुणे"),
    ]])
    challan = Exhibit("CHALLAN", "final", [[
        ("center", "GOVERNMENT OF MAHARASHTRA"),
        ("center", "Government Receipt Accounting System (GRAS)"),
        ("title", "e-Challan"),
        "GRN : MH004512345678202425",
        "Department : Revenue and Forest Department",
        "Payment Type : Nazrana / Premium for conversion of Occupancy Class-II land",
        "Office Name : Collector Office, Pune",
        "Head of Account : 0029-00-800-01 Other Receipts (Land Revenue)",
        "Name of Party : Suyog Co-operative Housing Society Ltd",
        "Address : CTS No. 1234/A, Kothrud, Pune 411038",
        f"Mobile No. : {mob_sec}",
        "Total Amount : Rs. 45,64,325.00",
        "Amount in words : Rupees Forty Five Lakh Sixty Four Thousand Three Hundred Twenty Five Only",
        "Payment Date : 10/06/2024",
        "Bank Name : State Bank of India",
        "Bank CIN : 0002345202406100012345",
    ]])
    order = Exhibit("COLLECTOR_ORDER", "final", [office_header(REF, "28/06/2024") + [
        ("title", "आदेश"),
        "वाचले :",
        f"१) {S} यांचा अर्ज दिनांक {D('05/02/2024')}.",
        f"२) या कार्यालयाचे त्रुटी पत्र दिनांक {D('11/03/2024')}.",
        f"३) तलाठी, सजा कोथरूड यांचा स्थळ पाहणी अहवाल दिनांक {D('15/04/2024')}.",
        f"४) या कार्यालयाचे मागणी पत्र दिनांक {D('20/05/2024')}.",
        f"५) संस्थेने शासन जमा केलेले चलन (GRN MH004512345678202425) दिनांक {D('10/06/2024')}.",
        f"ज्याअर्थी, {S} यांनी मौजे कोथरूड, ता. हवेली, जि. पुणे येथील नगर भूमापन क्रमांक {CTS}, क्षेत्र {D('1250.50')} चौ.मी. ही भोगवटादार वर्ग-२ म्हणून धारण केलेली जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत अर्ज केला आहे;",
        f"आणि ज्याअर्थी, संस्थेने देय नजराणा रक्कम रु. {D('45,64,325')}/- दिनांक {D('10/06/2024')} रोजी चलनाद्वारे शासन जमा केली आहे;",
    ], [
        ("right", "पान क्र. २/२"),
        "त्याअर्थी, मी, जिल्हाधिकारी, पुणे, महाराष्ट्र जमीन महसूल (भोगवटादार वर्ग-२ व भाडेपट्ट्याने दिलेल्या जमिनींचे रूपांतरण) नियम, २०१९ मधील तरतुदींनुसार मला प्राप्त झालेल्या अधिकारानुसार याद्वारे सदर जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्यास परवानगी देण्यात येत आहे.",
        "अटी व शर्ती :",
        "१. मिळकत पत्रिकेवरील धारणाधिकाराची नोंद भोगवटादार वर्ग-१ अशी करण्यात यावी.",
        "२. सदर जमिनीचा वापर निवासी प्रयोजनासाठीच करण्यात यावा.",
        ("right", "जिल्हाधिकारी, पुणे"),
        "प्रत : नगर भूमापन अधिकारी, पुणे यांना मिळकत पत्रिकेवर नोंद घेण्यासाठी.",
    ]])
    return Docket(
        case_ref="DKT-001",
        files=[("docket.pdf", [application, reg_cert, property_card, sgm, members, affidavit, query, reply, oc,
                               field_report, demand, challan, order])],
        facts={
            "society.name": S, "society.registration_no": REG, "land.cts_no": "1234/अ", "land.village": "कोथरूड",
            "land.taluka": "हवेली", "land.district": "पुणे", "land.area_sqm": "1250.50", "land.tenure": "occupancy_class_2",
            "application.date": "2024-02-05", "property_card.issue_date": "2024-01-20", "sgm.meeting_date": "2024-01-12",
            "sgm.resolution_no": "3", "sgm.members_present": 36, "sgm.members_total": 48,
            "demand.letter_date": "2024-05-20", "demand.amount": "4564325.00",
            "order.order_date": "2024-06-28", "order.decision": "granted", "order.premium_amount": "4564325.00",
            "payments.0.amount": "4564325.00", "payments.0.grn": "MH004512345678202425", "payments.0.payment_date": "2024-06-10",
            "inspections.0.area_sqm": "1248.00", "inspections.0.inspection_date": "2024-04-15", "inspections.0.encroachment": False,
            "queries.0.letter_date": "2024-03-11", "queries.0.items.count": 2,
        },
        validations={"CTS_MATCH": "pass", "AREA_TOLERANCE": "pass", "HOLDER_IS_SOCIETY": "pass",
                     "SGM_DATE_ORDER": "pass", "PROPERTY_CARD_RECENCY": "pass"},
        requirements={"APPLICATION_FORM": "present", "MEMBER_LIST": "present", "OCCUPANCY_CERTIFICATE": "present",
                      "LEASE_OR_GRANT_DOCUMENT": "missing", "PREMIUM_PAYMENT": "present", "NO_DUES_LEASE_RENT": "not_applicable",
                      "CTSO_MEASUREMENT": "not_applicable"},
        events=["APPLICATION_FILED", "QUERY_ISSUED", "QUERY_REPLIED", "FIELD_INSPECTION_DONE", "DEMAND_ISSUED", "PREMIUM_PAID", "ORDER_ISSUED"],
        pii=[a_chair, a_sec, a_m1, a_m2, a_m3, mob_chair, mob_sec, mob_m1, mob_m2, mob_m3, pan_sec, email_sec,
             "रमेश गणपत देशपांडे", "सुनिता विजय पाटील", "प्रकाश नारायण जोशी", "आशा दिलीप मोरे", "अनिल दत्तात्रय जोशी",
             "मेघा सुरेश कुलकर्णी", "संजय मारुती शिंदे", "दिनेश बाबूराव गायकवाड"],
        notes="Clean Class-II case, one query cycle, granted.",
    )


# ================================================================================================
# DKT-002 — Govt leasehold, pending, several defects. Three separate PDFs.
# ================================================================================================
def dkt002() -> Docket:
    S = "गणेश कृपा सहकारी गृहनिर्माण संस्था मर्यादित"
    REG = "PNA/HSG/(TC)/2211/1971-72"
    CTS = "५६७"
    REF = "महसूल/कक्ष-२/रूपांतर/एसआर-१८/२०२५"
    acct, ifsc = "032104567890", "SBIN0001234"
    email, mob = "ganeshkrupa.chs@gmail.com", "+91 98220 45678"
    a1, a2 = aadhaar("78901234560"), aadhaar("89012345671")
    m1, m2 = "9420011223", "7030044556"

    application = Exhibit("APPLICATION", "filing", [[
        ("right", f"दिनांक : {D('15/01/2025')}"),
        "प्रति,", "मा. जिल्हाधिकारी, पुणे",
        ("title", "अर्ज"),
        "विषय : भाडेपट्ट्याने दिलेली शासकीय जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत.",
        f"अर्जदार संस्था : {S}",
        f"नोंदणी क्रमांक : {REG}",
        "महोदय,",
        f"सविनय विनंती अर्ज करण्यात येतो की, मौजे एरंडवणे, ता. हवेली, जि. पुणे येथील नगर भूमापन क्रमांक {CTS}, क्षेत्र {D('800.00')} चौ.मी. ही जमीन शासनाने संस्थेस भाडेपट्ट्याने दिलेली आहे.",
        "मिळकत पत्रिकेवर सत्ता प्रकार शासकीय भाडेपट्टा असा नमूद आहे.",
        "सदर जमीन नियमानुसार भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्यास परवानगी मिळावी, ही विनंती.",
        f"परताव्यासाठी संस्थेचे बँक खाते क्रमांक {D(acct)} (IFSC : {ifsc}) आहे.",
        f"संपर्क : सचिव, ई-मेल : {email}, मोबाईल : {mob}",
        "अध्यक्ष : श्री. मोहन शंकर कुलकर्णी",
        "सचिव : श्री. Rajesh K. Nair",
    ]])
    lease = Exhibit("LEASE_SANAD", "filing", [[
        ("title", "भाडेपट्टा करारनामा"),
        ("center", "(शासकीय जमीन भाडेपट्ट्याने देण्याबाबत)"),
        f"हा भाडेपट्टा करारनामा आज दिनांक {D('10/08/1972')} रोजी पुणे येथे महाराष्ट्र शासनाच्या वतीने जिल्हाधिकारी, पुणे (यापुढे भाडेपट्टा देणार) आणि",
        f"{S}, एरंडवणे, पुणे (यापुढे भाडेपट्टाधारक) यांच्यामध्ये करण्यात येत आहे.",
        f"१. मौजे एरंडवणे, ता. हवेली, जि. पुणे येथील नगर भूमापन क्रमांक {CTS}, क्षेत्र {D('800.00')} चौ.मी. ही शासकीय जमीन निवासी प्रयोजनासाठी भाडेपट्ट्याने देण्यात येत आहे.",
        f"२. भाडेपट्ट्याचा कालावधी {D('99')} वर्षांचा राहील.",
        f"३. वार्षिक भाडे रु. {D('1,200')}/- (रुपये एक हजार दोनशे फक्त) दरवर्षी १ एप्रिलपूर्वी भरावे लागेल.",
    ], [
        ("right", "पान क्र. २/२"),
        "४. भाडेपट्टाधारकाने जमीन अथवा इमारत शासनाच्या पूर्वपरवानगीशिवाय हस्तांतरित करू नये.",
        "५. अटींचा भंग झाल्यास भाडेपट्टा रद्द करण्याचा अधिकार शासनास राहील.",
        "भाडेपट्टा देणार : जिल्हाधिकारी, पुणे यांच्या वतीने",
        "भाडेपट्टाधारकाच्या वतीने",
        "अध्यक्ष : श्री. विठ्ठल रामचंद्र जोशी",
        "साक्षीदार : श्री. गोविंद हरी आपटे",
    ]])
    property_card = Exhibit("PROPERTY_CARD", "filing", [[
        ("center", "महाराष्ट्र शासन"),
        ("title", "मिळकत पत्रिका"),
        "गाव / पेठ : एरंडवणे      तालुका : हवेली      जिल्हा : पुणे",
        f"नगर भूमापन क्रमांक : {CTS}      शीट क्रमांक : {D('12')}",
        f"क्षेत्र : {D('800.00')} चौ.मी.",
        "सत्ता प्रकार : शासकीय भाडेपट्टा",
        "धारकाचे नाव : श्री. विठ्ठल रामचंद्र जोशी (मूळ भाडेपट्टाधारक)",
        "नोंदी : संस्थेचे नाव धारक म्हणून दाखल करणे प्रलंबित.",
        f"प्रत दिल्याचा दिनांक : {D('01/06/2023')}",
        ("right", "नगर भूमापन अधिकारी, पुणे"),
    ]])
    sgm = Exhibit("SGM_RESOLUTION", "filing", [[
        ("center", S),
        ("title", "विशेष सर्वसाधारण सभेचा ठराव"),
        f"संस्थेची विशेष सर्वसाधारण सभा दिनांक {D('20/01/2025')} रोजी संस्थेच्या कार्यालयात झाली.",
        f"एकूण सभासद संख्या : {D('24')}",
        f"उपस्थित सभासद संख्या : {D('15')}",
        f"ठराव क्रमांक : {D('2')}",
        "ठराव : भाडेपट्ट्याची जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्यासाठी अर्ज करण्यास मंजुरी देण्यात येत आहे.",
        "सूचक : Shri Rajesh K. Nair",
        "अनुमोदक : श्रीमती कविता अरुण भिडे",
        "ठराव सर्वानुमते मंजूर.",
    ]])
    members = Exhibit("MEMBER_LIST", "filing", [[
        ("center", S),
        ("title", "सभासदांची यादी"),
        "अ.क्र.   सभासदाचे नाव   सदनिका क्र.   आधार क्रमांक   मोबाईल",
        f"१   श्री. मोहन शंकर कुलकर्णी   १   {D(a1)}   {D(m1)}",
        f"२   Shri Rajesh K. Nair   २   {a2}   {m2}",
        "३   श्रीमती कविता अरुण भिडे   ३",
        "प्रमाणित करण्यात येते की वरील यादी बरोबर आहे.",
    ]])
    field_report = Exhibit("FIELD_REPORT", "internal", [[
        ("center", "तलाठी कार्यालय, सजा एरंडवणे, ता. हवेली"),
        ("title", "स्थळ पाहणी अहवाल"),
        f"दिनांक : {D('05/02/2025')}",
        f"आज दिनांक {D('05/02/2025')} रोजी नगर भूमापन क्रमांक {CTS}, मौजे एरंडवणे येथील जागेची स्थळ पाहणी केली.",
        f"१. प्रत्यक्ष मोजणी क्षेत्र {D('842.00')} चौ.मी. आढळले.",
        f"२. जागेच्या दक्षिण बाजूस सुमारे {D('40')} चौ.मी. क्षेत्रावर अतिक्रमण आढळून आले.",
        "३. जागेवर संस्थेची निवासी इमारत आहे.",
        ("right", "तलाठी, सजा एरंडवणे"),
    ]])
    ctso = Exhibit("CTSO_REPORT", "internal", [[
        ("center", "कार्यालय, नगर भूमापन अधिकारी क्र. २, पुणे"),
        ("title", "मोजणी अहवाल"),
        "जावक क्रमांक : नभूअ/मोजणी/७८९/२०२५",
        f"दिनांक : {D('10/03/2025')}",
        f"मौजे एरंडवणे येथील नगर भूमापन क्रमांक {CTS} ची दिनांक {D('05/03/2025')} रोजी प्रत्यक्ष मोजणी करण्यात आली.",
        f"मिळकत पत्रिकेवरील क्षेत्र : {D('800.00')} चौ.मी.",
        f"प्रत्यक्ष मोजणी क्षेत्र : {D('841.50')} चौ.मी.",
        f"फरक : {D('41.50')} चौ.मी. (जादा)",
        "मोजणीअंती जागेवर संस्थेची इमारत असून दक्षिण सीमेवर अतिक्रमण आढळून आले.",
        ("right", "नगर भूमापन अधिकारी क्र. २, पुणे"),
    ]])
    query = Exhibit("QUERY_LETTER", "internal", [office_header(REF, "20/03/2025") + [
        ("title", "त्रुटी पत्र"),
        "प्रति,", "अध्यक्ष / सचिव,", f"{S}, एरंडवणे, पुणे",
        "विषय : भाडेपट्ट्याने दिलेली शासकीय जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत.",
        "आपल्या अर्जाची छाननी केली असता खालील त्रुटी आढळून आल्या आहेत :",
        "१. अद्ययावत मिळकत पत्रिका सादर करावी; सध्याच्या मिळकत पत्रिकेवर संस्थेचे नाव धारक म्हणून नमूद नाही.",
        "२. भाडेपट्टा थकबाकी नसल्याचा दाखला सादर करावा.",
        "३. सर्वसाधारण सभा अर्जाच्या दिनांकानंतर घेतल्याने सुधारित ठराव सादर करावा.",
        "४. जागेवरील अतिक्रमणाबाबत खुलासा सादर करावा.",
        "तरी वरील त्रुटींची पूर्तता करावी.",
        ("right", "करिता जिल्हाधिकारी, पुणे"),
    ]])
    return Docket(
        case_ref="DKT-002",
        files=[("01_arj.pdf", [application, lease]),
               ("02_kagadpatre.pdf", [property_card, sgm, members]),
               ("03_karyalay.pdf", [field_report, ctso, query])],
        facts={
            "society.name": S, "land.cts_no": "567", "land.village": "एरंडवणे", "land.area_sqm": "800.00",
            "land.tenure": "govt_lease", "application.date": "2025-01-15", "lease.lease_date": "1972-08-10",
            "lease.lease_period_years": 99, "lease.annual_rent": "1200.00", "sgm.meeting_date": "2025-01-20",
            "property_card.issue_date": "2023-06-01", "inspections.0.area_sqm": "842.00", "inspections.0.encroachment": True,
            "inspections.1.area_sqm": "841.50", "inspections.1.inspection_date": "2025-03-05", "queries.0.items.count": 4,
        },
        validations={"CTS_MATCH": "pass", "AREA_TOLERANCE": "fail", "HOLDER_IS_SOCIETY": "fail",
                     "SGM_DATE_ORDER": "fail", "PROPERTY_CARD_RECENCY": "fail"},
        requirements={"PROPERTY_CARD_RECENT": "invalid", "SGM_RESOLUTION": "invalid", "NO_DUES_LEASE_RENT": "queried",
                      "LEASE_OR_GRANT_DOCUMENT": "present", "CTSO_MEASUREMENT": "present", "PREMIUM_PAYMENT": "missing"},
        events=["APPLICATION_FILED", "FIELD_INSPECTION_DONE", "CTSO_MEASUREMENT_DONE", "QUERY_ISSUED"],
        pii=[acct, email, "9822045678", a1, a2, m1, m2, "मोहन शंकर कुलकर्णी", "Rajesh K. Nair", "विठ्ठल रामचंद्र जोशी",
             "गोविंद हरी आपटे", "कविता अरुण भिडे"],
        notes="Govt lease, pending: stale property card held by an individual, SGM after application, area mismatch.",
    )


# ================================================================================================
# DKT-003 — Class-II, payment stage, new property card in query response, back-to-back challans.
# ================================================================================================
def dkt003() -> Docket:
    S = "साई दर्शन सहकारी गृहनिर्माण संस्था मर्यादित"
    REG = "PNA/HSG/(TC)/5678/2003-04"
    CTS = "२१०/ब/३"
    REF = "महसूल/कक्ष-२/रूपांतर/एसआर-९२/२०२४"
    a_aff, v_aff = aadhaar("90123456782"), vid("912345678901234")
    pan, mob_aff, email_aff = "AKLPB7788Q", "+91-98901-23456", "kiran.bhosale@example.in"
    mob_app, email_app = "94221 77889", "sd.chs.baner@gmail.com"

    def card(issue: str) -> list[Line]:
        return [
            ("center", "महाराष्ट्र शासन"),
            ("title", "मिळकत पत्रिका"),
            "गाव / पेठ : बाणेर      तालुका : हवेली      जिल्हा : पुणे",
            f"नगर भूमापन क्रमांक : {CTS}",
            f"क्षेत्र : {D('2100.00')} चौ.मी.",
            "धारणाधिकार : भोगवटादार वर्ग-२",
            f"धारकाचे नाव : {S}",
            f"प्रत दिल्याचा दिनांक : {D(issue)}",
            ("right", "नगर भूमापन अधिकारी, पुणे"),
        ]

    def challan(grn: str, head: str, purpose: str, amount: str, words: str) -> Exhibit:
        return Exhibit("CHALLAN", "final", [[
            ("center", "GOVERNMENT OF MAHARASHTRA"),
            ("center", "Government Receipt Accounting System (GRAS)"),
            ("title", "e-Challan"),
            f"GRN : {grn}",
            "Department : Revenue and Forest Department",
            f"Payment Type : {purpose}",
            "Office Name : Collector Office, Pune",
            f"Head of Account : {head}",
            "Name of Party : Sai Darshan Co-operative Housing Society Ltd",
            f"Total Amount : Rs. {amount}",
            f"Amount in words : {words}",
            "Payment Date : 25/11/2024",
            "Bank Name : Bank of Maharashtra",
        ]])

    application = Exhibit("APPLICATION", "filing", [[
        ("right", f"दिनांक : {D('02/09/2024')}"),
        "प्रति,", "मा. जिल्हाधिकारी, पुणे",
        ("title", "अर्ज"),
        "विषय : भोगवटादार वर्ग-२ मधील जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत.",
        f"अर्जदार संस्था : {S}",
        f"नोंदणी क्रमांक : {REG}",
        f"सविनय विनंती अर्ज करण्यात येतो की, मौजे बाणेर, ता. हवेली, जि. पुणे येथील नगर भूमापन क्रमांक {CTS}, क्षेत्र {D('2100.00')} चौ.मी. ही जमीन संस्थेस भोगवटादार वर्ग-२ म्हणून मिळाली आहे.",
        "मिळकत पत्रिकेवर धारणाधिकार भोगवटादार वर्ग-२ असा आहे.",
        "सदर जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्यास परवानगी मिळावी, ही विनंती.",
        f"संपर्क : {D(mob_app)} / {email_app}",
        ("right", "अध्यक्ष / सचिव"),
    ]])
    sgm = Exhibit("SGM_RESOLUTION", "filing", [[
        ("center", S),
        ("title", "विशेष सर्वसाधारण सभेचा ठराव"),
        f"संस्थेची विशेष सर्वसाधारण सभा दिनांक {D('15/08/2024')} रोजी झाली.",
        f"एकूण सभासद संख्या : {D('60')}",
        f"उपस्थित सभासद संख्या : {D('41')}",
        f"ठराव क्रमांक : {D('7')}",
        "ठराव : संस्थेची जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्यासाठी अर्ज करण्यास मंजुरी देण्यात येत आहे.",
        "ठराव बहुमताने मंजूर.",
    ]])
    affidavit = Exhibit("AFFIDAVIT", "filing", [[
        ("title", "प्रतिज्ञापत्र"),
        f"मी, श्री. किरण अशोक भोसले, वय {D('52')} वर्षे, अध्यक्ष, {S}, बाणेर, पुणे,",
        f"आधार क्रमांक : {D(a_aff)}",
        f"आधार व्हीआयडी : {v_aff}",
        f"पॅन : {pan}",
        f"मोबाईल : {mob_aff}",
        f"ई-मेल : {email_aff}",
        "शपथपूर्वक लिहून देतो की,",
        "१. संस्थेची जमीन कोणत्याही बँकेकडे गहाण नाही.",
        "२. जमिनीबाबत कोणताही न्यायालयीन वाद प्रलंबित नाही.",
    ], [
        ("right", "पान क्र. २/२"),
        "३. रूपांतरणानंतर देय होणारे सर्व कर संस्था भरेल.",
        "४. वरील सर्व माहिती खरी असून ती खोटी आढळल्यास कायदेशीर कारवाईस पात्र राहीन.",
        f"ठिकाण : पुणे      दिनांक : {D('30/08/2024')}",
        ("right", "प्रतिज्ञापत्र करणार"),
        "समक्ष : नोटरी, पुणे",
    ]])
    query = Exhibit("QUERY_LETTER", "internal", [office_header(REF, "20/09/2024") + [
        ("title", "त्रुटी पत्र"),
        "प्रति,", "अध्यक्ष / सचिव,", f"{S}, बाणेर, पुणे",
        "विषय : भोगवटादार वर्ग-२ मधील जमीन भोगवटादार वर्ग-१ मध्ये रूपांतरित करण्याबाबत.",
        f"संदर्भ : आपला अर्ज दिनांक {D('02/09/2024')}.",
        "आपल्या अर्जाची छाननी केली असता खालील त्रुटी आढळून आल्या आहेत :",
        "१. अद्ययावत मिळकत पत्रिका सादर करावी; सादर केलेली मिळकत पत्रिका तीन महिन्यांपेक्षा जुनी आहे.",
        "२. संस्थेच्या सभासदांची यादी सादर करावी.",
    ], [
        ("right", "पान क्र. २/२"),
        "३. इमारतीचे भोगवटा प्रमाणपत्र सादर करावे.",
        "तरी वरील त्रुटींची पूर्तता पंधरा दिवसांत करावी.",
        ("right", "करिता जिल्हाधिकारी, पुणे"),
    ]])
    reply = Exhibit("QUERY_REPLY", "query_response", [[
        ("center", S),
        f"दिनांक : {D('25/10/2024')}",
        ("title", "त्रुटी पूर्तता पत्र"),
        "प्रति,", "मा. जिल्हाधिकारी, पुणे",
        f"संदर्भ : आपले पत्र क्र. {REF} दिनांक {D('20/09/2024')}.",
        "उपरोक्त पत्रातील त्रुटींची पूर्तता करीत आहोत :",
        "१. अद्ययावत मिळकत पत्रिका सोबत जोडली आहे.",
        "२. भोगवटा प्रमाणपत्र महानगरपालिकेकडून प्राप्त होताच सादर करण्यात येईल.",
        ("right", "सचिव"),
    ]])
    demand = Exhibit("DEMAND_LETTER", "internal", [office_header(REF, "15/11/2024") + [
        ("title", "मागणी पत्र"),
        "प्रति,", "अध्यक्ष / सचिव,", f"{S}, बाणेर, पुणे",
        "विषय : नजराणा रक्कम शासन जमा करण्याबाबत.",
        f"वार्षिक बाजारमूल्य दर तक्ता २०२४-२५ नुसार दर : रु. {D('58,000')}/- प्रति चौ.मी.",
        f"क्षेत्र : {D('2100.00')} चौ.मी.",
        "नजराणा दर : ५ टक्के",
        f"एकूण रक्कम : रु. {D('60,90,000')}/-",
        "सदर रक्कम शासन जमा करावी.",
        ("right", "करिता जिल्हाधिकारी, पुणे"),
    ]])
    ch1 = challan("MH004598765432202425", "0029-00-800-01 Other Receipts (Land Revenue)",
                  "Nazrana / Premium for conversion of Occupancy Class-II land", "60,90,000.00",
                  "Rupees Sixty Lakh Ninety Thousand Only")
    ch2 = challan("MH004598765433202425", "0029-00-101-01 Land Revenue Measurement Fees",
                  "Measurement fees", "12,500.00", "Rupees Twelve Thousand Five Hundred Only")
    return Docket(
        case_ref="DKT-003",
        files=[("docket.pdf", [application, Exhibit("PROPERTY_CARD", "filing", [card("10/01/2024")]), sgm, affidavit,
                               query, reply, Exhibit("PROPERTY_CARD", "query_response", [card("20/10/2024")]),
                               demand, ch1, ch2])],
        facts={
            "society.name": S, "society.registration_no": REG, "land.cts_no": "210/ब/3", "land.village": "बाणेर",
            "land.area_sqm": "2100.00", "land.tenure": "occupancy_class_2", "application.date": "2024-09-02",
            "property_card.issue_date": "2024-10-20", "sgm.meeting_date": "2024-08-15", "sgm.members_total": 60,
            "demand.amount": "6090000.00", "payments.0.amount": "6090000.00", "payments.1.amount": "12500.00",
            "payments.1.grn": "MH004598765433202425", "queries.0.items.count": 3,
        },
        validations={"CTS_MATCH": "pass", "AREA_TOLERANCE": "pass", "HOLDER_IS_SOCIETY": "pass",
                     "SGM_DATE_ORDER": "pass", "PROPERTY_CARD_RECENCY": "pass"},
        requirements={"PROPERTY_CARD_RECENT": "present", "OCCUPANCY_CERTIFICATE": "queried", "MEMBER_LIST": "queried",
                      "PREMIUM_PAYMENT": "present", "UNDERTAKING_AFFIDAVIT": "present"},
        events=["APPLICATION_FILED", "QUERY_ISSUED", "QUERY_REPLIED", "DEMAND_ISSUED", "PREMIUM_PAID", "PREMIUM_PAID"],
        pii=[a_aff, v_aff, pan, "9890123456", email_aff, "9422177889", email_app, "क\u093fरण अश\u094bक भ\u094bसल\u0947"],
        notes="Payment stage; replacement property card in query response; two back-to-back challans.",
    )


# ------------------------------------------------------------------------------------------------
def build(docket: Docket, r: Renderer) -> tuple[dict, list[dict]]:
    folder = DOCKETS / docket.case_ref
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.pdf"):
        old.unlink()
    seq, exhibits, bench = 0, [], []
    for filename, exs in docket.files:
        pdf = pymupdf.open()
        for ex in exs:
            first = seq + 1
            for page_lines in ex.pages:
                lines = r.page(pdf, page_lines)
                seq += 1
                bench.append({"id": f"{docket.case_ref}-p{seq:02d}", "pdf": f"samples/dockets/{docket.case_ref}/{filename}",
                              "page_index": pdf.page_count - 1, "exhibit_type": ex.type, "transcription": "\n".join(lines)})
            exhibits.append({"type": ex.type, "stage": ex.stage, "pages": [first, seq], "document": filename})
        pdf.subset_fonts()
        pdf.save(folder / filename, garbage=4, deflate=True)
    expected = {
        "case_ref": docket.case_ref, "notes": docket.notes, "page_count": seq, "exhibits": exhibits,
        "facts": docket.facts, "validations": docket.validations, "requirements": docket.requirements,
        "events": docket.events, "pii_values": docket.pii,
    }
    EXPECTED.mkdir(parents=True, exist_ok=True)
    (EXPECTED / f"{docket.case_ref}.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return expected, bench


def main() -> None:
    r = Renderer(font_path())
    bench_all = []
    for make in (dkt001, dkt002, dkt003):
        d = make()
        exp, bench = build(d, r)
        bench_all += bench
        print(f"{d.case_ref}: {exp['page_count']} pages, {len(exp['exhibits'])} exhibits")
    BENCH.mkdir(parents=True, exist_ok=True)
    (BENCH / "manifest.json").write_text(json.dumps(
        {"description": "Synthetic ground truth generated by eval/samples/generate_samples.py. Add hand-transcribed "
                        "real pages under eval/ocr_bench/real/ (see README).", "pages": bench_all},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
