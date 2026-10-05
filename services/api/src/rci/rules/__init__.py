"""Rules layer: rule sets (2019 rules, amendments, circulars, amnesty notifications) and their premium slabs,
requirements, obligations, adjustments and document checks. Pure resolution lives in `resolve.py`; this module loads rows."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.rules.resolve import (  # noqa: F401 - re-exported
    AmbiguousRule,
    CodedRow,
    NoRuleFound,
    RuleError,
    RuleSetRow,
    RuleStack,
    SlabResolution,
    SlabRow,
    UnverifiedRule,
    adjustments_for,
    build_stack,
    document_checks_for,
    evaluate,
    obligations_for,
    requirements_for,
    resolve_slab,
    select_stack,
)

CHS = "CHS"


def load_rows(session: Session) -> tuple[list[RuleSetRow], list[SlabRow], list[CodedRow], list[CodedRow], list[CodedRow],
                                        list[CodedRow]]:
    from rci.db.models import (
        RequirementCatalog,
        RuleAdjustment,
        RuleDocumentCheck,
        RuleObligation,
        RulePremiumSlab,
        RuleRequirement,
        RuleSet,
    )

    sets = session.scalars(select(RuleSet)).all()
    code_of = {r.id: r.code for r in sets}
    rule_sets = [RuleSetRow(code=r.code, kind=r.kind, effective_from=r.effective_from, effective_to=r.effective_to,
                            notification_date=r.notification_date, supersedes=code_of.get(r.supersedes_id),
                            status=r.status, name=r.name, id=r.id) for r in sets]
    slabs = [SlabRow(rule_set_code=code_of[s.rule_set_id], applicant_category=s.applicant_category,
                     holding_basis=s.holding_basis, land_use=s.land_use, rate_pct=s.rate_pct, rate_basis=s.rate_basis,
                     window_from=s.window_from, window_to=s.window_to, min_amount=s.min_amount, max_amount=s.max_amount,
                     conditions=s.conditions or {}, status=s.status, source_ref=s.source_ref, seed_key=s.seed_key, id=s.id)
             for s in session.scalars(select(RulePremiumSlab))]
    catalog = {c.code: c for c in session.scalars(select(RequirementCatalog))}
    reqs = [CodedRow(code_of[r.rule_set_id], r.requirement_code, r.condition or {}, r.status,
                     {"mandatory": r.mandatory, "source_ref": r.source_ref, **_catalog_data(catalog.get(r.requirement_code))})
            for r in session.scalars(select(RuleRequirement))]
    obls = [CodedRow(code_of[r.rule_set_id], r.code, r.condition or {}, r.status,
                     {"description_en": r.description_en, "description_mr": r.description_mr, "source_ref": r.source_ref})
            for r in session.scalars(select(RuleObligation))]
    adjs = [CodedRow(code_of[r.rule_set_id], r.code, r.condition or {}, r.status,
                     {"kind": r.kind, "rate_pct": r.rate_pct, "amount": r.amount, "applies_from": r.applies_from,
                      "applies_to": r.applies_to, "source_ref": r.source_ref})
            for r in session.scalars(select(RuleAdjustment))]
    checks = [CodedRow(code_of[r.rule_set_id], r.code, r.condition or {}, r.status,
                       {"slot": r.slot, "text_en": r.text_en, "text_mr": r.text_mr, "severity": r.severity,
                        "source_ref": r.source_ref, "condition": r.condition or {}})
              for r in session.scalars(select(RuleDocumentCheck))]
    return rule_sets, slabs, reqs, obls, adjs, checks


def _catalog_data(c) -> dict:
    """What the intake needs to know about a required document: its name and the slot it is filed under."""
    if c is None:
        return {"name_en": None, "name_mr": None, "category": None, "intake_slot": None}
    return {"name_en": c.name_en, "name_mr": c.name_mr, "category": c.category, "intake_slot": c.intake_slot}


def stack_for(session: Session, on: dt.date) -> RuleStack:
    """Rule sets in force on `on` (with their slabs / requirements / obligations / adjustments / document checks)."""
    return build_stack(on, *load_rows(session))


def verification_gaps(session: Session, on: dt.date, applicant_category: str = CHS) -> list[dict]:
    """Slabs that could be selected on `on` for the category but are not usable: to_verify rule set or slab,
    or rate not filled in. The Phase 2 exit check: this list should be empty (or documented)."""
    stack = stack_for(session, on)
    keys = sorted({(s.holding_basis, s.land_use) for s in stack.slabs
                   if s.applicant_category == applicant_category and s.in_window(on)})
    gaps = []
    for holding, land_use in keys:
        try:
            res = resolve_slab(stack, applicant_category=applicant_category, holding_basis=holding, land_use=land_use,
                               allow_unverified=True)
        except AmbiguousRule as e:
            gaps.append({"rule_set_code": None, "seed_key": None, "holding_basis": holding, "land_use": land_use,
                         "rate_pct": None, "reasons": [f"ambiguous: {e}"]})
            continue
        except NoRuleFound:
            continue
        s, rs = res.slab, res.rule_set
        reasons = []
        if rs.status != "verified":
            reasons.append("rule_set to_verify")
        if s.status != "verified":
            reasons.append("slab to_verify")
        if s.rate_pct is None:
            reasons.append("rate missing")
        if reasons:
            gaps.append({**s.to_json(), "reasons": reasons})
    return gaps
