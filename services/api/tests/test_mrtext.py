"""Marathi draft filters: mr_num, inr, mr_words (lakh / crore, exhaustively round-tripped), mr_rupees, mr_date."""
import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rci.drafting.mrtext import (
    CRORE,
    HUNDRED,
    LAKH,
    THOUSAND,
    UNITS,
    Blank,
    fy_label,
    inr,
    mr_date,
    mr_num,
    mr_rupees,
    mr_words,
    parse_mr_words,
)


def test_unit_table_is_complete_and_unambiguous():
    assert len(UNITS) == 100 and len(set(UNITS)) == 100
    assert not any(w.endswith("शे") for w in UNITS)  # the hundreds suffix can never be mistaken for a unit word
    assert not {HUNDRED, THOUSAND, LAKH, CRORE} & set(UNITS)
    assert all(" " not in w for w in UNITS)
    # spot checks against the written forms used in government letters
    assert [UNITS[i] for i in (0, 11, 19, 29, 45, 64, 67, 79, 99)] == [
        "शून्य", "अकरा", "एकोणीस", "एकोणतीस", "पंचेचाळीस", "चौसष्ट", "सदुसष्ट", "एकोणऐंशी", "नव्व्याण्णव"]
    # every 9 before a ten is "एकोण" + the next ten
    assert all(UNITS[t * 10 - 1].startswith("एकोण") for t in range(2, 10))


@pytest.mark.parametrize("n,words", [
    (0, "शून्य"), (7, "सात"), (100, "शंभर"), (101, "एकशे एक"), (150, "एकशे पन्नास"), (200, "दोनशे"), (999, "नऊशे नव्व्याण्णव"),
    (1000, "एक हजार"), (1100, "एक हजार शंभर"), (1150, "एक हजार एकशे पन्नास"), (10_000, "दहा हजार"), (99_999, "नव्व्याण्णव हजार नऊशे नव्व्याण्णव"),
    (100_000, "एक लाख"), (105_000, "एक लाख पाच हजार"), (4_564_325, "पंचेचाळीस लाख चौसष्ट हजार तीनशे पंचवीस"),
    (6_090_000, "साठ लाख नव्वद हजार"), (10_000_000, "एक कोटी"), (10_000_100, "एक कोटी शंभर"),
    (1_000_000_000, "शंभर कोटी"), (1_234_567_890, "एकशे तेवीस कोटी पंचेचाळीस लाख सदुसष्ट हजार आठशे नव्वद"),
    (123_456_789_012, "बारा हजार तीनशे पंचेचाळीस कोटी सदुसष्ट लाख एकोणनव्वद हजार बारा"),
])
def test_golden_words(n, words):
    assert mr_words(n) == words
    assert parse_mr_words(words) == n


def test_every_number_below_two_lakh_round_trips():
    seen = set()
    for n in range(200_000):
        w = mr_words(n)
        assert parse_mr_words(w) == n, n
        seen.add(w)
    assert len(seen) == 200_000  # no two numbers share a spelling


def _count(n: int, unit: int, group: int) -> int:
    """How often the word for `unit` (10^5 lakh / 10^3 thousand) appears: once per non-zero group, recursing into crores."""
    crore, rest = divmod(n, 10**7)
    return (_count(crore, unit, group) if crore else 0) + (1 if (rest // unit) % group else 0)


def _crores(n: int) -> int:
    return 0 if n < 10**7 else 1 + _crores(n // 10**7)


@settings(max_examples=3000, deadline=None)
@given(st.integers(min_value=0, max_value=10**15))
def test_words_round_trip_and_structure(n):
    w = mr_words(n)
    assert parse_mr_words(w) == n
    toks = w.split()
    assert toks.count(CRORE) == _crores(n)
    assert toks.count(LAKH) == _count(n, 10**5, 100)
    assert toks.count(THOUSAND) == _count(n, 10**3, 100)
    assert "  " not in w and w == w.strip() and all(t in UNITS or t in {HUNDRED, THOUSAND, LAKH, CRORE} or t.endswith("शे")
                                                   for t in toks)


@given(st.decimals(min_value=0, max_value=10**12, places=2, allow_nan=False, allow_infinity=False))
def test_inr_grouping_round_trips(d):
    s = inr(d, paise="always")
    assert Decimal(s.replace(",", "")) == d
    whole = s.split(".")[0]
    groups = whole.split(",")
    assert len(groups[-1]) == 3 or len(groups) == 1
    assert all(len(g) == 2 for g in groups[1:-1]) and 1 <= len(groups[0]) <= (3 if len(groups) == 1 else 2)


def test_inr_and_mr_num():
    assert inr(4564325) == "45,64,325" and inr("4564325.00") == "45,64,325"
    assert inr("1250.5") == "1,250.50" and inr(999) == "999" and inr(100000) == "1,00,000"
    assert inr("45,64,325/-") == "45,64,325" and inr("४५,६४,३२५") == "45,64,325"
    assert inr(7, paise="always") == "7.00" and inr("-1234567") == "-12,34,567"
    assert mr_num(inr(4564325)) == "४५,६४,३२५" and mr_num("1250.50") == "१२५०.५०" and mr_num(None) == ""


def test_rupees_in_words():
    assert mr_rupees("4564325.00") == "रुपये पंचेचाळीस लाख चौसष्ट हजार तीनशे पंचवीस फक्त"
    assert mr_rupees("1250.50") == "रुपये एक हजार दोनशे पन्नास आणि पन्नास पैसे फक्त"
    assert mr_rupees(0) == "रुपये शून्य फक्त"
    with pytest.raises(ValueError):
        mr_words(-5)


def test_dates():
    assert mr_date("2024-06-28") == "२८/०६/२०२४"
    assert mr_date(dt.date(2024, 6, 28), "long") == "२८ जून २०२४"
    assert mr_date("२०२४-०१-०५") == "०५/०१/२०२४" and mr_date(None) == ""
    assert fy_label(2024) == "२०२४-२५" and fy_label(1999) == "१९९९-००"


def test_blank_passes_through_every_filter():
    b = Blank("premium")
    for f in (mr_num, inr, mr_words, mr_rupees, mr_date):
        out = f(b)
        assert out == "________" and isinstance(out, Blank) and out.slot == "premium"
