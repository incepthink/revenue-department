"""PII redaction: pattern recognisers (via Presidio), structural names, vault tokens."""
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rci.pipeline.normalise import ascii_digits, devanagari_digits
from rci.pipeline.redact import verhoeff
from rci.pipeline.redact.engine import detect_pattern_pii
from rci.pipeline.redact.structural import find_structural_names
from rci.pipeline.redact.vault import TOKEN_RE


def aadhaar(body11: str) -> str:
    n = body11 + verhoeff.check_digit(body11)
    return f"{n[:4]} {n[4:8]} {n[8:]}"


A1 = aadhaar("23456789012")
VID = (lambda b: f"{b[:4]} {b[4:8]} {b[8:12]} {b[12:15]}{verhoeff.check_digit(b)}")("912345678901234")


def entities(text):
    return {(s.entity, text[s.start : s.end]) for s in detect_pattern_pii(text)}


def test_aadhaar_ascii_and_devanagari_digits():
    assert ("AADHAAR", A1) in entities(f"आधार क्रमांक : {A1}")
    dev = devanagari_digits(A1)
    assert ("AADHAAR", dev) in entities(f"आधार क्रमांक : {dev}")


def test_aadhaar_in_table_row_next_to_mobile():
    row = f"१ [PERSON_x] ए-१०१ {devanagari_digits(A1)} {devanagari_digits('9765432109')}"
    found = entities(row)
    assert ("AADHAAR", devanagari_digits(A1)) in found
    assert ("PHONE", devanagari_digits("9765432109")) in found


def test_invalid_checksum_only_with_context():
    bad = A1[:-1] + str((int(A1[-1]) + 1) % 10)
    assert not any(e == "AADHAAR" for e, _ in entities(f"Scroll {bad} reference"))
    assert ("AADHAAR", bad) in entities(f"आधार क्र. {bad}")


def test_vid_is_one_entity_not_an_aadhaar_slice():
    found = entities(f"आधार व्हीआयडी : {VID}")
    assert ("AADHAAR_VID", VID) in found
    assert not any(e == "AADHAAR" for e, _ in found)


@pytest.mark.parametrize("mobile", ["9822012345", "98220 12345", "+91 98220 12345", "+91-98901-23456", "०९८२२०१२३४५"])
def test_mobile_variants(mobile):
    assert any(e == "PHONE" for e, _ in entities(f"मोबाईल : {mobile}"))


def test_pan_email_bank_account_near_ifsc():
    t = "पॅन : BQKPP4821M ई-मेल : a.b@example.in खाते क्रमांक ०३२१०४५६७८९० (IFSC : SBIN0001234)"
    found = entities(t)
    assert ("PAN", "BQKPP4821M") in found
    assert ("EMAIL", "a.b@example.in") in found
    assert ("BANK_ACCOUNT", "०३२१०४५६७८९०") in found


def test_ids_that_are_not_pii_survive():
    t = "GRN : MH004512345678202425\nBank CIN : 0002345202406100012345\nनोंदणी क्रमांक : PNA/HSG/(TC)/1234/1998-99\nपिन ४११०३८"
    assert entities(t) == set()


def test_structural_names_but_not_societies():
    t = ("अध्यक्ष : श्री. रमेश गणपत देशपांडे\nधारकाचे नाव : सुयोग सहकारी गृहनिर्माण संस्था मर्यादित\n"
         "मी, श्रीमती सुनिता विजय पाटील, वय ४८ वर्षे\nश्री गणेश कृपा सहकारी गृहनिर्माण संस्था")
    names = {t[s.start : s.end] for s in find_structural_names(t)}
    assert any("रमेश गणपत देशपांडे" in n for n in names)
    assert "सुनिता विजय पाटील" in names
    assert not any("संस्था" in n or "गणेश" in n for n in names)


def test_member_list_rows():
    t = "सभासदांची यादी\nअ.क्र. सभासदाचे नाव सदनिका क्र.\n१ श्री. प्रकाश नारायण जोशी ए-१०१\n२ Shri Rajesh K. Nair २\nप्रमाणित करण्यात येते"
    names = [t[s.start : s.end] for s in find_structural_names(t)]
    assert any("प्रकाश नारायण जोशी" in n and "ए" not in n.split() for n in names)
    assert any("Rajesh K. Nair" in n for n in names)


def test_redact_replaces_with_stable_tokens(redactor, vault, case_id):
    text = f"सचिव : श्रीमती सुनिता विजय पाटील\nमोबाईल : ९८९०१२३४५६\nआधार : {A1}\nपुन्हा मोबाईल 9890123456"
    r1 = redactor.redact(case_id, text)
    r2 = redactor.redact(case_id, text)
    assert r1.text == r2.text
    assert "९८९०१२३४५६" not in r1.text and A1 not in r1.text and "सुनिता" not in r1.text
    phones = [m.group(0) for m in TOKEN_RE.finditer(r1.text) if m.group("type") == "PHONE"]
    assert len(phones) == 2 and len(set(phones)) == 1  # same number, two scripts → one token
    assert vault.detokenize(case_id, phones[0]) == "९८९०१२३४५६"


def test_vault_ciphertext_and_case_scoping(vault, case_id):
    import uuid

    tok = vault.tokenize(case_id, "AADHAAR", A1)
    rec = vault.store.find_token(case_id, tok)
    assert A1.encode() not in rec.ciphertext
    other = vault.tokenize(uuid.uuid4(), "AADHAAR", A1)
    assert other != tok  # tokens do not link people across cases


@settings(max_examples=60, deadline=None)
@given(st.from_regex(r"[2-9][0-9]{10}", fullmatch=True), st.sampled_from(["ascii", "dev"]), st.sampled_from(["", " ", "-"]))
def test_no_valid_aadhaar_survives_redaction(body, script, sep):
    n = body + verhoeff.check_digit(body)
    num = sep.join([n[:4], n[4:8], n[8:]])
    if script == "dev":
        num = devanagari_digits(num)
    from rci.pipeline.redact.engine import Redactor
    from rci.pipeline.redact.vault import InMemoryVaultStore, Vault
    import uuid

    red = Redactor(Vault(InMemoryVaultStore(), b"k" * 32, b"h" * 32, "t")).redact(uuid.uuid4(), f"सभासद आधार {num} नोंद")
    assert n not in re.sub(r"\D", "", ascii_digits(TOKEN_RE.sub("", red.text)))
