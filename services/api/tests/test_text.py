"""Normalisation, Verhoeff and the legacy-font text-layer detector."""
from hypothesis import given
from hypothesis import strategies as st

from rci.pipeline.normalise import ascii_digits, devanagari_digits, normalise_text
from rci.pipeline.redact import verhoeff
from rci.pipeline.textlayer import assess_text_layer


@given(st.text())
def test_ascii_shadow_is_length_preserving(s):
    assert len(ascii_digits(s)) == len(s)


@given(st.from_regex(r"[0-9]{1,20}", fullmatch=True))
def test_digit_scripts_roundtrip(s):
    assert ascii_digits(devanagari_digits(s)) == s


def test_normalise_nfc_whitespace_and_dashes():
    na, nukta, zwsp, hyphen, nbsp = chr(0x0928), chr(0x093C), chr(0x200B), chr(0x2010), chr(0x00A0)
    crlf, lf = chr(13) + chr(10), chr(10)
    raw = na + nukta + zwsp + "गर   भूमापन" + hyphen + "क्र." + crlf * 3 + "पुढील" + nbsp + "ओळ"
    want = chr(0x0929) + "गर भूमापन-क्र." + lf * 2 + "पुढील ओळ"  # न + nukta composes (NFC)
    assert normalise_text(raw) == want


@given(st.from_regex(r"[0-9]{11}", fullmatch=True))
def test_verhoeff_check_digit_validates(payload):
    assert verhoeff.is_valid(payload + verhoeff.check_digit(payload))


@given(st.from_regex(r"[0-9]{11}", fullmatch=True), st.integers(0, 11), st.integers(1, 9))
def test_verhoeff_detects_single_digit_errors(payload, pos, delta):
    n = payload + verhoeff.check_digit(payload)
    bad = n[:pos] + str((int(n[pos]) + delta) % 10) + n[pos + 1 :]
    assert not verhoeff.is_valid(bad)


UNICODE_MR = "मिळकत पत्रिका\nनगर भूमापन क्रमांक १२३४/अ, क्षेत्र १२५०.५० चौ.मी.\nधारकाचे नाव : सुयोग सहकारी गृहनिर्माण संस्था"
# Real extraction output of a Nirmala-shaped PDF with a broken ToUnicode map (captured from PyMuPDF).
BROKEN_SHAPED = "म\nिळकत पũ\nिका\nनगर भूमापन Ţमांक १२३४/अ, Ɨेũ ५४२.७५ चौ.मी.\nŵी. राम पाटील"
# Kruti Dev 010 encoding of Devanagari text ("महाराष्ट्र शासन महसूल विभाग ...").
KRUTI = "egkjk\"Vª 'kklu] eglwy foHkkx] ftYgkf/kdkjh dk;kZy;] iq.ks ¼fe-i=½ uxj Hkwekiu Øekad"


def test_unicode_devanagari_layer_is_used():
    r = assess_text_layer(UNICODE_MR, ["Mangal"])
    assert r.status == "unicode" and r.use_text_layer


def test_english_layer_is_used():
    r = assess_text_layer("Government Receipt Accounting System\nGRN : MH004512345678202425\nTotal Amount : Rs. 45,64,325.00", ["Arial"])
    assert r.status == "unicode"


def test_broken_shaping_forces_ocr():
    r = assess_text_layer(BROKEN_SHAPED, ["Nirmala UI"])
    assert r.status == "broken" and not r.use_text_layer


def test_legacy_font_name_forces_ocr():
    r = assess_text_layer(KRUTI, ["KrutiDev010"])
    assert r.status == "legacy" and "KrutiDev010" in r.legacy_fonts


def test_legacy_glyph_soup_forces_ocr_without_font_names():
    r = assess_text_layer(KRUTI, [])
    assert r.status == "legacy"


def test_empty_layer():
    assert assess_text_layer("  \n ", []).status == "none"
