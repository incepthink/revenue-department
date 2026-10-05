"""Anchors of the intake report on the sample dockets: every quoted evidence item is boxed on its page, and the boxes
cover the quoted words (Part 1 of the on-PDF viewer). Runs the intake with a fake AI reader that quotes the pages."""
import dataclasses
import re
import uuid

import pytest
from sqlalchemy import select

from rci.adapters.llm import FakeLLMClient
from rci.db.models import Page, PipelineStep
from rci.intake.anchors import ANCHORS_VERSION
from rci.intake.service import run_intake
from rci.pipeline.redact.vault import TOKEN_RE
from rci.pipeline.runner import PipelineContext
from rci.review.anchors import key
from rci.review.pageview import load_ocr
from rci.storage import get_blob_store
from test_intake_db import pages_of, upload

pytestmark = pytest.mark.db

PAGE_RE = re.compile(r"=== page_seq (\d+) ===\n(.*?)(?=\n\n=== page_seq |\Z)", re.S)


def _lines(user: str) -> list[tuple[int, str]]:
    """(page_seq, line) of the prompt's pages: lines without PII tokens, long enough to quote."""
    return [(int(seq), ln.strip()) for seq, body in PAGE_RE.findall(user) for ln in body.split("\n")
            if len(ln.strip()) >= 8 and not TOKEN_RE.search(ln)]


def _part(line: str) -> str:
    """Some words from the middle of a line."""
    ws = line.split()
    return " ".join(ws[1:-1]) if len(ws) >= 4 else line


def responder(task: str, user: str, schema: dict) -> dict:
    if task == "intake_identify":
        return {"slot": "application", "detected_as": "Application", "reason": "test", "confidence": 0.9, "bundled": []}
    if not task.startswith("intake_"):
        return {}  # the case card's own extraction / classification: an invalid answer, so it keeps to the rules
    lines = _lines(user)
    props = schema["properties"]
    # fields: a whole line or part of one (kept only when it parses as the field's kind)
    fields = {}
    for i, name in enumerate(props["fields"]["required"]):
        seq, ln = lines[i % len(lines)]
        fields[name] = {"found": True, "raw": ln if i % 2 else _part(ln), "page_seq": seq}
    out = {"document_matches": True, "detected_as": "", "match_reason": "", "fields": fields,
           "observations": [{"severity": "warning", "text": "The seal is faint.", "page_seq": lines[0][0]}]}
    if "summary" in props:
        out.update(summary="A supporting document.", relevance="supporting")
    if "satisfies_requirement" in props:
        out["satisfies_requirement"] = None
    if "rule_results" in props:
        codes = props["rule_results"]["items"]["properties"]["rule_code"]["enum"]
        out["rule_results"] = []
        for i, code in enumerate(codes):
            seq, ln = lines[(i * 3 + 1) % len(lines)]
            out["rule_results"].append({"rule_code": code, "verdict": "not_met" if i % 2 else "met",
                                        "quote": _part(ln) if i % 2 else ln, "page_seq": seq, "reason": "test"})
    return out


def _run(client, db_url, case_ref: str, named: list[tuple[str, str, bytes]]) -> str:
    case_id = client.post("/cases", json={"case_ref": case_ref, "data_class": "synthetic"}).json()["id"]
    assert upload(client, case_id, named, process="false").status_code == 201
    ctx = dataclasses.replace(PipelineContext.default(), llm=FakeLLMClient(responder=responder))
    assert run_intake(uuid.UUID(case_id), ctx=ctx, url=db_url) is not None
    return case_id


def _covered(ocr: dict, boxes: list[list[float]]) -> str:
    """key() of the blob's words whose centre lies inside one of the boxes, in reading order."""
    out = []
    for line in ocr["lines"]:
        for w in line.get("words") or []:
            x0, y0, x1, y1 = w["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes):
                out.append(w["text"])
    return key("".join(out))


def _check_anchors(db_session, case_id: str, report: dict) -> None:
    pages = {p.seq: p for p in db_session.scalars(select(Page).where(Page.case_id == uuid.UUID(case_id)))}
    blobs = get_blob_store()
    assert report["anchors_version"] == ANCHORS_VERSION
    quoted = 0
    for f in report["findings"]:
        assert f["id"] and len(f["anchors"]) == len(f["evidence"])
        for ev, a in zip(f["evidence"], f["anchors"]):
            if ev.get("raw") and ev.get("page_seq"):
                quoted += 1
                assert a["kind"] == "highlight" and a["boxes"], (f["code"], ev, a)
                assert a["page_seq"] == ev["page_seq"] and a["document_id"] == ev["document_id"]
                covered = _covered(load_ocr(blobs, pages[a["page_seq"]].ocr_blob_key), a["boxes"])
                for piece in TOKEN_RE.split(ev["raw"]):  # a PII token is not in the words; what is around it is
                    if key(piece) and not TOKEN_RE.fullmatch(piece):
                        assert key(piece) in covered, (f["code"], ev["raw"], covered)
            elif ev.get("page_seq"):
                assert a["kind"] == "page" and a["reason"] == "no_quote"
            else:
                assert a["kind"] == "missing"
    assert quoted > 0
    for fact in report["facts"]:
        assert fact["anchor"]["kind"] == "highlight" and fact["anchor"]["boxes"]


def test_dkt002_anchors(api, db_session, db_url):
    client = api("officer")
    case_id = _run(client, db_url, "ANCHOR-002", [("application", "arj.pdf", pages_of("01_arj.pdf", 0)),
                                                  ("grant", "lease.pdf", pages_of("01_arj.pdf", 1, 2)),
                                                  ("property_card", "card.pdf", pages_of("02_kagadpatre.pdf", 0))])
    view = client.get(f"/cases/{case_id}/intake").json()
    report = view["report"]
    _check_anchors(db_session, case_id, report)
    by = {}
    for f in report["findings"]:
        by.setdefault(f["code"], []).append(f)

    assert all(f["anchors"] == [] for f in by["DOC_MISSING"])
    cts = by["LAND_CTS_MATCH"][0]  # 567 on the application, the lease and the property card: one anchor per document
    assert len({a["document_id"] for a in cts["anchors"]}) == len(cts["evidence"]) >= 2
    assert all(a["kind"] == "highlight" and a["precision"] == "word" for a in cts["anchors"])
    observations = [f for f in report["findings"] if f["source"] == "ai"]
    assert observations and all(a["kind"] == "page" and a["reason"] == "no_quote" for f in observations for a in f["anchors"])
    assert any(f["source"] == "rule" and f["anchors"] and f["anchors"][0]["kind"] == "highlight" for f in report["findings"])

    # the page list carries the page size, and the page OCR view no word geometry (un-redacted text)
    doc = client.get(f"/cases/{case_id}").json()["documents"][0]
    assert all(p["width_px"] > 0 and p["height_px"] > 0 for p in doc["page_list"])
    ocr = client.get(f"/cases/{case_id}/pages/{doc['page_list'][0]['seq']}/ocr").json()
    assert ocr["lines"] and all("words" not in l for l in ocr["lines"])

    # a report stored before anchors existed is anchored when read, and the stored row is left as it was
    row = db_session.scalar(select(PipelineStep).where(PipelineStep.case_id == uuid.UUID(case_id), PipelineStep.scope == "case",
                                                       PipelineStep.step == "intake"))
    old = {k: v for k, v in row.output.items() if k != "anchors_version"}
    old["findings"] = [{k: v for k, v in f.items() if k not in ("anchors", "id")} for f in old["findings"]]
    old["facts"] = [{k: v for k, v in f.items() if k != "anchor"} for f in old["facts"]]
    row.output = old
    db_session.commit()
    again = client.get(f"/cases/{case_id}/intake").json()["report"]
    assert again["anchors_version"] == ANCHORS_VERSION
    assert [f["anchors"] for f in again["findings"]] == [f["anchors"] for f in report["findings"]]
    db_session.expire_all()
    stored = db_session.get(PipelineStep, row.id).output
    assert "anchors_version" not in stored and all("anchors" not in f for f in stored["findings"])


@pytest.mark.parametrize("docket", ["DKT-001", "DKT-003"])
def test_single_file_docket_anchors(api, db_session, db_url, docket):
    from conftest import sample_files

    client = api("officer")
    named = [("auto", name, data) for name, data in sample_files(docket)]
    case_id = _run(client, db_url, f"ANCHOR-{docket}", named)
    _check_anchors(db_session, case_id, client.get(f"/cases/{case_id}/intake").json()["report"])
