"""Stratified holdout selection. Holdouts are approved precedents set aside for the backtest: role = holdout and
role_locked = true, so matching, gap support, tuning and clause mining never see them.

    select_holdouts(session, n=20, data_class=…, seed=7)

Eligible: approved + locked card, role precedent / unassigned, not role-locked, the requested data class (and case-ref
prefix, if given), an application date on the card. Strata are the combinations of the configured dimensions
(tenure × era by default); n is split over the non-empty strata by largest remainder (≥ 1 each when n allows, never
more than a stratum holds), then cases are drawn per stratum with a seeded shuffle of the sorted case refs, so the
selection is reproducible. A selection is final: once holdouts exist for the data class it is returned as it is.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from rci.config import get_settings
from rci.db.models import Case, CaseCard
from rci.intel.profile import build_profile


@dataclass
class BacktestConfig:
    raw: dict

    @classmethod
    def load(cls, path: Path | None = None) -> "BacktestConfig":
        return cls(yaml.safe_load((path or get_settings().backtest_file).read_text(encoding="utf-8")) or {})

    @property
    def holdouts(self) -> dict:
        return self.raw.get("holdouts") or {}

    @property
    def eras(self) -> list[dt.date]:
        return [dt.date.fromisoformat(str(d)) for d in self.holdouts.get("eras", [])]

    @property
    def gate(self) -> dict:
        return self.raw.get("gate") or {}


def era(d: dt.date | None, boundaries: list[dt.date]) -> str:
    if d is None:
        return "unknown"
    before = [b for b in sorted(boundaries) if d < b]
    return f"before_{before[0].isoformat()}" if before else (f"from_{max(boundaries).isoformat()}" if boundaries else "all")


def stratum_of(card: dict, cfg: BacktestConfig) -> dict:
    p = build_profile(card)
    return {"tenure": p.tenure or "unknown", "era": era(p.application_date, cfg.eras), "decision": p.decision or "pending",
            "queried": "queried" if p.req_queried else "no_query"}


def allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """Largest-remainder allocation of n over strata of the given sizes (deterministic)."""
    strata = sorted(k for k, v in counts.items() if v > 0)
    total = sum(counts[k] for k in strata)
    n = min(n, total)
    if not strata or n <= 0:
        return {k: 0 for k in counts}
    alloc = {k: 0 for k in counts}
    floor_one = n >= len(strata)
    if floor_one:
        for k in strata:
            alloc[k] = 1
    remaining = n - sum(alloc.values())
    room = {k: counts[k] - alloc[k] for k in strata}
    base = sum(room.values())
    if remaining > 0 and base > 0:
        quotas = {k: remaining * room[k] / base for k in strata}
        for k in strata:
            alloc[k] += min(room[k], int(quotas[k]))
        left = n - sum(alloc.values())
        order = sorted(strata, key=lambda k: (-(quotas[k] - int(quotas[k])), -counts[k], k))
        while left > 0:
            progressed = False
            for k in order:
                if left and alloc[k] < counts[k]:
                    alloc[k] += 1
                    left -= 1
                    progressed = True
            if not progressed:
                break
    return alloc


def choose(eligible: list[tuple[str, str]], n: int, seed: int) -> list[str]:
    """eligible = [(case_ref, stratum key)] → selected case refs."""
    by: dict[str, list[str]] = {}
    for ref, key in eligible:
        by.setdefault(key, []).append(ref)
    alloc = allocate({k: len(v) for k, v in by.items()}, n)
    out = []
    for key in sorted(by):
        refs = sorted(by[key])
        random.Random(f"{seed}:{key}").shuffle(refs)
        out += refs[: alloc[key]]
    return sorted(out)


def _approved(session: Session, data_class: str, prefix: str | None, roles: tuple[str, ...]):
    q = (select(Case, CaseCard).join(CaseCard, and_(CaseCard.case_id == Case.id, CaseCard.status == "approved", CaseCard.locked.is_(True)))
         .where(Case.data_class == data_class, Case.role.in_(roles)).order_by(Case.case_ref))
    if prefix:
        q = q.where(Case.case_ref.like(f"{prefix}%"))
    return session.execute(q).all()


def holdout_cases(session: Session, data_class: str, prefix: str | None = None) -> list[tuple[Case, CaseCard]]:
    return _approved(session, data_class, prefix, ("holdout",))


def holdouts_hash(refs: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(refs)).encode()).hexdigest()[:16]


def select_holdouts(session: Session, *, data_class: str, n: int | None = None, seed: int | None = None, prefix: str | None = None,
                    cfg: BacktestConfig | None = None, by: str = "cli") -> dict:
    from rci.pipeline.runner import audit
    from rci.review.service import save_report

    cfg = cfg or BacktestConfig.load()
    n = n if n is not None else int(cfg.holdouts.get("n", 20))
    seed = seed if seed is not None else int(cfg.holdouts.get("seed", 7))
    dims = cfg.holdouts.get("strata", ["tenure", "era"])
    existing = holdout_cases(session, data_class, prefix)
    if existing:
        refs = [c.case_ref for c, _ in existing]
        return {"selected": False, "reason": "holdouts already locked for this data class", "holdouts": refs,
                "holdouts_hash": holdouts_hash(refs)}
    rows = [(c, cc) for c, cc in _approved(session, data_class, prefix, ("precedent", "unassigned"))
            if not c.role_locked and build_profile(cc.card).application_date]
    strata = {c.case_ref: stratum_of(cc.card, cfg) for c, cc in rows}
    key = lambda ref: "|".join(str(strata[ref][d]) for d in dims)  # noqa: E731
    chosen = choose([(c.case_ref, key(c.case_ref)) for c, _ in rows], n, seed)
    for c, _ in rows:
        if c.case_ref in chosen:
            c.role, c.role_locked = "holdout", True
    session.flush()
    counts: dict[str, dict] = {}
    for c, _ in rows:
        k = key(c.case_ref)
        counts.setdefault(k, {"eligible": 0, "selected": 0})
        counts[k]["eligible"] += 1
        counts[k]["selected"] += int(c.case_ref in chosen)
    result = {"selected": True, "data_class": data_class, "prefix": prefix, "n": n, "seed": seed, "strata_dims": dims,
              "eligible": len(rows), "holdouts": chosen, "holdouts_hash": holdouts_hash(chosen), "strata": counts,
              "stratum_of": {r: strata[r] for r in chosen}}
    run = save_report(session, "holdout_selection", result, {"by": by, "data_class": data_class, "seed": seed, "n": n})
    audit(session, "backtest.holdouts", actor_kind="user" if by != "cli" else "cli", entity_type="eval_run", entity_id=run.id,
          selected=len(chosen), data_class=data_class, seed=seed)
    result["eval_run_id"] = str(run.id)
    return result


def release_holdouts(session: Session, *, data_class: str, prefix: str | None = None) -> int:
    """Unlock (back to precedent). Only for a deliberate re-selection; every backtest run records the set it used."""
    from rci.pipeline.runner import audit

    rows = holdout_cases(session, data_class, prefix)
    for c, _ in rows:
        c.role, c.role_locked = "precedent", False
    session.flush()
    audit(session, "backtest.holdouts_released", actor_kind="cli", released=len(rows), data_class=data_class)
    return len(rows)
