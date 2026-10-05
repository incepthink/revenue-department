"""Check ingested sample dockets against ground truth (eval/samples/expected/*.json).

Checks, per docket:
  exhibits      type / stage / page range match exactly (boundaries)
  facts         selected card facts match, and every card fact carries page-referenced evidence
  validations   expected status per validator
  requirements  expected draft status per requirement code
  events        expected event codes in timeline order
  redaction     none of the docket's PII values survive in redacted text, and a generic scan finds no
                Verhoeff-valid Aadhaar / VID and no mobile number
  idempotency   (--rerun) re-ingesting creates no rows, no new card version, identical card hash

    python eval/check_ingest.py [--rerun] [--json report.json]
Exit code 0 only if everything passes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from sqlalchemy import func, select

from rci.db.models import (
    AuditLog, Case, CaseCard, CaseEvent, CaseRequirement, Document, Exhibit, ExhibitPage, Office, Page, PiiToken,
)
from rci.db.session import session_scope
from rci.pipeline.normalise import ascii_digits
from rci.pipeline.redact import verhoeff
from rci.pipeline.redact.vault import TOKEN_RE

HERE = Path(__file__).resolve().parent
EXPECTED = HERE / "samples" / "expected"
DOCKETS = HERE / "samples" / "dockets"

AADHAAR_ANY = re.compile(r"(?<!\d)(\d{4}[ \-]?\d{4}[ \-]?\d{4}(?:[ \-]?\d{4})?)(?!\d)")
MOBILE_ANY = re.compile(r"(?<![\dA-Za-z])(?:\+91[ \-]?|0)?[6-9]\d{4}[ \-]?\d{5}(?![\dA-Za-z])")


def fact_value(facts: dict, path: str):
    parts = path.split(".")
    cur = facts
    for i, p in enumerate(parts):
        if p == "count":
            return len(cur)
        if isinstance(cur, list):
            cur = cur[int(p)] if int(p) < len(cur) else None
        else:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur.get("value") if isinstance(cur, dict) and "value" in cur else cur


def iter_facts(obj):
    if isinstance(obj, dict):
        if "value" in obj and "page_seq" in obj:
            yield obj
            return
        for v in obj.values():
            yield from iter_facts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_facts(v)


def squash(s: str) -> str:
    return re.sub(r"[\s\-]", "", ascii_digits(s))


def check_redaction(pages: list[Page], pii: list[str]) -> list[str]:
    errors = []
    for p in pages:
        for label, txt in (("text", p.text_redacted or ""), ("ascii", p.text_redacted_ascii or "")):
            stripped = TOKEN_RE.sub(" ", txt)
            sq = squash(stripped)
            for v in pii:
                if v in stripped or (re.fullmatch(r"[\d\s+\-]+", v) and squash(v)[-10:] in sq):
                    errors.append(f"page {p.seq}: PII value {v!r} survived ({label})")
            asc = ascii_digits(stripped)
            for m in AADHAAR_ANY.finditer(asc):
                if verhoeff.is_valid(m.group(1)):
                    errors.append(f"page {p.seq}: Verhoeff-valid Aadhaar/VID-like number {m.group(1)!r}")
            for m in MOBILE_ANY.finditer(asc):
                errors.append(f"page {p.seq}: mobile-like number {m.group(0)!r}")
    return sorted(set(errors))


def check_case(s, exp: dict) -> dict:
    office = s.scalar(select(Office).where(Office.code == "PILOT"))
    case = s.scalar(select(Case).where(Case.office_id == office.id, Case.case_ref == exp["case_ref"]))
    res = {"case_ref": exp["case_ref"], "errors": {}}
    if case is None:
        res["errors"]["case"] = ["not ingested"]
        return res
    card_row = s.scalar(select(CaseCard).where(CaseCard.case_id == case.id).order_by(CaseCard.version.desc()).limit(1))
    card = card_row.card
    pages = s.scalars(select(Page).where(Page.case_id == case.id).order_by(Page.seq)).all()

    got_ex = [{"type": e["type"], "stage": e["stage"], "pages": [e["pages"][0], e["pages"][-1]]} for e in card["exhibits"]]
    want_ex = [{"type": e["type"], "stage": e["stage"], "pages": e["pages"]} for e in exp["exhibits"]]
    ex_err = []
    if len(pages) != exp["page_count"]:
        ex_err.append(f"page count {len(pages)} != {exp['page_count']}")
    for i in range(max(len(got_ex), len(want_ex))):
        g = got_ex[i] if i < len(got_ex) else None
        w = want_ex[i] if i < len(want_ex) else None
        if g != w:
            ex_err.append(f"#{i + 1}: got {g} want {w}")
    res["errors"]["exhibits"] = ex_err

    f_err = []
    for path, want in exp["facts"].items():
        got = fact_value(card["facts"], path)
        if got != want:
            f_err.append(f"{path}: got {got!r} want {want!r}")
    for f in iter_facts(card["facts"]):
        if not f.get("page_seq") or not f.get("snippet") or f.get("raw") is None:
            f_err.append(f"fact {f.get('exhibit_type')}.{f.get('field')} lacks page evidence")
        elif not (1 <= f["page_seq"] <= len(pages)):
            f_err.append(f"fact {f.get('field')} cites page {f['page_seq']} outside docket")
    res["errors"]["facts"] = f_err

    got_v = {v["code"]: v["status"] for v in card["validations"]}
    res["errors"]["validations"] = [f"{k}: got {got_v.get(k)} want {w}" for k, w in exp["validations"].items() if got_v.get(k) != w]
    got_r = {r["code"]: r["status"] for r in card["requirements"]}
    res["errors"]["requirements"] = [f"{k}: got {got_r.get(k)} want {w}" for k, w in exp["requirements"].items() if got_r.get(k) != w]
    got_e = [e["code"] for e in card["events"]]
    res["errors"]["events"] = [] if got_e == exp["events"] else [f"got {got_e} want {exp['events']}"]
    res["errors"]["redaction"] = check_redaction(pages, exp["pii_values"])
    res["card_version"] = card_row.version
    res["pii_counts"] = {k: sum((p.pii_counts or {}).get(k, 0) for p in pages)
                         for k in sorted({k for p in pages for k in (p.pii_counts or {})})}
    return res


def row_counts(s) -> dict[str, int]:
    return {m.__tablename__: s.scalar(select(func.count()).select_from(m))
            for m in (Case, Document, Page, Exhibit, ExhibitPage, CaseCard, CaseRequirement, CaseEvent, PiiToken)}


def card_hashes(s) -> dict[str, str]:
    rows = s.execute(select(Case.case_ref, CaseCard.version, CaseCard.content_hash).join(CaseCard, CaseCard.case_id == Case.id)).all()
    return {f"{r[0]}@v{r[1]}": r[2] for r in rows}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun", action="store_true", help="re-ingest the samples and verify nothing changes")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    report = {"cases": [], "idempotency": None}
    with session_scope() as s:
        before, hashes_before = row_counts(s), card_hashes(s)
        audit_before = s.scalar(select(func.count()).select_from(AuditLog))
    if args.rerun:
        from rci.cli import main as rci_main

        rci_main(["ingest", str(DOCKETS), "--data-class", "synthetic"])
        with session_scope() as s:
            after, hashes_after = row_counts(s), card_hashes(s)
        diffs = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        report["idempotency"] = {"row_count_changes": diffs, "card_hashes_changed": hashes_before != hashes_after,
                                 "audit_rows_before": audit_before}
    with session_scope() as s:
        for f in sorted(EXPECTED.glob("*.json")):
            report["cases"].append(check_case(s, json.loads(f.read_text(encoding="utf-8"))))

    ok = True
    for c in report["cases"]:
        n = sum(len(v) for v in c["errors"].values())
        ok &= n == 0
        print(f"{c['case_ref']}: {'PASS' if n == 0 else f'FAIL ({n})'}  card v{c.get('card_version')}  pii={c.get('pii_counts')}")
        for section, errs in c["errors"].items():
            for e in errs:
                print(f"   [{section}] {e}")
    if report["idempotency"] is not None:
        idem = report["idempotency"]
        idem_ok = not idem["row_count_changes"] and not idem["card_hashes_changed"]
        ok &= idem_ok
        print(f"idempotency: {'PASS' if idem_ok else 'FAIL'} {idem}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
