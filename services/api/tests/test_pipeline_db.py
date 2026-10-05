"""End-to-end on the sample dockets against the Postgres test database (docker compose up -d db)."""
import json

import pytest
from sqlalchemy import func, select

from conftest import SAMPLES, sample_files
from rci.db.models import AuditLog, CaseCard, CaseEvent, CaseRequirement, Exhibit, Page, PiiToken
from rci.pipeline.runner import PipelineContext, add_document, get_or_create_case, latest_card, process_case

pytestmark = pytest.mark.db


def ingest(session, case_ref):
    ctx = PipelineContext.default()
    case, _ = get_or_create_case(session, case_ref=case_ref, data_class="synthetic")
    for name, data in sample_files(case_ref):
        add_document(session, ctx.blobs, case, name, data)
    summary = process_case(session, case.id, ctx)
    session.commit()
    return case, summary


def counts(session, case_id):
    return {m.__tablename__: session.scalar(select(func.count()).select_from(m).where(m.case_id == case_id))
            for m in (Page, Exhibit, CaseCard, CaseRequirement, CaseEvent, PiiToken)}


def test_ingest_boundaries_card_and_idempotency(db_session):
    expected = json.loads((SAMPLES / "expected" / "DKT-001.json").read_text(encoding="utf-8"))
    case, first = ingest(db_session, "DKT-001")
    assert first.card_created and first.pages == expected["page_count"]
    card = latest_card(db_session, case.id).card
    got = [{"type": e["type"], "stage": e["stage"], "pages": [e["pages"][0], e["pages"][-1]]} for e in card["exhibits"]]
    assert got == [{k: e[k] for k in ("type", "stage", "pages")} for e in expected["exhibits"]]
    assert card["facts"]["land"]["cts_no"]["page_seq"] >= 1

    before = counts(db_session, case.id)
    hash_before = latest_card(db_session, case.id).content_hash
    _, second = ingest(db_session, "DKT-001")
    assert not second.card_created and second.documents_skipped == 1 and second.documents_processed == 0
    assert counts(db_session, case.id) == before
    assert latest_card(db_session, case.id).content_hash == hash_before


def test_redacted_pages_hold_no_aadhaar_or_phone(db_session):
    from rci.pipeline.normalise import ascii_digits
    from rci.pipeline.redact import verhoeff
    import re

    case, _ = ingest(db_session, "DKT-003")
    pages = db_session.scalars(select(Page).where(Page.case_id == case.id)).all()
    assert pages
    for p in pages:
        t = ascii_digits(re.sub(r"\[[A-Z_]+_[0-9a-f]{8}\]", " ", p.text_redacted))
        for m in re.finditer(r"(?<!\d)(\d{4}[ -]?\d{4}[ -]?\d{4}(?:[ -]?\d{4})?)(?!\d)", t):
            assert not verhoeff.is_valid(m.group(1)), (p.seq, m.group(1))
        assert not re.search(r"(?<![\dA-Za-z])(?:\+91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?![\dA-Za-z])", t), p.seq


def test_api_flow(api):
    client = api("officer")
    r = client.post("/cases", json={"case_ref": "API-DKT-002", "data_class": "synthetic"})
    assert r.status_code == 201, r.text
    case_id = r.json()["id"]
    assert client.post("/cases", json={"case_ref": "API-DKT-002", "data_class": "synthetic"}).status_code == 409
    files = [("files", (n, d, "application/pdf")) for n, d in sample_files("DKT-002")]
    r = client.post(f"/cases/{case_id}/documents", files=files)
    assert r.status_code == 201, r.text
    assert r.json()["processing"]["summary"]["card_created"]
    body = client.get(f"/cases/{case_id}").json()
    assert len(body["exhibits"]) == 8 and body["card"]["version"] == 1
    statuses = {v["code"]: v["status"] for v in body["card"]["card"]["validations"]}
    assert statuses["SGM_DATE_ORDER"] == "fail" and statuses["AREA_TOLERANCE"] == "fail"
    assert client.get(f"/cases/{case_id}/pages/1").json()["text_redacted"]
    # re-upload of the same files is a no-op
    r = client.post(f"/cases/{case_id}/documents", files=files)
    assert all(not d["created"] for d in r.json()["documents"])
    assert r.json()["processing"]["summary"]["card_created"] is False


def test_audit_log_written(db_session):
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "pipeline.run")) >= 1
