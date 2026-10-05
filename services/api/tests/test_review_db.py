"""Review workflow end-to-end on the sample dockets (test DB): single review → approved locked card with
requirements, events, summary, embeddings and correction log; double review → agreement report; auth,
roles, audit, masked page images, rules admin verification, vault guard."""
import uuid

import jsonschema
import pytest
from sqlalchemy import func, select

from conftest import ingest_case
from rci.db.models import (
    AuditLog,
    CaseCard,
    CaseEmbedding,
    CaseEvent,
    CaseRequirement,
    Exhibit,
    ExtractionCorrection,
    RequirementCatalog,
    RulePremiumSlab,
    RuleSet,
)
from rci.pipeline.card import card_schema
from rci.pipeline.runner import PipelineContext, latest_card, process_case
from rci.review import service
from rci.review.service import Conflict
from rci.review.state import ReviewError

pytestmark = pytest.mark.db


@pytest.fixture
def single_mode(monkeypatch):
    monkeypatch.setattr(service, "double_review_quota", lambda session: (0, 0))


@pytest.fixture
def double_mode(monkeypatch):
    monkeypatch.setattr(service, "double_review_quota", lambda session: (0, 10))


def ex_by_type(view, t, n=0):
    return [e for e in view["exhibits"] if e["type"] == t][n]


def op(session, case, user, *ops):
    service.apply(session, case, user, list(ops))
    session.flush()
    return service.view(session, case, user)


def map_all_queries(session, case, user, view):
    ops = []
    for q in view["queries"]:
        for it in q["items"]:
            codes = it["suggested_codes"]
            ops.append({"op": "query.map", "exhibit_id": q["exhibit_id"], "index": it["index"],
                        "codes": codes, "not_requirement": not codes})
    return op(session, case, user, *ops)


def test_single_review_to_approved_card(db_session, users, single_mode):
    s = db_session
    rv = users["reviewer1"]
    case = ingest_case(s, "REV-DKT-002", "DKT-002")
    draft = latest_card(s, case.id)
    a = service.start_review(s, case, rv)
    assert a.mode == "single" and a.base_card_id == draft.id
    v = service.view(s, case, rv)
    assert v["progress"]["fields_undecided"] > 0 and not v["can_approve"] and v["blockers"]
    with pytest.raises(Conflict):
        service.approve(s, case, rv)

    # --- exhibit rail: split / merge round trip, retype, stage
    lease = ex_by_type(v, "LEASE_SANAD")
    v = op(s, case, rv, {"op": "exhibit.split", "exhibit_id": lease["exhibit_id"], "at_page_seq": 3})
    parts = [e for e in v["exhibits"] if e["type"] == "LEASE_SANAD"]
    assert [(e["start"], e["end"]) for e in parts] == [(2, 2), (3, 3)] and all(e["origin"] == "manual" for e in parts)
    v = op(s, case, rv, {"op": "exhibit.merge", "exhibit_id": parts[0]["exhibit_id"]})
    lease = ex_by_type(v, "LEASE_SANAD")
    assert (lease["start"], lease["end"]) == (2, 3) and len(v["exhibits"]) == 8
    with pytest.raises(ReviewError):
        service.apply(s, case, rv, [{"op": "exhibit.split", "exhibit_id": lease["exhibit_id"], "at_page_seq": 2}])
    ml = ex_by_type(v, "MEMBER_LIST")
    v = op(s, case, rv, {"op": "exhibit.stage", "exhibit_id": ml["exhibit_id"], "stage": "query_response"})
    assert ex_by_type(v, "MEMBER_LIST")["stage"] == "query_response"

    # --- card form: page reference required, must be inside the exhibit, values typed
    pc = ex_by_type(v, "PROPERTY_CARD")
    base = {"op": "field.decide", "exhibit_id": pc["exhibit_id"], "field": "area_sqm", "action": "edit"}
    with pytest.raises(ReviewError, match="page reference"):
        service.apply(s, case, rv, [{**base, "value": "805"}])
    with pytest.raises(ReviewError, match="outside this exhibit"):
        service.apply(s, case, rv, [{**base, "value": "805", "page_seq": 1}])
    with pytest.raises(ReviewError, match="not a non-negative number"):
        service.apply(s, case, rv, [{**base, "value": "eight hundred", "page_seq": pc["start"]}])
    v = op(s, case, rv, {**base, "value": "805.5", "page_seq": pc["start"], "raw": "८००.०० चौ.मी."},
           {"op": "field.decide", "exhibit_id": pc["exhibit_id"], "field": "tenure", "action": "accept"})
    area = next(f for f in ex_by_type(v, "PROPERTY_CARD")["fields"] if f["name"] == "area_sqm")
    assert area["current"]["value"] == "805.50" and area["current"]["method"] == "manual" and area["current"]["located"]

    # --- query mapper: create a new code, map items, mark one "not a requirement"
    s.add(RequirementCatalog(code="ENCROACHMENT_CLARIFICATION", name_en="Clarification on encroachment",
                             name_mr="अतिक्रमणाबाबत खुलासा", category="legal_condition", satisfied_by=[], query_keywords=[],
                             applies_when={}, status="to_verify", created_by=rv.id))
    s.flush()
    v = map_all_queries(s, case, rv, service.view(s, case, rv))
    q = v["queries"][0]
    last = q["items"][-1]
    v = op(s, case, rv, {"op": "query.map", "exhibit_id": q["exhibit_id"], "index": last["index"],
                         "codes": ["ENCROACHMENT_CLARIFICATION"]},
           {"op": "query.map", "exhibit_id": q["exhibit_id"], "index": 2, "codes": [], "not_requirement": True})
    with pytest.raises(ReviewError, match="unknown requirement"):
        service.apply(s, case, rv, [{"op": "query.map", "exhibit_id": q["exhibit_id"], "index": 0, "codes": ["NOPE"]}])
    assert v["progress"]["query_items_unmapped"] == 0

    # --- requirements matrix override, events editor
    v = op(s, case, rv, {"op": "requirement.set", "code": "OCCUPANCY_CERTIFICATE", "status": "waived", "notes": "old building"})
    occ = next(x for x in v["requirements"] if x["code"] == "OCCUPANCY_CERTIFICATE")
    assert occ["status"] == "waived" and occ["derived_status"] == "missing" and occ["origin"] == "review"
    events = [{"code": e["code"], "date": e["date"], "exhibit_id": e["exhibit_id"],
               "page_seq": (e.get("evidence") or {}).get("page_seq")} for e in v["events"]]
    ql = ex_by_type(v, "QUERY_LETTER")
    events.append({"code": "QUERY_REPLIED", "date": "2025-04-02", "exhibit_id": ql["exhibit_id"], "page_seq": ql["start"]})
    v = op(s, case, rv, {"op": "events.set", "events": events})
    assert v["events_overridden"] and [e["code"] for e in v["events"]][-1] == "QUERY_REPLIED"

    # --- accept the rest, approve
    v = op(s, case, rv, {"op": "field.accept_all"})
    assert v["progress"]["fields_undecided"] == 0 and v["can_approve"], v["blockers"]
    card = service.approve(s, case, rv)
    s.commit()

    assert card.status == "approved" and card.locked and card.version == draft.version + 1 and card.parent_card_id == draft.id
    jsonschema.validate(card.card, card_schema())
    assert card.card["summary"]["mr"] and "805.50" in card.card["summary"]["en"]
    assert card.card["approval"]["review_mode"] == "single"
    assert s.get(CaseCard, draft.id).status == "superseded"
    n_catalog = s.scalar(select(func.count()).select_from(RequirementCatalog).where(RequirementCatalog.active))
    reqs = s.scalars(select(CaseRequirement).where(CaseRequirement.card_id == card.id)).all()
    assert len(reqs) == n_catalog
    by_code = {r.requirement_code: r for r in reqs}
    assert by_code["OCCUPANCY_CERTIFICATE"].status == "waived" and by_code["OCCUPANCY_CERTIFICATE"].origin == "review"
    assert by_code["ENCROACHMENT_CLARIFICATION"].status == "queried" and by_code["ENCROACHMENT_CLARIFICATION"].was_queried_in
    evs = s.scalars(select(CaseEvent).where(CaseEvent.card_id == card.id).order_by(CaseEvent.seq)).all()
    assert [e.event_code for e in evs][-1] == "QUERY_REPLIED" and all(e.origin == "review" for e in evs)
    embs = s.scalars(select(CaseEmbedding).where(CaseEmbedding.card_id == card.id)).all()
    assert sorted(e.facet for e in embs) == ["documents", "facts", "issues"]
    assert all(len(e.embedding) == 1536 and e.model == "local-hash-v1" and e.as_of for e in embs)
    assert "ENCROACHMENT_CLARIFICATION" in next(e.source_text for e in embs if e.facet == "issues")
    exs = s.scalars(select(Exhibit).where(Exhibit.case_id == case.id)).all()
    assert all(e.status == "approved" and e.extraction["reviewed"] for e in exs)
    assert next(e for e in exs if e.exhibit_type == "MEMBER_LIST").origin == "manual"

    corr = s.scalars(select(ExtractionCorrection).where(ExtractionCorrection.approved_card_id == card.id)).all()
    area_c = next(c for c in corr if c.field_path == "PROPERTY_CARD.area_sqm")
    assert area_c.action == "edit" and area_c.draft_value["value"] == "800.00" and area_c.approved_value["value"] == "805.50"
    assert any(c.field_path == "MEMBER_LIST._stage" for c in corr)
    assert sum(c.action == "accept" for c in corr) > 10
    rep = service.extraction_accuracy(s)
    assert rep["cases"] >= 1 and rep["by_field"]["PROPERTY_CARD.area_sqm"]["edit"] >= 1 and rep["overall"]["precision"] < 1

    # locked: pipeline re-run does not touch the approved card, the review cannot be approved twice
    summary = process_case(s, case.id, PipelineContext.default())
    assert not summary.card_created and latest_card(s, case.id).id == card.id
    with pytest.raises(Conflict):
        service.approve(s, case, rv)
    with pytest.raises(Conflict):
        service.start_review(s, case, users["reviewer2"])
    assert s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.case_id == case.id, AuditLog.action == "card.approve")) == 1

    # admin reopens → round 2 starts from the approved card with every field accepted; no corrections logged
    a2 = service.start_review(s, case, users["admin"], new_round=True)
    assert a2.round == 2 and a2.mode == "single"
    v2 = service.view(s, case, users["admin"])
    assert v2["progress"]["fields_undecided"] == 0 and v2["progress"]["query_items_unmapped"] == 0
    card2 = service.approve(s, case, users["admin"])
    assert card2.version == card.version + 1
    assert {r["code"]: r["status"] for r in card2.card["requirements"]} == {r["code"]: r["status"] for r in card.card["requirements"]}
    assert not s.scalars(select(ExtractionCorrection).where(ExtractionCorrection.approved_card_id == card2.id)).all()
    s.commit()


def test_double_review_agreement(db_session, users, double_mode):
    s = db_session
    r1, r2 = users["reviewer1"], users["reviewer2"]
    case = ingest_case(s, "REV-DKT-003", "DKT-003")
    assert service.start_review(s, case, r1).mode == "double"
    assert service.start_review(s, case, r2).mode == "double"
    with pytest.raises(Conflict, match="two reviewers"):
        service.start_review(s, case, users["admin"])
    for user, area in ((r1, None), (r2, "2150")):
        v = service.view(s, case, user)
        pc = ex_by_type(v, "PROPERTY_CARD", 1)
        ops = [{"op": "field.accept_all"}]
        if area:
            ops.insert(0, {"op": "field.decide", "exhibit_id": pc["exhibit_id"], "field": "area_sqm", "action": "edit",
                           "value": area, "page_seq": pc["start"]})
        v = op(s, case, user, *ops)
        v = map_all_queries(s, case, user, v)
        assert v["disagreements"] is None  # nothing is shown before both submitted
    with pytest.raises(Conflict, match="both reviewers"):
        service.approve(s, case, r1)
    service.submit(s, case, r1)
    assert service.view(s, case, r1)["disagreements"] is None
    service.submit(s, case, r2)
    d = service.view(s, case, r1)["disagreements"]
    assert d["segmentation"] == 1.0 and d["fields"] < 1.0
    assert any(x["kind"] == "field" and x["field"] == "area_sqm" and {x["a"], x["b"]} == {"2100.00", "2150.00"}
               for x in d["disagreements"])
    card = service.approve(s, case, r2)
    s.commit()
    assert card.card["approval"]["review_mode"] == "double" and len(card.card["approval"]["reviewers"]) == 2
    rep = service.agreement_report(s)
    row = next(c for c in rep["per_case"] if c["case_ref"] == "REV-DKT-003")
    assert row["segmentation"] == 1.0 and row["fields"] < 1.0
    # 2150 vs 2100 sq m also flips AREA_TOLERANCE, so the CTSO measurement requirement differs too
    assert [x["code"] for x in row["disagreements"] if x["kind"] == "requirement"] == ["CTSO_MEASUREMENT"]
    assert rep["requirements_kappa"] is not None
    assert "REV-DKT-003" in service.report_markdown("agreement", rep)


# ---------------------------------------------------------------------------------------------
# HTTP: auth, roles, audit, page viewer, admin
# ---------------------------------------------------------------------------------------------
def test_auth_roles_and_audit(api, db_session, single_mode):
    anon = api(None)
    assert anon.get("/cases").status_code == 200  # no login: tokenless requests act as the operator
    assert anon.get("/auth/me").json()["role"] == "admin"
    assert anon.post("/auth/login", json={"email": "officer@test.local", "password": "wrong-password"}).status_code == 401
    officer, reviewer, admin = api("officer"), api("reviewer1"), api("admin")
    assert officer.get("/auth/me").json()["role"] == "officer"
    case = ingest_case(db_session, "REV-API-001", "DKT-001")
    cid = str(case.id)
    assert officer.post(f"/cases/{cid}/review/start").status_code == 403
    assert officer.get("/admin/users").status_code == 403
    assert reviewer.patch(f"/admin/rules/slabs/{uuid.uuid4()}", json={}).status_code == 403

    # page viewer: masked JPEG by default; officers cannot fetch unmasked images
    from rci.db.models import Page

    seq = next(p.seq for p in db_session.scalars(select(Page).where(Page.case_id == case.id).order_by(Page.seq)) if p.pii_counts)
    r = officer.get(f"/cases/{cid}/pages/{seq}/image?w=600")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["x-rci-mask"] == "lines"
    assert officer.get(f"/cases/{cid}/pages/{seq}/image?masked=false").status_code == 403
    assert reviewer.get(f"/cases/{cid}/pages/{seq}/image?masked=false").headers["x-rci-mask"] == "none"
    ocr = reviewer.get(f"/cases/{cid}/pages/{seq}/ocr").json()
    assert ocr["aligned"] and ocr["lines"] and all(0 <= l["bbox"][0] <= 1 for l in ocr["lines"] if l["bbox"])
    assert any(l["pii"] for l in ocr["lines"])
    assert not any("[" not in (l.get("text") or "") and l["pii"] for l in ocr["lines"])

    # review over HTTP
    v = reviewer.post(f"/cases/{cid}/review/start").json()
    assert v["assignment"]["mode"] == "single" and v["rev"]
    r = reviewer.post(f"/cases/{cid}/review/ops", json={"ops": [{"op": "field.accept_all"}], "rev": v["rev"]})
    assert r.status_code == 200
    stale = reviewer.post(f"/cases/{cid}/review/ops", json={"ops": [{"op": "field.accept_all"}], "rev": v["rev"]})
    assert stale.status_code == 409
    bad = reviewer.post(f"/cases/{cid}/review/ops", json={"ops": [{"op": "nope"}]})
    assert bad.status_code == 422
    new_code = reviewer.post("/catalog/requirements", json={"code": "NA_ORDER_COPY", "name_en": "Copy of NA order",
                                                            "name_mr": "बिनशेती आदेशाची प्रत"})
    assert new_code.status_code == 201 and new_code.json()["status"] == "to_verify"

    audits = db_session.scalars(select(AuditLog).where(AuditLog.case_id == case.id)).all()
    actions = {(a.action, (a.details or {}).get("route")) for a in audits}
    assert ("http.get", "/cases/{case_id}/pages/{seq}/image") in actions
    assert ("auth.denied", "/cases/{case_id}/review/start") in actions
    assert ("http.post", "/cases/{case_id}/review/ops") in actions
    assert any(a.action == "review.ops" and a.actor_id is None and a.details["actor"] for a in audits)
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth.login_failed")) >= 1


def test_rules_admin_verification(api, db_session):
    admin, reviewer = api("admin"), api("reviewer1")
    sets = reviewer.get("/admin/rule-sets").json()
    base = next(r for r in sets if r["code"] == "RULES_2019")
    detail = admin.get(f"/admin/rule-sets/{base['id']}").json()
    slab = next(x for x in detail["slabs"] if x["seed_key"] == "RULES_2019.CHS.LEASE.RES")
    r = admin.patch(f"/admin/rules/slabs/{slab['id']}", json={"status": "verified"})
    assert r.status_code == 422 and "upload the notification" in r.json()["detail"]
    up = admin.post(f"/admin/rule-sets/{base['id']}/source", files={"file": ("base-2023.txt", "notification text".encode(), "text/plain")})
    assert up.status_code == 200 and up.json()["has_source"]
    r = admin.patch(f"/admin/rules/slabs/{slab['id']}", json={"status": "verified"})
    assert r.status_code == 422 and "source_ref" in r.json()["detail"] and "rate_pct" in r.json()["detail"]
    r = admin.patch(f"/admin/rules/slabs/{slab['id']}", json={"rate_pct": "150"})
    assert r.status_code == 422
    r = admin.patch(f"/admin/rules/slabs/{slab['id']}", json={"rate_pct": "12.5", "source_ref": "cl. 3(b), p.2", "status": "verified"})
    assert r.status_code == 200 and r.json()["status"] == "verified" and r.json()["verified_by"]
    res = reviewer.get("/admin/rules/resolve", params={"on": "2024-10-01", "holding_basis": "govt_lease"}).json()
    assert res["outcome"] == "unverified" and "rule set RULES_2019 is to_verify" in res["message"]
    r = admin.patch(f"/admin/rule-sets/{base['id']}", json={"status": "verified"})
    assert r.status_code == 200 and r.json()["status"] == "verified"
    res = reviewer.get("/admin/rules/resolve", params={"on": "2024-10-01", "holding_basis": "govt_lease"}).json()
    assert res["outcome"] == "resolved" and res["result"]["slab"]["rate_pct"] == "12.5000"
    # editing a verified row sends it back to to_verify
    r = admin.patch(f"/admin/rules/slabs/{slab['id']}", json={"rate_pct": "12"})
    assert r.json()["status"] == "to_verify"
    assert admin.delete(f"/admin/rules/slabs/{slab['id']}").status_code == 409  # seeded rows are not deletable
    gaps = reviewer.get("/admin/rules/gaps", params={"on": "2024-10-01"}).json()
    assert gaps["active_chs_slabs_unverified"] >= 1
    # restore the shared test DB
    admin.patch(f"/admin/rule-sets/{base['id']}", json={"status": "to_verify"})
    rs = db_session.scalar(select(RuleSet).where(RuleSet.code == "RULES_2019"))
    db_session.refresh(rs)
    assert rs.status == "to_verify"
    rs.source_blob_key = rs.source_filename = rs.updated_by = rs.updated_at = rs.verified_by = rs.verified_at = None
    obj = db_session.get(RulePremiumSlab, slab["id"])
    obj.rate_pct = obj.source_ref = obj.updated_by = obj.updated_at = obj.verified_by = obj.verified_at = None
    obj.status = "to_verify"
    db_session.commit()


def test_asr_import_api(api):
    from conftest import SAMPLES

    data = (SAMPLES / "asr" / "asr_synthetic_pune.csv").read_bytes()
    admin, officer = api("admin"), api("officer")
    assert officer.post("/admin/asr/import", files={"file": ("a.csv", data, "text/csv")}).status_code == 403
    dry = admin.post("/admin/asr/import", files={"file": ("a.csv", data, "text/csv")}).json()
    assert dry["ok"] and dry["dry_run"] and dry["would_insert"] + dry["would_replace"] == 6
    bad = admin.post("/admin/asr/import", files={"file": ("b.csv", b"year,district\n2024,x\n", "text/csv")}).json()
    assert not bad["ok"] and bad["error_count"] >= 1


def test_vault_guard(db_session, users):
    from rci.db.models import Page
    from rci.pipeline.redact.vault import TOKEN_RE
    from rci.vault_access import VaultAccessDenied, render_with_pii

    case = ingest_case(db_session, "REV-VAULT-003", "DKT-003")
    pages = db_session.scalars(select(Page).where(Page.case_id == case.id).order_by(Page.seq)).all()
    text = next(p.text_redacted for p in pages if TOKEN_RE.search(p.text_redacted))
    with pytest.raises(VaultAccessDenied):
        render_with_pii(db_session, case.id, text, users["reviewer1"], purpose="draft_render", draft_ref="t")
    with pytest.raises(VaultAccessDenied):
        render_with_pii(db_session, case.id, text, users["officer"], purpose="review", draft_ref="t")
    out = render_with_pii(db_session, case.id, text, users["officer"], purpose="draft_render", draft_ref="draft-1")
    assert not TOKEN_RE.search(out) and out != text
    acts = [a.action for a in db_session.scalars(select(AuditLog).where(AuditLog.case_id == case.id, AuditLog.action.like("vault.%")))]
    assert acts.count("vault.denied") == 2 and acts.count("vault.detokenize") == 1
