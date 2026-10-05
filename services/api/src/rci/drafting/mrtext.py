"""Marathi number, money and date formatting for drafts (Jinja filters).

    mr_num      digits → Devanagari digits                       1250.50 → १२५०.५०
    inr         Indian digit grouping (ASCII; chain |mr_num)      4564325 → 45,64,325   (paise shown only when non-zero)
    mr_words    integer → Marathi words, lakh / crore system      4564325 → पंचेचाळीस लाख चौसष्ट हजार तीनशे पंचवीस
    mr_rupees   amount → "रुपये … [आणि … पैसे] फक्त"
    mr_date     date → २८/०६/२०२४ (short) or २८ जून २०२४ (long)

Numbers 0–99 are irregular in Marathi, so they come from a table (standard written forms as used in Maharashtra
Government correspondence; spelling variants exist — see HANDOFF). Above 99: शंभर / एकशे …, हजार, लाख, कोटी; the crore
count is itself written recursively (123 crore → एकशे तेवीस कोटी). `parse_mr_words` is the exact inverse, used by the tests
to check every number round-trips.

Every filter passes a `Blank` (a missing slot's placeholder) through unchanged.
"""
from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from rci.pipeline.normalise import ascii_digits, devanagari_digits

UNITS: tuple[str, ...] = (
    "शून्य", "एक", "दोन", "तीन", "चार", "पाच", "सहा", "सात", "आठ", "नऊ",
    "दहा", "अकरा", "बारा", "तेरा", "चौदा", "पंधरा", "सोळा", "सतरा", "अठरा", "एकोणीस",
    "वीस", "एकवीस", "बावीस", "तेवीस", "चोवीस", "पंचवीस", "सव्वीस", "सत्तावीस", "अठ्ठावीस", "एकोणतीस",
    "तीस", "एकतीस", "बत्तीस", "तेहेतीस", "चौतीस", "पस्तीस", "छत्तीस", "सदतीस", "अडतीस", "एकोणचाळीस",
    "चाळीस", "एक्केचाळीस", "बेचाळीस", "त्रेचाळीस", "चव्वेचाळीस", "पंचेचाळीस", "सेहेचाळीस", "सत्तेचाळीस", "अठ्ठेचाळीस", "एकोणपन्नास",
    "पन्नास", "एक्कावन्न", "बावन्न", "त्रेपन्न", "चोपन्न", "पंचावन्न", "छप्पन्न", "सत्तावन्न", "अठ्ठावन्न", "एकोणसाठ",
    "साठ", "एकसष्ट", "बासष्ट", "त्रेसष्ट", "चौसष्ट", "पासष्ट", "सहासष्ट", "सदुसष्ट", "अडुसष्ट", "एकोणसत्तर",
    "सत्तर", "एकाहत्तर", "बाहत्तर", "त्र्याहत्तर", "चौऱ्याहत्तर", "पंचाहत्तर", "शहात्तर", "सत्याहत्तर", "अठ्ठ्याहत्तर", "एकोणऐंशी",
    "ऐंशी", "एक्क्याऐंशी", "ब्याऐंशी", "त्र्याऐंशी", "चौऱ्याऐंशी", "पंच्याऐंशी", "शहाऐंशी", "सत्त्याऐंशी", "अठ्ठ्याऐंशी", "एकोणनव्वद",
    "नव्वद", "एक्क्याण्णव", "ब्याण्णव", "त्र्याण्णव", "चौऱ्याण्णव", "पंच्याण्णव", "शहाण्णव", "सत्त्याण्णव", "अठ्ठ्याण्णव", "नव्व्याण्णव",
)
HUNDRED = "शंभर"  # exactly 100 (as a group of its own)
HUNDREDS_SUFFIX = "शे"  # 1xx → एकशे …, 200 → दोनशे, …
THOUSAND, LAKH, CRORE = "हजार", "लाख", "कोटी"
MONTHS = ("जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर")
BLANK = "________"


class Blank(str):
    """Placeholder for a slot with no value; every filter returns it unchanged so the draft shows a blank to fill."""

    def __new__(cls, name: str = ""):
        obj = super().__new__(cls, BLANK)
        obj.slot = name
        return obj


def _dec(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = ascii_digits(str(v)).strip().replace(",", "").replace("₹", "").replace("रु.", "").rstrip("/-").strip()
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"not a number: {v!r}") from None


# ------------------------------------------------------------------------------------------------
# Filters
# ------------------------------------------------------------------------------------------------
def mr_num(v) -> str:
    if v is None:
        return ""
    if isinstance(v, Blank):
        return v
    return devanagari_digits(str(v))


def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(([head] if head else []) + groups + [tail])


def inr(v, paise: str = "auto") -> str:
    """Indian grouping: 4564325 → 45,64,325; 1250.5 → 1,250.50. paise = auto (only if non-zero) | always | never."""
    if isinstance(v, Blank) or v is None:
        return v if isinstance(v, Blank) else ""
    d = _dec(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if d < 0 else ""
    d = abs(d)
    rupees, p = divmod(int(d * 100), 100)
    out = sign + _group_indian(str(rupees))
    if paise == "always" or (paise == "auto" and p):
        out += f".{p:02d}"
    return out


def _below_1000(n: int) -> list[str]:
    h, rest = divmod(n, 100)
    out = []
    if h:
        out.append(HUNDRED if (h == 1 and rest == 0) else UNITS[h] + HUNDREDS_SUFFIX)
    if rest:
        out.append(UNITS[rest])
    return out


def _below_crore(n: int) -> list[str]:
    lakh, rest = divmod(n, 100_000)
    thousand, rest = divmod(rest, 1000)
    out = []
    if lakh:
        out += [UNITS[lakh], LAKH]
    if thousand:
        out += [UNITS[thousand], THOUSAND]
    out += _below_1000(rest)
    return out


def _words(n: int) -> list[str]:
    crore, rest = divmod(n, 10_000_000)
    out = (_words(crore) + [CRORE]) if crore else []
    return out + _below_crore(rest)


def mr_words(v) -> str:
    """Whole number in Marathi words (decimals are dropped: use mr_rupees for money)."""
    if isinstance(v, Blank):
        return v
    d = _dec(v)
    if d < 0:
        raise ValueError("negative amounts are not written in words")
    n = int(d)
    return UNITS[0] if n == 0 else " ".join(_words(n))


def mr_rupees(v) -> str:
    if isinstance(v, Blank):
        return v
    d = _dec(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rupees, paise = divmod(int(d * 100), 100)
    text = f"रुपये {mr_words(rupees)}"
    if paise:
        text += f" आणि {mr_words(paise)} पैसे"
    return text + " फक्त"


def to_date(v) -> dt.date | None:
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(ascii_digits(str(v))[:10])


def mr_date(v, style: str = "short") -> str:
    if isinstance(v, Blank):
        return v
    d = to_date(v)
    if d is None:
        return ""
    if style == "long":
        return devanagari_digits(f"{d.day} ") + MONTHS[d.month - 1] + devanagari_digits(f" {d.year}")
    return devanagari_digits(d.strftime("%d/%m/%Y"))


def fy_label(year: int | None) -> str:
    """ASR year 2024 → २०२४-२५."""
    if year is None:
        return ""
    return devanagari_digits(f"{year}-{str(year + 1)[-2:]}")


FILTERS = {"mr_num": mr_num, "inr": inr, "mr_words": mr_words, "mr_rupees": mr_rupees, "mr_date": mr_date, "fy_label": fy_label}


# ------------------------------------------------------------------------------------------------
# Inverse (for tests and for reading amounts written in words)
# ------------------------------------------------------------------------------------------------
_UNIT_VALUE = {w: i for i, w in enumerate(UNITS)}


def _parse_below_crore(tokens: list[str]) -> int:
    total, cur = 0, 0
    for t in tokens:
        if t in _UNIT_VALUE:
            cur += _UNIT_VALUE[t]
        elif t == HUNDRED:
            cur += 100
        elif t.endswith(HUNDREDS_SUFFIX) and t[: -len(HUNDREDS_SUFFIX)] in _UNIT_VALUE:
            cur += 100 * _UNIT_VALUE[t[: -len(HUNDREDS_SUFFIX)]]
        elif t == THOUSAND:
            total, cur = total + cur * 1000, 0
        elif t == LAKH:
            total, cur = total + cur * 100_000, 0
        else:
            raise ValueError(f"not a Marathi number word: {t!r}")
    return total + cur


def parse_mr_words(text: str) -> int:
    tokens = text.split()
    if tokens == [UNITS[0]]:
        return 0
    if CRORE in tokens:
        i = len(tokens) - 1 - tokens[::-1].index(CRORE)
        return parse_mr_words(" ".join(tokens[:i])) * 10_000_000 + _parse_below_crore(tokens[i + 1:])
    return _parse_below_crore(tokens)
