"""Idempotent seed loader (upsert by primary/natural key)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rci.db.models import (
    EventCatalog,
    ExhibitType,
    Office,
    RequirementCatalog,
    RuleAdjustment,
    RuleDocumentCheck,
    RuleObligation,
    RulePremiumSlab,
    RuleRequirement,
    RuleSet,
)

SEED_DIR = Path(__file__).parent


def _load(name: str) -> dict:
    return yaml.safe_load((SEED_DIR / name).read_text(encoding="utf-8"))


def _upsert(session: Session, model, rows: list[dict], key: str) -> int:
    for row in rows:
        stmt = insert(model).values(**row)
        stmt = stmt.on_conflict_do_update(index_elements=[key], set_={k: stmt.excluded[k] for k in row if k != key})
        session.execute(stmt)
    return len(rows)


def seed_all(session: Session) -> dict[str, int]:
    offices = _load("offices.yaml")["offices"]
    et = _load("exhibit_types.yaml")
    rc = _load("requirement_catalog.yaml")
    ec = _load("event_catalog.yaml")
    counts = {
        "offices": _upsert(session, Office, offices, "code"),
        "exhibit_types": _upsert(
            session, ExhibitType,
            [{**{k: v for k, v in r.items()}, "keywords": r.get("keywords", []), "negative_keywords": r.get("negative_keywords", []),
              "catalog_version": et["catalog_version"]} for r in et["exhibit_types"]],
            "code",
        ),
        "requirement_catalog": _upsert(
            session, RequirementCatalog,
            [{"applies_when": {}, "intake_slot": None, "active": True, **r, "catalog_version": rc["catalog_version"]}
             for r in rc["requirements"]], "code",
        ),
        "event_catalog": _upsert(
            session, EventCatalog, [{**r, "catalog_version": ec["catalog_version"]} for r in ec["events"]], "code",
        ),
    }
    session.flush()
    counts.update(seed_rules(session))
    from rci.drafting.clauses import seed_clauses

    counts["clauses"] = seed_clauses(session)
    return counts


def _date(v) -> dt.date | None:
    return dt.date.fromisoformat(v) if isinstance(v, str) else v


_RULE_SET_COLS = ("name", "kind", "notification_ref", "notification_date", "effective_from", "effective_to", "status", "notes")
_CHILDREN = {
    "slabs": (RulePremiumSlab, ("applicant_category", "holding_basis", "land_use", "window_from", "window_to", "rate_pct",
                                "rate_basis", "min_amount", "max_amount", "conditions", "status", "source_ref", "notes")),
    "requirements": (RuleRequirement, ("requirement_code", "mandatory", "condition", "status", "source_ref", "notes")),
    "obligations": (RuleObligation, ("code", "description_mr", "description_en", "condition", "status", "source_ref", "notes")),
    "adjustments": (RuleAdjustment, ("code", "kind", "rate_pct", "amount", "applies_from", "applies_to", "condition",
                                     "status", "source_ref", "notes")),
    "document_checks": (RuleDocumentCheck, ("code", "slot", "text_en", "text_mr", "severity", "condition", "status",
                                            "source_ref", "notes")),
}
_DEFAULTS = {"status": "to_verify", "conditions": {}, "condition": {}, "mandatory": True, "severity": "problem"}


def _prune_rules(session: Session, data: dict) -> int:
    """Delete the rule sets listed under `retired` (with their rows) and every seeded row whose key is no longer in
    rules.yaml. Rows an admin added (no seed_key) are kept."""
    from sqlalchemy import delete, select, update

    from rci.db.models import Case, FeeCalculation

    removed = 0
    ids = list(session.scalars(select(RuleSet.id).where(RuleSet.code.in_(data.get("retired") or []))))
    if ids:
        for model, col in ((Case, Case.rule_set_id), (FeeCalculation, FeeCalculation.rule_set_id),
                           (RuleSet, RuleSet.supersedes_id)):
            session.execute(update(model).where(col.in_(ids)).values({col.key: None}))
        removed += session.execute(delete(RuleSet).where(RuleSet.id.in_(ids))).rowcount  # children cascade
    for group, (model, _) in _CHILDREN.items():
        keys = [row["key"] for spec in data["rule_sets"] for row in spec.get(group, [])]
        removed += session.execute(delete(model).where(model.seed_key.is_not(None), model.seed_key.not_in(keys))).rowcount
    session.flush()
    return removed


def seed_rules(session: Session) -> dict[str, int]:
    """Upsert rules.yaml. Rows an admin has edited or verified (updated_by set) are never overwritten; seeded rows and
    rule sets removed from the file are deleted (_prune_rules)."""
    from sqlalchemy import select

    data = _load("rules.yaml")
    counts = {"rule_sets": 0, "rule_rows": 0, "rule_rows_removed": _prune_rules(session, data)}
    by_code: dict[str, RuleSet] = {}
    for spec in data["rule_sets"]:
        rs = session.scalar(select(RuleSet).where(RuleSet.code == spec["code"]))
        if rs is None:
            rs = RuleSet(code=spec["code"])
            session.add(rs)
        if rs.updated_by is None:
            for c in _RULE_SET_COLS:
                v = spec.get(c, _DEFAULTS.get(c))
                setattr(rs, c, _date(v) if c.endswith(("_date", "_from", "_to")) else v)
        by_code[spec["code"]] = rs
        counts["rule_sets"] += 1
    session.flush()
    for spec in data["rule_sets"]:
        rs = by_code[spec["code"]]
        if rs.updated_by is None and spec.get("supersedes"):
            rs.supersedes_id = by_code[spec["supersedes"]].id
        for group, (model, cols) in _CHILDREN.items():
            for row in spec.get(group, []):
                obj = session.scalar(select(model).where(model.seed_key == row["key"]))
                if obj is None:
                    obj = model(seed_key=row["key"], rule_set_id=rs.id)
                    session.add(obj)
                elif obj.updated_by is not None:
                    continue
                for c in cols:
                    v = row.get(c, _DEFAULTS.get(c))
                    setattr(obj, c, _date(v) if c.startswith(("window_", "applies_")) else v)
                counts["rule_rows"] += 1
    session.flush()
    return counts
