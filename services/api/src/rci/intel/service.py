"""Case analysis (DB side): precedent pool → recall → rerank → gaps, next steps, fee trace; stored per run.

    analyse_case(session, case)          # latest card of the case (draft incoming or approved)

Matching
  1. SQL hard filters: approved + locked card, not the case itself, role in filters.roles (never holdout /
     incoming), same data_class and case_type, and — time travel — decided on or before `as_of`
     (case_embeddings.as_of). Then the same society (name key or registration no.) is dropped.
  2. pgvector recall: the query card's three facet texts are embedded with the configured embedding client; per
     facet the `per_facet_k` nearest precedent vectors *of the same model* are taken; the union is the candidate set.
     Without comparable vectors the whole pool is reranked on structured features only (and the result says so).
  3. Rerank (rank.py) with the configured / tuned weights; the top_k precedents drive gaps and steps.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from rci.adapters.embeddings import get_embedding_client
from rci.adapters.llm import LLMClient, get_llm_client
from rci.db.models import (
    Case,
    CaseAnalysis,
    CaseCard,
    CaseEmbedding,
    EventCatalog,
    ExhibitType,
    RequirementCatalog,
)
from rci.fees import service as fee_service
from rci.intel.config import INTEL_ENGINE_VERSION, IntelConfig
from rci.intel.explain import explain
from rci.intel.gaps import GapSettings, analyse_gaps
from rci.intel.profile import CaseProfile, build_profile, cite
from rci.intel.rank import Candidate, rerank
from rci.intel.steps import next_steps
from rci.pipeline.runner import audit, latest_card
from rci.review.summary import facet_texts
from rci.rules import obligations_for, requirements_for

FACETS = ("facts", "documents", "issues")
VALIDATOR_CODES = {"CTS_MATCH", "AREA_TOLERANCE", "HOLDER_IS_SOCIETY", "SGM_DATE_ORDER", "PROPERTY_CARD_RECENCY"}


class AnalysisError(Exception):
    """The case cannot be analysed (no card yet, ...) → HTTP 409."""


# ------------------------------------------------------------------------------------------------
# Catalogs and rules
# ------------------------------------------------------------------------------------------------
@dataclass
class Catalogs:
    requirements: list[dict]
    events: list[dict]
    names: dict[str, dict]

    @property
    def known_codes(self) -> set[str]:
        return {r["code"] for r in self.requirements} | {e["code"] for e in self.events} | VALIDATOR_CODES


def load_catalogs(session: Session) -> Catalogs:
    reqs = [{"code": r.code, "name_en": r.name_en, "name_mr": r.name_mr, "category": r.category, "status": r.status,
             "satisfied_by": r.satisfied_by, "applies_when": r.applies_when}
            for r in session.scalars(select(RequirementCatalog).where(RequirementCatalog.active).order_by(RequirementCatalog.code))]
    events = [{"code": e.code, "name_en": e.name_en, "name_mr": e.name_mr, "actor": e.actor, "typical_order": e.typical_order}
              for e in session.scalars(select(EventCatalog).where(EventCatalog.active).order_by(EventCatalog.typical_order))]
    names = {t.code: {"name_mr": t.name_mr, "name_en": t.name_en} for t in session.scalars(select(ExhibitType))}
    names.update({r["code"]: {"name_mr": r["name_mr"], "name_en": r["name_en"]} for r in reqs})
    return Catalogs(reqs, events, names)


class RulesView:
    """Rule stacks by date (rows loaded once) and what they require / oblige for a tenure."""

    def __init__(self, session: Session):
        self.stack = fee_service.stack_loader(session)
        self._req = lru_cache(maxsize=256)(self._requirements)
        self._obl = lru_cache(maxsize=256)(self._obligations)

    def regime(self, on: dt.date | None) -> tuple[str, ...]:
        return tuple(self.stack(on).codes) if on else ()

    def proc_regime(self, on: dt.date | None) -> tuple[str, ...]:
        """Rule sets in force that carry requirements or obligations (the procedure, not the premium rates)."""
        if not on:
            return ()
        st = self.stack(on)
        procedural = {r.rule_set_code for r in st.requirements + st.obligations}
        return tuple(c for c in st.codes if c in procedural)

    def candidate(self, prof: CaseProfile, card_id: str, basis: str) -> Candidate:
        d = prof.rule_date(basis)
        return Candidate(profile=prof, card_id=card_id, regime=self.regime(d), proc_regime=self.proc_regime(d))

    def _requirements(self, on: dt.date, tenure: str | None) -> dict[str, dict]:
        rows = requirements_for(self.stack(on), {"tenure": tenure})
        return {r["code"]: r for r in rows if r["applies"] is not False and r.get("mandatory", True)}

    def _obligations(self, on: dt.date, tenure: str | None) -> tuple[dict, ...]:
        return tuple(obligations_for(self.stack(on), {"tenure": tenure}))

    def statutory(self, on: dt.date | None, tenure: str | None) -> dict[str, dict]:
        return self._req(on, tenure) if on else {}

    def obligations(self, on: dt.date | None, tenure: str | None) -> list[dict]:
        return list(self._obl(on, tenure)) if on else []


# ------------------------------------------------------------------------------------------------
# Pool and recall
# ------------------------------------------------------------------------------------------------
def pool_query(case: Case, cfg: IntelConfig, as_of: dt.date):
    f = cfg.filters
    stmt = (select(Case, CaseCard)
            .join(CaseCard, and_(CaseCard.case_id == Case.id, CaseCard.status == "approved", CaseCard.locked.is_(True)))
            .where(Case.id != case.id, Case.role.in_(list(f["roles"]))))
    if f.get("same_data_class", True):
        stmt = stmt.where(Case.data_class == case.data_class)
    if f.get("same_case_type", True):
        stmt = stmt.where(Case.case_type == case.case_type)
    if f.get("time_travel", True):
        stmt = stmt.where(exists(select(CaseEmbedding.id).where(CaseEmbedding.card_id == CaseCard.id, CaseEmbedding.as_of <= as_of)))
    return stmt.order_by(Case.case_ref)


def pool_stats(session: Session, case: Case, cfg: IntelConfig, as_of: dt.date) -> dict:
    """How many approved precedents each hard filter removed (for the screen's "why so few" line)."""
    f = cfg.filters
    as_of_sq = (select(func.min(CaseEmbedding.as_of)).where(CaseEmbedding.card_id == CaseCard.id).correlate(CaseCard)
                .scalar_subquery())
    rows = session.execute(
        select(Case.role, Case.data_class, Case.case_type, as_of_sq)
        .join(CaseCard, and_(CaseCard.case_id == Case.id, CaseCard.status == "approved", CaseCard.locked.is_(True)))
        .where(Case.id != case.id)).all()
    out = {"approved_total": len(rows), "role": 0, "data_class": 0, "case_type": 0, "after_as_of": 0}
    for role, dc, ct, p_as_of in rows:
        if role not in f["roles"]:
            out["role"] += 1
        elif f.get("same_data_class", True) and dc != case.data_class:
            out["data_class"] += 1
        elif f.get("same_case_type", True) and ct != case.case_type:
            out["case_type"] += 1
        elif f.get("time_travel", True) and (p_as_of is None or p_as_of > as_of):
            out["after_as_of"] += 1
    return out


def same_society(q: CaseProfile, p: CaseProfile) -> bool:
    if q.society_key and q.society_key == p.society_key:
        return True
    return bool(q.registration_no and p.registration_no and q.registration_no.strip() == p.registration_no.strip())


def recall(session: Session, card_ids: list[uuid.UUID], vectors: dict[str, list[float]], model: str, k: int) -> dict[uuid.UUID, dict]:
    """card_id -> {facet: cosine} for the union of the per-facet top-k (same embedding model only)."""
    if not card_ids:
        return {}
    cand: set[uuid.UUID] = set()
    for facet in FACETS:
        dist = CaseEmbedding.embedding.cosine_distance(vectors[facet])
        cand |= set(session.scalars(select(CaseEmbedding.card_id).where(
            CaseEmbedding.card_id.in_(card_ids), CaseEmbedding.facet == facet, CaseEmbedding.model == model)
            .order_by(dist).limit(k)))
    sims: dict[uuid.UUID, dict] = {c: {} for c in cand}
    for facet in FACETS:
        dist = CaseEmbedding.embedding.cosine_distance(vectors[facet])
        for cid, d in session.execute(select(CaseEmbedding.card_id, dist).where(
                CaseEmbedding.card_id.in_(list(cand)), CaseEmbedding.facet == facet, CaseEmbedding.model == model)):
            sims[cid][facet] = 1.0 - float(d)
    return sims


def query_vectors(card: dict, cats: Catalogs, data_class: str) -> tuple[str | None, dict | None, str | None]:
    """(model, facet vectors, warning)."""
    try:
        client = get_embedding_client()
        texts = facet_texts(card, cats.names)
        vecs = client.embed([texts[f] for f in FACETS], data_class=data_class)
        return client.model, dict(zip(FACETS, vecs)), None
    except Exception as e:  # residency refusal, provider down, ...: rank on structured features only
        return None, None, f"no query embedding ({e}); ranked on structured features only"


# ------------------------------------------------------------------------------------------------
# Analysis
# ------------------------------------------------------------------------------------------------
def _json_date(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def precedent_json(c: Candidate, rank: int, q_regime: tuple[str, ...], q_rule_date: dt.date | None) -> dict:
    p = c.profile
    card = p.card
    order_ex = next((e for e in reversed(card["exhibits"]) if e["type"] == "COLLECTOR_ORDER"), None)
    app_ex = next((e for e in card["exhibits"] if e["type"] == "APPLICATION"), None)
    link = order_ex or app_ex or (card["exhibits"][0] if card["exhibits"] else None)
    p_date = p.rule_date()
    regime_label = "same_rules" if c.regime == q_regime else (
        "older_rules" if p_date and q_rule_date and p_date < q_rule_date else "other_rules")
    return {
        "rank": rank, "case_id": p.case_id, "case_ref": p.case_ref, "card_id": c.card_id, "society_name": p.society_name,
        "score": round(c.score, 4), "features": c.breakdown, "reasons": c.reasons,
        "similarity": {f: (round(v, 4) if v is not None else None) for f, v in c.sims.items()},
        "tenure": p.tenure, "village": p.village, "taluka": p.taluka, "district": p.district,
        "area_sqm": str(p.area_sqm) if p.area_sqm is not None else None,
        "application_date": _json_date(p.application_date), "order_date": _json_date(p.order_date), "as_of": _json_date(p.as_of),
        "decision": p.decision, "regime": list(c.regime), "procedural_regime": list(c.proc_regime), "regime_label": regime_label,
        "failed_validations": sorted(p.failed_validations), "open_codes": sorted(p.open_codes),
        "summary": card.get("summary"),
        "link": cite(card, exhibit_id=link["exhibit_id"]) if link else None,
    }


def analyse_case(session: Session, case: Case, *, as_of: dt.date | None = None, card_row: CaseCard | None = None,
                 card: dict | None = None, user_id: uuid.UUID | None = None, store: bool = True, cfg: IntelConfig | None = None,
                 llm: LLMClient | None = None, explain_llm: bool | None = None, today: dt.date | None = None,
                 rules: RulesView | None = None, cats: Catalogs | None = None) -> dict:
    today = today or dt.date.today()
    cfg = cfg or IntelConfig.load(data_class=case.data_class)
    card_row = card_row if card_row is not None or card is not None else latest_card(session, case.id)
    if card is None:
        if card_row is None:
            raise AnalysisError("case has no card yet — ingest the docket first")
        card = card_row.card
    as_of = as_of or today
    cats = cats or load_catalogs(session)
    rules = rules or RulesView(session)
    fee_params = fee_service.load_params()
    warnings: list[str] = []

    q = build_profile(card)
    q_rule_date = q.rule_date(fee_params.rule_date_basis)
    if q_rule_date is None:
        q_rule_date = today
        warnings.append(f"{fee_params.rule_date_basis} not on the card; rules taken as on the analysis date {today}")
    q_regime, q_proc = rules.regime(q_rule_date), rules.proc_regime(q_rule_date)

    # 1. pool (SQL hard filters) minus same society
    rows = session.execute(pool_query(case, cfg, as_of)).all()
    stats = pool_stats(session, case, cfg, as_of)
    pool: list[Candidate] = []
    stats["same_society"] = 0
    for c, cc in rows:
        prof = build_profile(cc.card)
        if cfg.filters.get("exclude_same_society", True) and same_society(q, prof):
            stats["same_society"] += 1
            continue
        pool.append(rules.candidate(prof, str(cc.id), fee_params.rule_date_basis))
    stats["pool"] = len(pool)

    # 2. recall
    model, vectors, warn = query_vectors(card, cats, case.data_class)
    if warn:
        warnings.append(warn)
    by_card = {c.card_id: c for c in pool}
    cands: list[Candidate]
    if vectors and pool:
        sims = recall(session, [uuid.UUID(c.card_id) for c in pool], vectors, model, cfg.per_facet_k)
        stats["no_embedding_for_model"] = len(pool) - session.scalar(
            select(func.count(func.distinct(CaseEmbedding.card_id))).where(
                CaseEmbedding.card_id.in_([uuid.UUID(c.card_id) for c in pool]), CaseEmbedding.model == model))
        if sims:
            cands = []
            for cid, s in sims.items():
                c = by_card[str(cid)]
                c.sims = {f: s.get(f) for f in FACETS}
                cands.append(c)
        else:
            warnings.append(f"no precedent has {model} vectors; ranked on structured features only")
            cands = pool
    else:
        cands = pool
    stats["recalled"] = len(cands)

    # 3. rerank, top-k
    ranked = rerank(q, cands, cfg.weights, q_regime, q_rule_date)
    top = [c for c in ranked if c.score > 0][:cfg.top_k]
    if not top:
        warnings.append("no precedents passed the filters; gaps rest on the rules and the case's own checks only")

    # 4. gaps
    statutory_now = rules.statutory(q_rule_date, q.tenure)
    statutory_then = {c.profile.case_id: set(rules.statutory(c.profile.rule_date(fee_params.rule_date_basis), c.profile.tenure))
                      for c in top}
    gaps = analyse_gaps(q, top, cats.requirements, statutory_now, statutory_then, q_proc,
                        GapSettings(cfg.support_threshold, cfg.min_support_cases))
    use_llm = cfg.explain_with_llm if explain_llm is None else explain_llm
    explanation = explain(gaps, llm=(llm or get_llm_client()) if use_llm else None, data_class=case.data_class,
                          known_codes=cats.known_codes)

    # 5. next steps
    obligations_then = {c.profile.case_id: {o["code"] for o in rules.obligations(c.profile.rule_date(fee_params.rule_date_basis),
                                                                                  c.profile.tenure) if o["applies"] is not False}
                        for c in top}
    steps = next_steps(q, top, cats.events, cfg.raw.get("extra_steps", {}), rules.obligations(q_rule_date, q.tenure),
                       obligations_then, cfg.raw.get("obligation_steps", {}), cfg.raw.get("fact_triggers", []), q_proc,
                       cfg.step_threshold)

    # 6. fee
    fee = fee_service.calculate_for_card(session, card, params=fee_params, today=today, loader=rules.stack)

    result = {
        "engine": {"intel": INTEL_ENGINE_VERSION, "fees": fee.engine_version, "config_hash": cfg.fingerprint(),
                   "weights_source": cfg.source(), "tuned": cfg.tuned, "weights": cfg.weights,
                   "support_threshold": cfg.support_threshold, "embedding_model": model},
        "case": {"id": str(case.id), "case_ref": case.case_ref, "data_class": case.data_class, "role": case.role},
        "card": {"id": str(card_row.id) if card_row else None, "version": card_row.version if card_row else None,
                 "status": card.get("status")},
        "as_of": as_of.isoformat(), "rule_date": q_rule_date.isoformat(), "regime": list(q_regime),
        "procedural_regime": list(q_proc),
        "query": {"society_name": q.society_name, "tenure": q.tenure, "village": q.village, "taluka": q.taluka,
                  "district": q.district, "area_sqm": str(q.area_sqm) if q.area_sqm is not None else None,
                  "application_date": _json_date(q.application_date), "failed_validations": sorted(q.failed_validations),
                  "open_codes": sorted(q.open_codes)},
        "pool": stats,
        "precedents": [precedent_json(c, i, q_regime, q_rule_date) for i, c in enumerate(top, start=1)],
        "gaps": gaps, "explanations": explanation,
        "steps": steps,
        "fee": fee.to_json(),
        "warnings": warnings,
    }
    if store:
        if card_row is None:
            raise AnalysisError("only a stored card can be analysed with store=True")
        fee_row = fee_service.store(session, case, card_row, fee, user_id)
        aid = uuid.uuid4()
        result["analysis_id"], result["created_at"] = str(aid), dt.datetime.now(dt.timezone.utc).isoformat()
        result["fee"]["calculation_id"] = str(fee_row.id)
        row = CaseAnalysis(id=aid, case_id=case.id, card_id=card_row.id, as_of=as_of, rule_date=q_rule_date,
                           engine_version=INTEL_ENGINE_VERSION, config_hash=cfg.fingerprint(), embedding_model=model,
                           result=result, fee_calculation_id=fee_row.id, created_by=user_id)
        session.add(row)
        session.flush()
        audit(session, "case.analyse", actor_kind="user" if user_id else "system", case_id=case.id, entity_type="case_analysis",
              entity_id=row.id, actor=str(user_id) if user_id else None, precedents=len(top), gaps=len(gaps),
              next_steps=len(steps["next"]), fee_status=fee.status, as_of=as_of.isoformat())
    return result


def latest_analysis(session: Session, case_id: uuid.UUID) -> CaseAnalysis | None:
    return session.scalar(select(CaseAnalysis).where(CaseAnalysis.case_id == case_id).order_by(CaseAnalysis.created_at.desc()).limit(1))

