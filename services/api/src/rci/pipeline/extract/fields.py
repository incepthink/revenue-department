"""Per-exhibit-type field specifications: the single source of truth for the rules extractor, the LLM
extraction schemas (packages/schemas/extraction/*.json) and the card.

Phase 1 covers the 8 highest-value types (+ the letters needed for the event timeline).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from rci.pipeline.extract.common import (
    AMOUNT_RE,
    AREA_RE,
    CTS_RE,
    DATE_RE,
    SOCIETY_EN_RE,
    SOCIETY_MR_RE,
    Evidence,
    PageText,
    area_from_match,
    clean_society,
    date_from_match,
    find_all_dates,
    find_first,
    find_labeled,
    make_evidence,
    normalise_cts,
    parse_date,
    money,
    parse_decimal,
)

Kind = str  # date | amount | area | cts | text | int | bool | enum | list


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: Kind
    description: str
    rule: Callable[[list[PageText]], Any] | None = None
    enum: tuple[str, ...] = ()


# ---- value parsers shared by rules and LLM output ---------------------------------------------
def parse_value(kind: Kind, raw: str, enum: tuple[str, ...] = ()) -> Any:
    from rci.pipeline.normalise import ascii_digits

    s = ascii_digits(raw or "").strip()
    if not s:
        return None
    if kind == "date":
        return parse_date(s)
    if kind == "amount":
        m = AMOUNT_RE.search(s)
        return money(parse_decimal(m.group(1)) if m else parse_decimal(re.sub(r"[^\d.,]", "", s)))
    if kind == "area":
        m = AREA_RE.search(s)
        return area_from_match(m) if m else money(parse_decimal(re.sub(r"[^\d.]", "", s)))
    if kind == "cts":
        return normalise_cts(s)
    if kind == "int":
        m = re.search(r"\d+", s)
        return int(m.group()) if m else None
    if kind == "bool":
        return s.lower() in {"true", "yes", "होय", "1"}
    if kind == "enum":
        return s if s in enum else None
    return re.sub(r"\s+", " ", raw).strip()


# ---- generic rule builders ----------------------------------------------------------------------
DATE_VAL = DATE_RE


def _date(m):
    return date_from_match(m)


def labeled_date(label: str, window: int = 60, last: bool = False):
    return lambda pages: find_labeled(pages, label, DATE_VAL, _date, window=window, last=last, group=0)


def first_date(pages):
    ds = find_all_dates(pages[:1])
    return ds[0] if ds else None


def labeled_amount(label: str, window: int = 80):
    val = re.compile(AMOUNT_RE.pattern + "|" + r"(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+\.\d{2})", re.I)

    def parse(m):
        return money(parse_decimal(m.group(1) or m.group(2)))

    def rule(pages):
        ev = find_labeled(pages, label, val, parse, window=window)
        if ev:
            # keep evidence on the number itself
            ev.raw = ev.raw.replace("/-", "").strip()
        return ev

    return rule


def labeled_area(label: str, window: int = 60):
    return lambda pages: find_labeled(pages, label, AREA_RE, area_from_match, window=window, group=0)


def cts(pages):
    return find_first(pages, CTS_RE, lambda m: normalise_cts(m.group(1)), confidence=0.85)


def labeled_text(label: str, value: str = r"([^\n,;]{2,80})", window: int = 100):
    return lambda pages: find_labeled(pages, label, value, lambda m: re.sub(r"\s+", " ", m.group(1)).strip(" .:-"), window=window)


def labeled_int(label: str, window: int = 30):
    return lambda pages: find_labeled(pages, label, r"(\d{1,5})", lambda m: int(m.group(1)), window=window)


def society(pages):
    for rx in (SOCIETY_MR_RE, SOCIETY_EN_RE):
        for p in pages:
            for m in rx.finditer(p.ascii):
                name = clean_society(p.text[m.start(1) : m.end(1)])
                if len(name.split()) >= 3:
                    off = m.end(1) - len(name)
                    return make_evidence(p, off, m.end(1), name, 0.8)
    return None


def registration_no(pages):
    return find_labeled(
        pages, r"नोंदणी\s*(?:क्र(?:मांक)?\.?|नं\.?)|Reg(?:istration|n)?\.?\s*No\.?",
        r"([A-Z]{2,6}[/.\-][A-Z0-9()./\-]{4,40}[A-Z0-9)])", lambda m: m.group(1), window=40,
    )


def word_after(label: str):
    return lambda pages: find_labeled(pages, label, r"([\u0900-\u097f]{2,}(?:\s+[\u0900-\u097f]{2,})?)",
                                      lambda m: _trim_place(m.group(1)), window=15)


PLACE_STOP = {"ता", "तालुका", "जि", "जिल्हा", "येथील", "येथे", "मधील", "नगर", "सि", "न", "सी", "गाव"}


def _trim_place(s: str) -> str:
    toks = [t for t in s.split()]
    out = []
    for t in toks:
        if t.rstrip(".") in PLACE_STOP:
            break
        out.append(t)
    return " ".join(out)


TENURE_PATTERNS = [
    (re.compile(r"भोगवटादार\s*वर्ग\s*-?\s*(?:2|२|II)|occupancy\s*class\s*-?\s*(?:2|II)", re.I), "occupancy_class_2"),
    (re.compile(r"शासकीय\s*भाडेपट्ट|भाडेपट्टा|govt\.?\s*lease|government\s*lease|leasehold", re.I), "govt_lease"),
    (re.compile(r"भोगवटादार\s*वर्ग\s*-?\s*(?:1|१|I)\b|occupancy\s*class\s*-?\s*(?:1|I)\b", re.I), "occupancy_class_1"),
]


def tenure(pages):
    lab = re.compile(r"धारणाधिकार|सत्ता\s*प्रकार|Tenure", re.I)
    for p in pages:
        for lm in lab.finditer(p.ascii):
            line_end = p.ascii.find("\n", lm.end())
            seg = p.ascii[lm.end() : line_end if line_end != -1 else None]
            for rx, val in TENURE_PATTERNS:
                m = rx.search(seg)
                if m:
                    return make_evidence(p, lm.end() + m.start(), lm.end() + m.end(), val, 0.85)
    return None


def lease_years(pages):
    return find_first(pages, re.compile(r"(\d{1,3})\s*(?:वर्षां?च्या|वर्षां?चा|वर्षे|वर्षांसाठी|years)", re.I), lambda m: int(m.group(1)))


def query_items(pages) -> list[Evidence]:
    """Numbered items of a query letter, from the line after 'खालील त्रुटी…' (or the first numbered line)."""
    items: list[Evidence] = []
    started = False
    item_re = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+(.{6,})$")
    for p in pages:
        offset = 0
        for line in p.ascii.split("\n"):
            if re.search(r"खालील\s+त्रुटी|following\s+(?:deficiencies|queries)", line, re.I):
                started = True
            m = item_re.match(line)
            if m and (started or not items):
                s, e = offset + m.start(2), offset + m.end(2)
                items.append(make_evidence(p, s, e, re.sub(r"\s+", " ", p.text[s:e]).strip(), 0.8))
            elif items and re.match(r"^\s*(?:सबब|तरी|आपला|आपली|प्रत|सही)", line):
                started = False
            offset += len(line) + 1
    return items


def decision(pages):
    rules = [
        (re.compile(r"(?:रूपांतरित|रुपांतरित)\s+करण्यास\s+(?:परवानगी|मान्यता)\s+देण्यात\s+येत\s+आहे|परवानगी\s+देण्यात\s+येत\s+आहे|मान्यता\s+देण्यात\s+येत\s+आहे|is\s+hereby\s+(?:granted|permitted)", re.I), "granted"),
        (re.compile(r"नामंजूर\s+करण्यात\s+येत|फेटाळण्यात\s+येत|is\s+hereby\s+rejected", re.I), "rejected"),
    ]
    for p in pages:
        for rx, val in rules:
            m = rx.search(p.ascii)
            if m:
                return make_evidence(p, m.start(), m.end(), val, 0.85)
    return None


def encroachment(pages):
    for p in pages:
        m = re.search(r"अतिक्रमण\s+(?:आढळून\s+आले\s+नाही|नाही|आढळले\s+नाही)|no\s+encroachment", p.ascii, re.I)
        if m:
            return make_evidence(p, m.start(), m.end(), False, 0.8)
        m = re.search(r"अतिक्रमण\s+(?:आढळून\s+आले|आढळले|केलेले\s+आहे)|encroachment\s+(?:found|observed)", p.ascii, re.I)
        if m:
            return make_evidence(p, m.start(), m.end(), True, 0.8)
    return None


def grn(pages):
    return find_labeled(pages, r"GRN(?:\s*No\.?)?", r"([A-Z0-9]{10,24})", lambda m: m.group(1), window=40, confidence=0.9)


def first_of(*rules):
    def rule(pages):
        for r in rules:
            ev = r(pages)
            if ev:
                return ev
        return None

    return rule


def inspection_date(pages):
    """Prefer the date the inspection happened ("<date> रोजी … पाहणी/मोजणी") over the letter date."""
    rx = re.compile(r"(" + DATE_RE.pattern + r")\s*रोजी[^\n]{0,120}?(?:पाहणी|मोजणी)", re.I)
    for p in pages:
        m = rx.search(p.ascii)
        if m:
            d = parse_date(m.group(1))
            if d:
                return make_evidence(p, m.start(1), m.end(1), d, 0.85)
    return labeled_date(r"पाहणी\s*दिनांक|मोजणी\s*दिनांक|दिनांक\s*[:\-]?|inspected\s*on|date\s*of\s*inspection", 40)(pages)


# Measured area in a field / CTSO report: prefer "प्रत्यक्ष/मोजणी" wording, else the first area.
def measured_area(pages):
    return (labeled_area(r"प्रत्यक्ष\s*(?:मोजणी)?\s*क्षेत्र|मोजणीअंती\s*क्षेत्र|measured\s*area", 60)(pages)
            or find_first(pages, AREA_RE, area_from_match, group=0))


LETTER_DATE = labeled_date(r"दिनांक|दि\.|Date", 30)
REF_NO = labeled_text(r"जावक\s*क्र(?:मांक)?\.?|क्रमांक|क्र\.|No\.", r"([\w\u0900-\u097f][\w\u0900-\u097f()./\-]{3,60})", 20)
SOCIETY_NAME = FieldSpec("society_name", "text", "Full name of the cooperative housing society", society)
CTS_NO = FieldSpec("cts_no", "cts", "City survey (नगर भूमापन / CTS) number of the land", cts)
AREA = FieldSpec("area_sqm", "area", "Land area in square metres", labeled_area(r"क्षेत्र(?:फळ)?|Area", 60))
VILLAGE = FieldSpec("village", "text", "Village (मौजे) name", word_after(r"मौजे|गाव(?:\s*/\s*पेठ)?\s*[:\-]|Village\s*[:\-]?"))

FIELDS: dict[str, list[FieldSpec]] = {
    "APPLICATION": [
        SOCIETY_NAME,
        FieldSpec("registration_no", "text", "Society registration number", registration_no),
        CTS_NO,
        VILLAGE,
        FieldSpec("taluka", "text", "Taluka", word_after(r"(?<![\u0900-\u097f])(?:ता\.|तालुका(?![\u0900-\u097f]))")),
        FieldSpec("district", "text", "District", word_after(r"(?<![\u0900-\u097f])(?:जि\.|जिल्हा(?![\u0900-\u097f]))")),
        AREA,
        FieldSpec("application_date", "date", "Date of the application letter", first_date),
        FieldSpec("tenure", "enum", "Current tenure of the land", tenure, ("occupancy_class_2", "govt_lease", "occupancy_class_1")),
    ],
    "PROPERTY_CARD": [
        CTS_NO,
        VILLAGE,
        AREA,
        FieldSpec("holder_name", "text", "Holder (धारक) as recorded", labeled_text(r"धारकाचे\s*नाव|धारक", r"([^\n]{3,120})", 10)),
        FieldSpec("tenure", "enum", "Tenure (धारणाधिकार / सत्ता प्रकार)", tenure, ("occupancy_class_2", "govt_lease", "occupancy_class_1")),
        FieldSpec("issue_date", "date", "Date the certified copy was issued",
                  labeled_date(r"प्रत\s*(?:दिल्याचा)?\s*दिनांक|नक्कल\s*दिनांक|issued\s*on|दिनांक", 30, last=True)),
    ],
    "LEASE_SANAD": [
        FieldSpec("lease_date", "date", "Date of the lease deed / sanad", labeled_date(r"दिनांक|दि\.|dated", 40)),
        FieldSpec("lessee", "text", "Lessee (the society)", society),
        CTS_NO,
        AREA,
        FieldSpec("lease_period_years", "int", "Lease period in years", lease_years),
        FieldSpec("annual_rent", "amount", "Annual lease rent", labeled_amount(r"वार्षिक\s*भाडे|annual\s*rent")),
    ],
    "SGM_RESOLUTION": [
        SOCIETY_NAME,
        FieldSpec("meeting_date", "date", "Date of the special general meeting",
                  labeled_date(r"सभा\s*(?:दिनांक|दि\.)|सर्वसाधारण\s*सभा|meeting\s*held\s*on", 60)),
        FieldSpec("resolution_no", "text", "Resolution number", labeled_text(r"ठराव\s*क्र(?:मांक)?\.?|Resolution\s*No\.?", r"([0-9][0-9A-Za-z/\-]*)", 10)),
        FieldSpec("members_present", "int", "Members present", labeled_int(r"उपस्थित\s*सभासद(?:ांची)?\s*(?:संख्या)?\s*[:\-]?|members\s*present")),
        FieldSpec("members_total", "int", "Total members of the society", labeled_int(r"एकूण\s*सभासद(?:\s*संख्या)?\s*[:\-]?|total\s*members")),
    ],
    "QUERY_LETTER": [
        FieldSpec("letter_date", "date", "Date of the query letter", LETTER_DATE),
        FieldSpec("ref_no", "text", "Outward (जावक) reference number", REF_NO),
        FieldSpec("query_items", "list", "Each numbered deficiency the applicant must comply with", query_items),
    ],
    "QUERY_REPLY": [
        FieldSpec("letter_date", "date", "Date of the reply letter", LETTER_DATE),
    ],
    "FIELD_REPORT": [
        FieldSpec("inspection_date", "date", "Date of the site inspection / measurement", inspection_date),
        CTS_NO,
        FieldSpec("area_sqm", "area", "Area found on site (sq m)", measured_area),
        FieldSpec("encroachment", "bool", "Whether encroachment was found", encroachment),
    ],
    "CHALLAN": [
        FieldSpec("grn", "text", "GRAS GRN number", grn),
        FieldSpec("payment_date", "date", "Payment / challan date", labeled_date(r"Payment\s*Date|Date|दिनांक", 30)),
        FieldSpec("amount", "amount", "Amount paid",
                  first_of(labeled_amount(r"Total\s*Amount|एकूण\s*रक्कम", 60), labeled_amount(r"Amount|रक्कम", 60))),
        FieldSpec("head_of_account", "text", "Budget head of account", labeled_text(r"Head\s*of\s*Account|लेखाशीर्ष", r"(\d{4}[\d\s\-]*[^\n]{0,60})", 20)),
    ],
    "DEMAND_LETTER": [
        FieldSpec("letter_date", "date", "Date of the demand letter", LETTER_DATE),
        FieldSpec("amount", "amount", "Amount demanded",
                  first_of(labeled_amount(r"एकूण\s*रक्कम", 60), labeled_amount(r"नजराणा\s*रक्कम|अधिमूल्य\s*रक्कम|रक्कम", 60))),
    ],
    "COLLECTOR_ORDER": [
        FieldSpec("order_no", "text", "Order number", REF_NO),
        FieldSpec("order_date", "date", "Date of the order", LETTER_DATE),
        FieldSpec("decision", "enum", "Decision of the order", decision, ("granted", "rejected")),
        FieldSpec("premium_amount", "amount", "Premium / nazrana amount", labeled_amount(r"नजराणा|अधिमूल्य|premium", 80)),
        CTS_NO,
        AREA,
    ],
}
FIELDS["CTSO_REPORT"] = FIELDS["FIELD_REPORT"]

EXTRACTABLE = tuple(FIELDS)
