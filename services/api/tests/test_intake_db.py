"""Intake end to end against the Postgres test database: upload per slot, run, poll, read the results."""
import pymupdf
import pytest
from sqlalchemy import func, select

from conftest import SAMPLES
from rci.db.models import AuditLog, Document, PipelineStep, RuleDocumentCheck

pytestmark = pytest.mark.db


def pages_of(filename: str, *indices: int) -> bytes:
    """A new PDF made of some pages of a DKT-002 sample file (one filing document per upload slot)."""
    src = pymupdf.open(SAMPLES / "dockets" / "DKT-002" / filename)
    out = pymupdf.open()
    for i in indices:
        out.insert_pdf(src, from_page=i, to_page=i)
    return out.tobytes()


def upload(client, case_id, named: list[tuple[str, str, bytes]], **params):
    files = [("files", (name, data, "application/pdf")) for _, name, data in named]
    return client.post(f"/cases/{case_id}/documents", params=params, files=files, data={"slots": [s for s, _, _ in named]})


def test_intake_flow(api, db_session):
    client = api("officer")
    case_id = client.post("/cases", json={"case_ref": "INTAKE-001", "data_class": "synthetic"}).json()["id"]
    r = upload(client, case_id, [("application", "arj.pdf", pages_of("01_arj.pdf", 0)),
                                 ("grant", "lease.pdf", pages_of("01_arj.pdf", 1, 2)),
                                 ("property_card", "card.pdf", pages_of("02_kagadpatre.pdf", 0))], process="false")
    assert r.status_code == 201, r.text
    assert [d["slot"] for d in r.json()["documents"]] == ["application", "grant", "property_card"]
    assert "processing" not in r.json()

    before = client.get(f"/cases/{case_id}/intake/status").json()
    assert before["state"] == "not_started" and {d["stage"] for d in before["documents"]} == {"queued"}

    r = client.post(f"/cases/{case_id}/intake")
    assert r.status_code == 202 and r.json()["mode"] == "background"  # TestClient runs the task before returning

    status = client.get(f"/cases/{case_id}/intake/status").json()
    assert status["state"] == "done" and not status["assembling"]
    assert status["progress"] == {"total": 3, "read": 3, "checked": 3}
    assert status["report"] is None and "result" not in status["documents"][0]  # the poll carries no extracted values

    view = client.get(f"/cases/{case_id}/intake").json()
    rep = view["report"]
    by = {}
    for f in rep["findings"]:
        by.setdefault(f["code"], []).append(f)
    assert rep["verdict"] == "incomplete"
    # required by the seeded 2019 rules; the resolution and (lease land) the no-dues certificate have no slot of their own
    assert sorted(s for f in by["DOC_MISSING"] for s in f["slots"]) == ["aadhaar", "index_ii", "other", "other", "satbara", "tax_receipts"]
    assert {f["rule"]["code"] for f in by["DOC_MISSING"] if f["slots"] == ["other"]} == {"SGM_RESOLUTION", "NO_DUES_LEASE_RENT"}
    assert all(f["level"] == "problem" and f["rule"]["rule_set_code"] == "RULES_2019" for f in by["DOC_MISSING"])
    assert rep["rule_facts"] == {"tenure": "govt_lease"} and rep["rules_applied"]["on"] == "2025-01-15"
    assert by["LAND_CTS_MATCH"][0]["level"] == "ok"  # 567 on the application, the lease and the property card
    assert by["LAND_AREA_MATCH"][0]["level"] == "ok"
    assert by["PROPERTY_CARD_RECENT"][0]["level"] == "problem"  # issued 01/06/2023, application 15/01/2025
    assert by["HOLDER_IS_SOCIETY"][0]["level"] == "warning"  # an individual is still recorded; government-lease land
    assert rep["limited"] and rep["reference_date"] == "2025-01-15"
    app_doc = next(d for d in view["documents"] if d["slot"] == "application")
    assert app_doc["stage"] == "checked" and app_doc["result"]["fields"]["application_date"]["value"] == "2025-01-15"
    assert app_doc["field_labels"]["application_date"] == "Dated"

    # the usual pipeline ran too: the case has its draft card for review and analysis
    case = client.get(f"/cases/{case_id}").json()
    assert case["status"] == "ingested" and case["card"]["version"] == 1

    # reading the results is audited; polling is not
    audited = db_session.scalars(select(AuditLog.details["route"].astext).where(AuditLog.case_id == case_id)).all()
    assert "/cases/{case_id}/intake" in audited and "/cases/{case_id}/intake/status" not in audited

    # a second run re-uses every document's scrutiny
    assert client.post(f"/cases/{case_id}/intake").status_code == 202
    assert client.get(f"/cases/{case_id}/intake").json()["report"]["verdict"] == "incomplete"
    runs = db_session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.case_id == case_id,
                                                                              AuditLog.action == "intake.report"))
    assert runs == 2


def test_intake_rejects_bad_input(api):
    client = api("officer")
    case_id = client.post("/cases", json={"case_ref": "INTAKE-002", "data_class": "synthetic"}).json()["id"]
    data = pages_of("01_arj.pdf", 0)
    assert upload(client, case_id, [("nope", "a.pdf", data)], process="false").status_code == 422
    r = client.post(f"/cases/{case_id}/documents", params={"process": "false"},
                    files=[("files", ("a.pdf", data, "application/pdf"))], data={"slots": ["application", "grant"]})
    assert r.status_code == 422
    assert client.post(f"/cases/{case_id}/intake").status_code == 422  # nothing uploaded for a slot
    assert api(None).get(f"/cases/{case_id}/intake/status").status_code == 401


def test_intake_identifies_files_uploaded_without_a_slot(api):
    client = api("officer")
    case_id = client.post("/cases", json={"case_ref": "INTAKE-003", "data_class": "synthetic"}).json()["id"]
    r = upload(client, case_id, [("auto", "scan-a.pdf", pages_of("01_arj.pdf", 0)),
                                 ("auto", "scan-b.pdf", pages_of("01_arj.pdf", 1, 2)),
                                 ("auto", "scan-c.pdf", pages_of("02_kagadpatre.pdf", 0)),
                                 ("auto", "scan-d.pdf", pages_of("02_kagadpatre.pdf", 1))], process="false")
    assert r.status_code == 201, r.text
    assert {d["slot"] for d in r.json()["documents"]} == {"auto"}
    assert client.post(f"/cases/{case_id}/intake").status_code == 202

    view = client.get(f"/cases/{case_id}/intake").json()
    slots = {d["filename"]: d["slot"] for d in view["documents"]}
    assert slots == {"scan-a.pdf": "application", "scan-b.pdf": "grant", "scan-c.pdf": "property_card", "scan-d.pdf": "other"}
    assert all(d["identified"]["method"] == "rules" for d in view["documents"])
    by = {f["code"]: f for f in view["report"]["findings"]}
    assert by["LAND_CTS_MATCH"]["level"] == "ok" and by["DOC_ADDITIONAL"]["slots"] == ["other"]
    missing = [f for f in view["report"]["findings"] if f["code"] == "DOC_MISSING"]
    assert sorted(s for f in missing if f["level"] == "problem" for s in f["slots"]) == ["aadhaar", "index_ii", "satbara", "tax_receipts"]
    # scan-d.pdf was read without the AI reader, so it may be the resolution / no-dues certificate: not decided
    assert {f["rule"]["code"] for f in missing if f["level"] == "unchecked"} == {"SGM_RESOLUTION", "NO_DUES_LEASE_RENT"}
    assert any(s["key"] == "other" for s in view["slots"])


def test_editing_a_document_check_rereads_only_its_documents(api, db_session):
    client, admin = api("officer"), api("admin")
    case_id = client.post("/cases", json={"case_ref": "INTAKE-004", "data_class": "synthetic"}).json()["id"]
    upload(client, case_id, [("application", "arj.pdf", pages_of("01_arj.pdf", 0)),
                             ("property_card", "card.pdf", pages_of("02_kagadpatre.pdf", 0))], process="false")
    assert client.post(f"/cases/{case_id}/intake").status_code == 202

    def hashes() -> dict[str, str]:
        db_session.expire_all()
        slot_of = dict(db_session.execute(select(Document.sha256, Document.slot).where(Document.case_id == case_id)).all())
        rows = db_session.scalars(select(PipelineStep).where(PipelineStep.case_id == case_id, PipelineStep.step == "intake",
                                                              PipelineStep.scope != "case"))
        return {slot_of[r.scope]: r.input_hash for r in rows}

    before = hashes()
    rep = client.get(f"/cases/{case_id}/intake").json()["report"]
    checked = [f for f in rep["findings"] if f["code"] == "RULE_CHECK"]
    # without an AI reader the rules for both documents are sent but not judged; each finding still cites its rule
    assert {f["slots"][0] for f in checked} == {"application", "property_card"}
    assert "APPLICATION_SIGNED_BY_OFFICE_BEARER" in {f["rule"]["code"] for f in checked}
    assert all(f["level"] == "unchecked" and f["rule"]["label"] == "rule not yet verified" for f in checked)

    rs = next(r for r in admin.get("/admin/rule-sets").json() if r["code"] == "RULES_2019")
    assert rs["counts"]["document_checks"]["total"] == 41
    row = next(x for x in admin.get(f"/admin/rule-sets/{rs['id']}").json()["document_checks"]
               if x["code"] == "APPLICATION_SIGNED_BY_OFFICE_BEARER")
    url = f"/admin/rules/document_checks/{row['id']}"
    assert admin.patch(url, json={"slot": "nope"}).status_code == 422
    assert admin.patch(url, json={"severity": "fatal"}).status_code == 422
    r = admin.patch(url, json={"status": "verified"})
    assert r.status_code == 422 and "upload the notification" in r.json()["detail"]
    try:
        assert admin.patch(url, json={"text_en": row["text_en"] + " (edited)"}).status_code == 200
        assert client.post(f"/cases/{case_id}/intake").status_code == 202
        after = hashes()
        assert after["application"] != before["application"] and after["property_card"] == before["property_card"]
        rep = client.get(f"/cases/{case_id}/intake").json()["report"]
        assert next(f for f in rep["findings"] if f["code"] == "RULE_CHECK"
                    and f["rule"]["code"] == "APPLICATION_SIGNED_BY_OFFICE_BEARER")["title"].endswith("(edited)")

        new = admin.post(f"/admin/rule-sets/{rs['id']}/document_checks",
                         json={"code": "TEST_CHECK", "slot": "grant", "text_en": "The grant is dated.", "severity": "info"})
        assert new.status_code == 201 and new.json()["status"] == "to_verify"
        assert admin.delete(f"/admin/rules/document_checks/{new.json()['id']}").status_code == 204
        assert admin.delete(url).status_code == 409  # seeded rows are not deletable
    finally:  # leave the shared test DB as seeded
        obj = db_session.get(RuleDocumentCheck, row["id"])
        obj.text_en, obj.updated_by, obj.updated_at = row["text_en"], None, None
        db_session.commit()
