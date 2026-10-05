"""Review workflow (DB side): assignments, the review view, ops, submit, approve, reports.

Assignment rules
  * A review round starts from the latest card. Round 1 starts from the machine draft; an admin can reopen an
    approved case (round n+1, always single-review, no correction logging since the base is human-approved).
  * The first `double_review_first_n` cases (parameters.yaml) are double-reviewed: two reviewers work
    independently, each submits, and only then can one of them approve. Disagreements become visible to
    both after both have submitted. Later cases are single-reviewed.
Approval (one transaction, advisory-locked per case like the pipeline)
  new locked card version (status approved) → exhibits materialised → case_requirements + case_events →
  templated summary → facet embeddings (facts / documents / issues) → extraction corrections → audit.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid

import jsonschema
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rci.adapters.embeddings import get_embedding_client
from rci.adapters.llm import get_llm_client
from rci.auth import CurrentUser
from rci.config import Parameters, get_settings
from rci.db.models import (
    Case,
    CaseCard,
    CaseEmbedding,
    CaseEvent,
    CaseRequirement,
    Document,
    EvalRun,
    EventCatalog,
    Exhibit,
    ExhibitPage,
    ExhibitType,
    ExtractionCorrection,
    Office,
    Page,
    RequirementCatalog,
    ReviewAssignment,
    User,
)
from rci.pipeline.card import card_schema, content_hash, suggest_codes
from rci.pipeline.extract.fields import FIELDS
from rci.pipeline.runner import audit, latest_card
from rci.review.compare import accuracy_report, aggregate, agreement, comparable, corrections
from rci.review.state import (
    STAGES,
    Derived,
    PageInfo,
    ReviewContext,
    ReviewError,
    apply_ops,
    blockers,
    derive,
    initial_state,
    qkey,
)
from rci.review.summary import facet_texts, summary

FACETS = ("facts", "documents", "issues")


class Conflict(Exception):
    """Workflow state does not allow the action (→ HTTP 409)."""


# ------------------------------------------------------------------------------------------------
# Context
# ------------------------------------------------------------------------------------------------
def _catalogs(session: Session) -> tuple[dict[str, dict], list[dict], list[dict]]:
    types = {t.code: {"code": t.code, "name_en": t.name_en, "name_mr": t.name_mr, "party": t.party, "default_stage": t.default_stage}
             for t in session.scalars(select(ExhibitType).where(ExhibitType.active).order_by(ExhibitType.code))}
    reqs = [{"code": r.code, "name_en": r.name_en, "name_mr": r.name_mr, "category": r.category, "satisfied_by": r.satisfied_by,
             "query_keywords": r.query_keywords, "applies_when": r.applies_when, "status": r.status}
            for r in session.scalars(select(RequirementCatalog).where(RequirementCatalog.active).order_by(RequirementCatalog.code))]
    events = [{"code": e.code, "name_en": e.name_en, "name_mr": e.name_mr, "source_exhibit_types": e.source_exhibit_types,
               "date_field": e.date_field, "typical_order": e.typical_order}
              for e in session.scalars(select(EventCatalog).where(EventCatalog.active).order_by(EventCatalog.typical_order))]
    return types, reqs, events


def build_context(session: Session, case: Case, card: CaseCard | None = None, *, with_llm: bool = True) -> ReviewContext:
    types, reqs, events = _catalogs(session)
    rows = session.execute(
        select(Page, Document.filename).join(Document, Page.document_id == Document.id).where(Page.case_id == case.id)
    ).all()
    pages = {p.seq: PageInfo(p.id, p.seq, p.page_no, fn, p.text_redacted or "", p.text_redacted_ascii or "") for p, fn in rows}
    card = card or latest_card(session, case.id)
    office = session.get(Office, case.office_id)
    case_block = (card.card["case"] if card else
                  {"case_id": str(case.id), "case_ref": case.case_ref, "office_code": office.code, "data_class": case.data_class,
                   "case_type": case.case_type})
    s = get_settings()
    return ReviewContext(case_id=case.id, case=case_block, data_class=case.data_class, pages=pages, exhibit_types=types,
                         requirement_catalog=reqs, event_catalog=events, params=Parameters.load(s.parameters_file),
                         llm=get_llm_client(s) if with_llm else None,
                         pipeline_version=card.card.get("pipeline_version", "p1.0") if card else "p1.0")


# ------------------------------------------------------------------------------------------------
# Assignments
# ------------------------------------------------------------------------------------------------
def _round(session: Session, case_id: uuid.UUID) -> int:
    return session.scalar(select(func.max(ReviewAssignment.round)).where(ReviewAssignment.case_id == case_id)) or 1


def _assignments(session: Session, case_id: uuid.UUID, rnd: int) -> list[ReviewAssignment]:
    return list(session.scalars(select(ReviewAssignment).where(ReviewAssignment.case_id == case_id, ReviewAssignment.round == rnd)
                                .order_by(ReviewAssignment.created_at)))


def _mine(session: Session, case_id: uuid.UUID, user: CurrentUser) -> ReviewAssignment | None:
    return session.scalar(select(ReviewAssignment).where(
        ReviewAssignment.case_id == case_id, ReviewAssignment.round == _round(session, case_id),
        ReviewAssignment.reviewer_id == user.id))


def double_review_quota(session: Session) -> tuple[int, int]:
    """(cases already in double review, N from parameters)."""
    n = int(Parameters.load(get_settings().parameters_file).value("double_review_first_n"))
    used = session.scalar(select(func.count(func.distinct(ReviewAssignment.case_id))).where(ReviewAssignment.mode == "double"))
    return used or 0, n


def _extractions(session: Session, case_id: uuid.UUID) -> dict[str, dict]:
    return {str(e.id): (e.extraction or {}) for e in session.scalars(select(Exhibit).where(Exhibit.case_id == case_id))}


def _lock(session: Session, case_id: uuid.UUID) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"case:{case_id}"})


def start_review(session: Session, case: Case, user: CurrentUser, *, new_round: bool = False) -> ReviewAssignment:
    _lock(session, case.id)
    card = latest_card(session, case.id)
    if card is None:
        raise Conflict("case has no card yet — ingest it first")
    rnd = _round(session, case.id)
    existing = _assignments(session, case.id, rnd)
    if card.status == "approved":
        if not new_round:
            raise Conflict("card is approved; an admin can reopen it for a new review round")
        if not user.has("admin"):
            raise PermissionError("only an admin can reopen an approved card")
        rnd, existing = (rnd + 1 if existing else rnd), []
    mine = next((a for a in existing if a.reviewer_id == user.id), None)
    if mine:
        return mine
    if not existing:
        used, n = double_review_quota(session)
        mode = "double" if rnd == 1 and used < n else "single"
    else:
        mode = existing[0].mode
        if mode == "single":
            who = session.get(User, existing[0].reviewer_id)
            raise Conflict(f"already under review by {who.display_name} (single-review mode)")
        if len(existing) >= 2:
            raise Conflict("this case already has two reviewers")
    state = initial_state(card.card, str(card.id), card.version, card.status, _extractions(session, case.id))
    a = ReviewAssignment(case_id=case.id, reviewer_id=user.id, mode=mode, round=rnd, base_card_id=card.id, status="assigned",
                         working=state)
    session.add(a)
    session.flush()
    audit(session, "review.start", actor_kind="user", case_id=case.id, entity_type="review_assignment", entity_id=a.id,
          actor=str(user.id), mode=mode, round=rnd, base_card_version=card.version)
    return a


# ------------------------------------------------------------------------------------------------
# View
# ------------------------------------------------------------------------------------------------
def _field_rows(e: dict, decisions: dict, reviewed: dict) -> list[dict]:
    rows = []
    for spec in FIELDS.get(e["type"], []):
        draft = e["extraction"]["fields"].get(spec.name)
        conf = None
        if isinstance(draft, dict):
            conf = draft.get("confidence")
        elif isinstance(draft, list) and draft:
            conf = min(x.get("confidence", 0) for x in draft)
        rows.append({"name": spec.name, "kind": spec.kind, "enum": list(spec.enum), "description": spec.description,
                     "draft": draft, "confidence": conf, "decision": decisions.get(spec.name), "current": reviewed.get(spec.name)})
    return rows


def _assignment_json(session: Session, a: ReviewAssignment) -> dict:
    u = session.get(User, a.reviewer_id)
    return {"id": str(a.id), "reviewer": {"id": str(u.id), "display_name": u.display_name, "email": u.email},
            "mode": a.mode, "round": a.round, "status": a.status, "submitted_at": a.submitted_at,
            "base_card_version": (a.working or {}).get("base_card_version")}


def view(session: Session, case: Case, user: CurrentUser) -> dict:
    card = latest_card(session, case.id)
    rnd = _round(session, case.id)
    all_a = _assignments(session, case.id, rnd)
    mine = next((a for a in all_a if a.reviewer_id == user.id), None)
    shown = mine
    read_only = mine is None or mine.status in ("approved", "closed")
    if shown is None and card is not None and card.status == "approved":
        shown = next((a for a in all_a if a.card_id == card.id), None)
    used, n = double_review_quota(session)
    base = {
        "case": {"id": str(case.id), "case_ref": case.case_ref, "status": case.status, "data_class": case.data_class,
                 "society_name": case.society_name},
        "card": {"id": str(card.id), "version": card.version, "status": card.status, "locked": card.locked} if card else None,
        "assignment": _assignment_json(session, shown) if shown else None,
        "assignments": [{**_assignment_json(session, a), "mine": a.reviewer_id == user.id} for a in all_a],
        "double_review": {"used": used, "first_n": n},
        "read_only": read_only,
        "can_start": card is not None and mine is None and card.status != "approved",
        "can_reopen": card is not None and card.status == "approved" and user.has("admin"),
    }
    if shown is None:
        return base
    ctx = build_context(session, case, with_llm=False)
    state = shown.working
    d = derive(state, ctx)
    types = ctx.exhibit_types
    exhibits = []
    for n_, e in enumerate(state["exhibits"], start=1):
        decisions = state["decisions"].get(e["exhibit_id"], {})
        exhibits.append({
            "exhibit_id": e["exhibit_id"], "seq": n_, "type": e["type"], "type_name_mr": types.get(e["type"], {}).get("name_mr"),
            "stage": e["stage"], "start": e["start"], "end": e["end"], "confidence": e["confidence"], "method": e["method"],
            "origin": e["origin"], "fields": _field_rows(e, decisions, d.fields[e["exhibit_id"]]),
            "undecided": sum(1 for x, _ in d.undecided if x == e["exhibit_id"]),
        })
    queries = []
    for q in d.card["facts"]["queries"]:
        items = []
        for i, it in enumerate(q["items"]):
            items.append({"index": i, "value": it.get("value"), "raw": it.get("raw"), "page_seq": it.get("page_seq"),
                          "snippet": it.get("snippet"), "suggested_codes": suggest_codes(it.get("value"), ctx.requirement_catalog),
                          "mapping": state["query_map"].get(qkey(q["exhibit_id"], i))})
        queries.append({"exhibit_id": q["exhibit_id"], "letter_date": (q.get("letter_date") or {}).get("value"),
                        "ref_no": (q.get("ref_no") or {}).get("value"), "items": items})
    derived_status = {r["code"]: r["status"] for r in d.derived_requirements}
    cat = {r["code"]: r for r in ctx.requirement_catalog}
    requirements = [{**r, "name_en": cat[r["code"]]["name_en"], "name_mr": cat[r["code"]]["name_mr"],
                     "category": cat[r["code"]]["category"], "derived_status": derived_status.get(r["code"]),
                     "override": state["requirements"].get(r["code"])} for r in d.requirements]
    disagreements = None
    if mine and mine.mode == "double" and mine.submitted:
        others = [a for a in all_a if a.id != mine.id and a.submitted]
        if others:
            disagreements = agreement(mine.submitted["comparable"], others[0].submitted["comparable"])
    block = blockers(state, d)
    if mine and mine.mode == "double":
        submitted = [a for a in all_a if a.submitted_at]
        if len({a.reviewer_id for a in submitted}) < 2:
            block.append("double review: both reviewers must submit before approval")
    return {
        **base,
        "pages": [{"seq": p.seq, "document": p.document, "page_no": p.page_no} for p in sorted(ctx.pages.values(), key=lambda p: p.seq)],
        "exhibits": exhibits, "queries": queries, "requirements": requirements,
        "events": d.events, "events_overridden": state["events"] is not None, "validations": d.validations,
        "progress": {"fields_total": sum(len(e["fields"]) for e in exhibits), "fields_undecided": len(d.undecided),
                     "query_items_total": sum(len(q["items"]) for q in queries), "query_items_unmapped": len(d.unmapped)},
        "rev": state_hash(state), "blockers": block, "can_approve": not read_only and not block,
        "can_submit": not read_only and mine is not None and mine.mode == "double" and not blockers(state, d),
        "disagreements": disagreements,
        "catalogs": {
            "exhibit_types": [{"code": t["code"], "name_en": t["name_en"], "name_mr": t["name_mr"]} for t in types.values()],
            "requirements": [{"code": r["code"], "name_en": r["name_en"], "name_mr": r["name_mr"], "category": r["category"],
                              "status": r["status"]} for r in ctx.requirement_catalog],
            "events": [{"code": e["code"], "name_en": e["name_en"], "name_mr": e["name_mr"]} for e in ctx.event_catalog],
            "stages": list(STAGES),
        },
    }


# ------------------------------------------------------------------------------------------------
# Mutations
# ------------------------------------------------------------------------------------------------
def _editable(session: Session, case: Case, user: CurrentUser) -> ReviewAssignment:
    a = _mine(session, case.id, user)
    if a is None:
        raise Conflict("start a review of this case first")
    if a.status in ("approved", "closed"):
        raise Conflict(f"review is {a.status}")
    return a


def apply(session: Session, case: Case, user: CurrentUser, ops: list[dict], rev: str | None = None) -> None:
    """`rev` (from the last view) guards against lost updates when the same review is open twice."""
    a = _editable(session, case, user)
    if rev and rev != state_hash(a.working):
        raise Conflict("this review changed in another window — reload")
    ctx = build_context(session, case)
    a.working = apply_ops(a.working, ctx, ops)
    a.updated_at = dt.datetime.now(dt.timezone.utc)
    audit(session, "review.ops", actor_kind="user", case_id=case.id, entity_type="review_assignment", entity_id=a.id,
          actor=str(user.id), ops=[{k: v for k, v in op.items() if k in ("op", "exhibit_id", "field", "action", "code", "index")}
                                   for op in ops])


def submit(session: Session, case: Case, user: CurrentUser) -> None:
    a = _editable(session, case, user)
    ctx = build_context(session, case, with_llm=False)
    d = derive(a.working, ctx)
    b = blockers(a.working, d)
    if b:
        raise Conflict("; ".join(b))
    now = dt.datetime.now(dt.timezone.utc)
    a.submitted = {"working": a.working, "comparable": comparable(a.working, d.fields, d.requirements, d.events),
                   "submitted_at": now.isoformat()}
    a.status, a.submitted_at = "submitted", now
    audit(session, "review.submit", actor_kind="user", case_id=case.id, entity_type="review_assignment", entity_id=a.id,
          actor=str(user.id))


def _as_of(card: dict) -> dt.date | None:
    dates = [e["date"] for e in card["events"] if e.get("date")]
    order = ((card["facts"].get("order") or {}).get("order_date") or {}).get("value")
    app = ((card["facts"].get("application") or {}).get("date") or {}).get("value")
    pick = order or (max(dates) if dates else None) or app
    return dt.date.fromisoformat(pick) if pick else None


def _materialise_exhibits(session: Session, case: Case, state: dict, d: Derived, ctx: ReviewContext) -> None:
    existing = {e.id: e for e in session.scalars(select(Exhibit).where(Exhibit.case_id == case.id))}
    keep: set[uuid.UUID] = set()
    for n, (e, ce) in enumerate(zip(state["exhibits"], d.exhibits), start=1):
        eid = uuid.UUID(e["exhibit_id"])
        keep.add(eid)
        ex = existing.get(eid) or Exhibit(id=eid, case_id=case.id, origin="auto")
        ex.seq, ex.exhibit_type, ex.stage = n, e["type"], e["stage"]
        ex.start_page_seq, ex.end_page_seq = e["start"], e["end"]
        ex.confidence, ex.classification_method = e["confidence"], e["method"]
        if e["origin"] == "manual":
            ex.origin = "manual"
        ex.status, ex.doc_date = "approved", ce.doc_date
        ex.extraction = {"fields": d.fields[e["exhibit_id"]], "draft_fields": e["extraction"]["fields"],
                         "methods": e["extraction"].get("methods", []), "unverified": e["extraction"].get("unverified", []),
                         "reviewed": True}
        if eid not in existing:
            session.add(ex)
            session.flush()
        page_ids = [ctx.pages[s].id for s in range(e["start"], e["end"] + 1)]
        if [ep.page_id for ep in ex.pages] != page_ids:
            session.execute(text("DELETE FROM exhibit_pages WHERE exhibit_id = :e"), {"e": eid})
            session.expire(ex, ["pages"])
            for i, pid in enumerate(page_ids):
                session.add(ExhibitPage(exhibit_id=eid, page_id=pid, position=i))
    for eid, ex in existing.items():
        if eid not in keep:
            session.delete(ex)
    session.flush()


def approve(session: Session, case: Case, user: CurrentUser) -> CaseCard:
    _lock(session, case.id)
    a = _editable(session, case, user)
    if a.mode == "double":
        submitted = [x for x in _assignments(session, case.id, a.round) if x.submitted_at]
        if len({x.reviewer_id for x in submitted}) < 2:
            raise Conflict("double review: both reviewers must submit before approval")
    ctx = build_context(session, case, with_llm=False)
    state = a.working
    d = derive(state, ctx)
    b = blockers(state, d)
    if b:
        raise Conflict("; ".join(b))
    base = session.get(CaseCard, a.base_card_id)
    latest = latest_card(session, case.id)
    now = dt.datetime.now(dt.timezone.utc)
    reviewers = sorted({session.get(User, x.reviewer_id).email for x in _assignments(session, case.id, a.round) if x.submitted_at}
                       | {user.email})
    card = d.card
    card["status"] = "approved"
    card["summary"] = summary(card)
    card["approval"] = {"approved_by": user.email, "approved_at": now.isoformat(), "review_mode": a.mode, "review_round": a.round,
                        "base_card_version": base.version, "reviewers": reviewers}
    card["review"]["open_issues"] = [i for i in card["review"]["open_issues"] if i["kind"] in ("validation_failed", "unverified_parameter")]
    jsonschema.validate(card, card_schema())

    _materialise_exhibits(session, case, state, d, ctx)
    for old in session.scalars(select(CaseCard).where(CaseCard.case_id == case.id, CaseCard.status.in_(("draft", "approved")))):
        old.status = "superseded"
    new = CaseCard(case_id=case.id, version=latest.version + 1, card=card, content_hash=content_hash(card), status="approved",
                   locked=True, created_by=user.id, approved_by=user.id, approved_at=now, parent_card_id=base.id)
    session.add(new)
    session.flush()
    for r in d.requirements:
        session.add(CaseRequirement(
            case_id=case.id, card_id=new.id, requirement_code=r["code"], status=r["status"],
            satisfied_by_exhibit_id=uuid.UUID(r["satisfied_by_exhibit_id"]) if r["satisfied_by_exhibit_id"] else None,
            was_queried_in=uuid.UUID(r["was_queried_in"]) if r["was_queried_in"] else None,
            evidence=r["evidence"], origin=r.get("origin", "auto"), notes=r.get("notes")))
    for e in d.events:
        session.add(CaseEvent(case_id=case.id, card_id=new.id, event_code=e["code"], seq=e["seq"],
                              event_date=dt.date.fromisoformat(e["date"]) if e["date"] else None,
                              exhibit_id=uuid.UUID(e["exhibit_id"]) if e.get("exhibit_id") else None,
                              evidence=[e["evidence"]] if e.get("evidence") else [], origin=e.get("origin", "auto")))
    write_embeddings(session, case, new, ctx)
    n_corr = 0
    if state.get("base_card_status") != "approved":
        for row in corrections(state["draft_exhibits"], state["exhibits"], d.fields):
            session.add(ExtractionCorrection(case_id=case.id, draft_card_id=base.id, approved_card_id=new.id,
                                             created_by=user.id, review_assignment_id=a.id, **row))
            n_corr += 1
    a.status, a.card_id = "approved", new.id
    for other in _assignments(session, case.id, a.round):
        if other.id != a.id and other.status in ("assigned", "submitted"):
            other.status = "closed"
    facts = card["facts"]
    case.status = "approved"
    case.society_name = (facts["society"].get("name") or {}).get("value")
    case.filed_on = _d((facts["application"].get("date") or {}).get("value"))
    case.decided_on = _d((facts["order"].get("order_date") or {}).get("value"))
    session.flush()
    audit(session, "card.approve", actor_kind="user", case_id=case.id, entity_type="case_card", entity_id=new.id,
          actor=str(user.id), version=new.version, base_version=base.version, mode=a.mode, corrections=n_corr,
          requirements=len(d.requirements), events=len(d.events))
    return new


def _d(v: str | None) -> dt.date | None:
    return dt.date.fromisoformat(v) if v else None


def write_embeddings(session: Session, case: Case, card: CaseCard, ctx: ReviewContext) -> list[CaseEmbedding]:
    names = {**{c: {"name_mr": t["name_mr"], "name_en": t["name_en"]} for c, t in ctx.exhibit_types.items()},
             **{r["code"]: {"name_mr": r["name_mr"], "name_en": r["name_en"]} for r in ctx.requirement_catalog}}
    texts = facet_texts(card.card, names)
    client = get_embedding_client()
    vectors = client.embed([texts[f] for f in FACETS], data_class=case.data_class)
    as_of = _as_of(card.card)
    rows = []
    for facet, vec in zip(FACETS, vectors):
        row = CaseEmbedding(case_id=case.id, card_id=card.id, facet=facet, model=client.model, as_of=as_of,
                            source_text=texts[facet], embedding=vec)
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


# ------------------------------------------------------------------------------------------------
# Reports
# ------------------------------------------------------------------------------------------------
def extraction_accuracy(session: Session) -> dict:
    q = (select(ExtractionCorrection).join(CaseCard, ExtractionCorrection.approved_card_id == CaseCard.id))
    rows = [{"exhibit_type": c.exhibit_type, "field_path": c.field_path, "action": c.action, "draft_method": c.draft_method}
            for c in session.scalars(q)]
    cases = session.scalar(select(func.count(func.distinct(ExtractionCorrection.case_id))))
    return {"cases": cases or 0, **accuracy_report(rows)}


def agreement_report(session: Session, first_n: int | None = None) -> dict:
    first_n = first_n or double_review_quota(session)[1]
    rows = session.execute(
        select(ReviewAssignment, Case.case_ref).join(Case, ReviewAssignment.case_id == Case.id)
        .where(ReviewAssignment.mode == "double", ReviewAssignment.submitted.is_not(None))
        .order_by(ReviewAssignment.submitted_at)
    ).all()
    by_case: dict[uuid.UUID, dict] = {}
    for a, ref in rows:
        c = by_case.setdefault(a.case_id, {"case_id": str(a.case_id), "case_ref": ref, "subs": []})
        if a.reviewer_id not in [s.reviewer_id for s in c["subs"]]:
            c["subs"].append(a)
    cases = []
    for c in by_case.values():
        if len(c["subs"]) >= 2 and len(cases) < first_n:
            a, b = c["subs"][:2]
            cases.append({"case_id": c["case_id"], "case_ref": c["case_ref"],
                          "reviewers": [session.get(User, a.reviewer_id).email, session.get(User, b.reviewer_id).email],
                          "a": a.submitted["comparable"], "b": b.submitted["comparable"]})
    return {"first_n": first_n, **aggregate(cases)}


def save_report(session: Session, kind: str, metrics: dict, config: dict | None = None) -> EvalRun:
    from rci.pipeline.runner import code_fingerprint

    run = EvalRun(kind=kind, config=config or {}, metrics=json.loads(json.dumps(metrics, default=str)), status="done",
                  code_version=code_fingerprint(), finished_at=dt.datetime.now(dt.timezone.utc))
    session.add(run)
    session.flush()
    return run


def report_markdown(kind: str, m: dict) -> str:
    lines = [f"# {'Extraction accuracy' if kind == 'extraction_accuracy' else 'Double-review agreement'} report", ""]
    if kind == "extraction_accuracy":
        o = m["overall"]
        lines += [f"Cases: {m['cases']} · compared fields: {o['n']} · precision {o['precision']} · recall {o['recall']} · "
                  f"accuracy {o['accuracy']} · segmentation corrections {m['segmentation_corrections']} · "
                  f"stage corrections {m['stage_corrections']}", "",
                  "| field | n | accept | edit | not present | added | precision | recall |", "|---|---|---|---|---|---|---|---|"]
        for k, v in m["by_field"].items():
            lines.append(f"| {k} | {v['n']} | {v['accept']} | {v['edit']} | {v['not_present']} | {v['added']} | {v['precision']} | {v['recall']} |")
        lines += ["", "| draft method | n | precision | recall | accuracy |", "|---|---|---|---|---|"]
        for k, v in m["by_draft_method"].items():
            lines.append(f"| {k} | {v['n']} | {v['precision']} | {v['recall']} | {v['accuracy']} |")
    else:
        lines += [f"Cases (first {m['first_n']} double-reviewed): {m['cases']} · segmentation {m['segmentation']} · "
                  f"fields {m['fields']} ({m['fields_compared']} compared) · requirements {m['requirements_agreement']} "
                  f"(kappa {m['requirements_kappa']}) · query mapping {m['query_mapping']}", "",
                  "| case | reviewers | segmentation | stage | fields | requirements | query mapping | events | disagreements |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for c in m["per_case"]:
            lines.append(f"| {c['case_ref']} | {', '.join(c['reviewers'] or [])} | {c['segmentation']} | {c['stage']} | {c['fields']} | "
                         f"{c['requirements']} | {c['query_mapping']} | {c['events']} | {c['disagreement_count']} |")
    return "\n".join(lines) + "\n"


def state_hash(state: dict) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()[:12]
