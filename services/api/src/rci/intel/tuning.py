"""Leave-one-out tuning of SUPPORT_THRESHOLD and the rerank weights, on precedents only (never holdouts).

For each precedent P (role precedent / unassigned, approved card):
  query   = P's card masked to the filing stage: only exhibits filed with the application, re-validated and
            re-assembled (no query letter, no office documents) — what the office saw on day one;
  pool    = every other precedent, minus P's society, decided before P was filed (time travel), same data class;
  truth   = the requirement codes the office actually queried in P (was_queried_in).
Objective: micro-F1 of the reported gaps against the truth (tie-break: nDCG@5 of the precedent ranking with
relevance = Jaccard of queried-code sets). Features do not depend on the weights, so each query's candidates and
features are computed once and every weight / threshold setting only re-scores them.

Search: grid over the support threshold, then coordinate ascent over each weight's multiplier, two passes.
`--save` writes config/intel_tuned.yaml and an eval_runs row (kind loo_tuning); defaults stay in config/intel.yaml.
"""
from __future__ import annotations

import datetime as dt
import math
import uuid
from dataclasses import dataclass

import yaml
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from rci.config import Parameters, get_settings
from rci.db.models import Case, CaseCard, EvalRun, EventCatalog, Exhibit, RequirementCatalog
from rci.fees.service import load_params
from rci.intel.config import FEATURES, IntelConfig
from rci.intel.gaps import GapSettings, analyse_gaps
from rci.intel.profile import CaseProfile, build_profile
from rci.intel.rank import Candidate, jaccard, rerank, score
from rci.intel.service import FACETS, Catalogs, RulesView, load_catalogs, query_vectors, recall, same_society
from rci.pipeline.card import CardExhibit, assemble_card
from rci.pipeline.validators import ExhibitFacts, run_validators

THRESHOLDS = [round(0.2 + 0.05 * i, 2) for i in range(13)]  # 0.20 … 0.80
MULTIPLIERS = [0.0, 0.5, 1.0, 1.5, 2.0]
MIN_CANDIDATES = 3


class HoldoutLeak(AssertionError):
    pass


def filing_card(session: Session, case: Case, card: dict) -> dict:
    """The card as it would have looked at filing: filing-stage exhibits only, validators and requirements recomputed."""
    exs = session.scalars(select(Exhibit).where(Exhibit.case_id == case.id, Exhibit.stage == "filing").order_by(Exhibit.seq)).all()
    reqs = [{"code": r.code, "satisfied_by": r.satisfied_by, "query_keywords": r.query_keywords, "applies_when": r.applies_when,
             "status": r.status} for r in session.scalars(select(RequirementCatalog).where(RequirementCatalog.active).order_by(RequirementCatalog.code))]
    events = [{"code": e.code, "source_exhibit_types": e.source_exhibit_types, "date_field": e.date_field, "typical_order": e.typical_order}
              for e in session.scalars(select(EventCatalog).where(EventCatalog.active).order_by(EventCatalog.typical_order))]
    card_exs = [CardExhibit(ExhibitFacts(str(e.id), n, e.exhibit_type, e.stage, (e.extraction or {}).get("fields") or {}),
                            list(range(e.start_page_seq, e.end_page_seq + 1)), e.confidence or 1.0, e.classification_method or "manual",
                            e.doc_date) for n, e in enumerate(exs, start=1)]
    params = Parameters.load(get_settings().parameters_file)
    masked, _, _ = assemble_card(case=card["case"], exhibits=card_exs, validations=run_validators([c.facts for c in card_exs], params),
                                 requirement_catalog=reqs, event_catalog=events, pipeline_version=card.get("pipeline_version", "p1.0"),
                                 item_codes=lambda *_: [], validate=False)
    return masked


@dataclass
class LooQuery:
    case_ref: str
    profile: CaseProfile
    candidates: list[Candidate]
    regime: tuple[str, ...]
    proc_regime: tuple[str, ...]
    rule_date: dt.date
    truth: set[str]
    statutory_now: dict
    statutory_then: dict[str, set[str]]


def precedents(session: Session, cfg: IntelConfig) -> list[tuple[Case, CaseCard]]:
    rows = session.execute(
        select(Case, CaseCard).join(CaseCard, and_(CaseCard.case_id == Case.id, CaseCard.status == "approved", CaseCard.locked.is_(True)))
        .where(Case.role.in_(list(cfg.filters["roles"]))).order_by(Case.case_ref)).all()
    if any(c.role == "holdout" for c, _ in rows):
        raise HoldoutLeak("a holdout case reached the tuning set")
    return rows


def build_queries(session: Session, cfg: IntelConfig, *, data_class: str | None = None, cats: Catalogs | None = None) -> tuple[list[LooQuery], dict]:
    cats = cats or load_catalogs(session)
    rules = RulesView(session)
    basis = load_params().rule_date_basis
    rows = [(c, cc) for c, cc in precedents(session, cfg) if data_class is None or c.data_class == data_class]
    profiles = {str(c.id): (c, cc, build_profile(cc.card)) for c, cc in rows}
    queries, skipped = [], {"no_truth_or_date": 0, "few_candidates": 0}
    for cid, (case, cc, prof) in profiles.items():
        fdate = prof.application_date
        if fdate is None:
            skipped["no_truth_or_date"] += 1
            continue
        masked = filing_card(session, case, cc.card)
        q = build_profile(masked)
        pool = []
        for oid, (oc, occ, op) in profiles.items():
            if oid == cid or oc.data_class != case.data_class or same_society(prof, op):
                continue
            if cfg.filters.get("time_travel", True) and (op.as_of is None or op.as_of > fdate):
                continue
            pool.append(rules.candidate(op, str(occ.id), basis))
        if len(pool) < MIN_CANDIDATES:
            skipped["few_candidates"] += 1
            continue
        model, vectors, _ = query_vectors(masked, cats, case.data_class)
        if vectors:
            sims = recall(session, [uuid.UUID(c.card_id) for c in pool], vectors, model, cfg.per_facet_k)
            for c in pool:
                c.sims = {f: sims.get(uuid.UUID(c.card_id), {}).get(f) for f in FACETS}
        rd = q.rule_date(basis) or fdate
        regime = rules.regime(rd)
        rerank(q, pool, cfg.weights, regime, rd)  # computes features (weight-independent)
        queries.append(LooQuery(case.case_ref, q, pool, regime, rules.proc_regime(rd), rd, set(prof.req_queried),
                                rules.statutory(rd, q.tenure),
                                {c.profile.case_id: set(rules.statutory(c.profile.rule_date(basis), c.profile.tenure)) for c in pool}))
    return queries, skipped


def _ndcg5(q: LooQuery, ranked: list[Candidate]) -> float | None:
    if not q.truth:
        return None
    rel = [jaccard(q.truth, set(c.profile.req_queried), empty=0.0) for c in ranked[:5]]
    ideal = sorted((jaccard(q.truth, set(c.profile.req_queried), empty=0.0) for c in q.candidates), reverse=True)[:5]
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rel))
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal))
    return dcg / idcg if idcg else None


def evaluate(queries: list[LooQuery], weights: dict[str, float], threshold: float, cats: Catalogs, cfg: IntelConfig) -> dict:
    tp = fp = fn = 0
    ndcgs = []
    per_case = []
    for q in queries:
        for c in q.candidates:
            c.score, c.breakdown = score(c.features, weights)
        ranked = sorted(q.candidates, key=lambda c: (-c.score, c.profile.case_ref))
        top = [c for c in ranked if c.score > 0][:cfg.top_k]
        gaps = analyse_gaps(q.profile, top, cats.requirements, q.statutory_now, q.statutory_then, q.proc_regime,
                            GapSettings(threshold, cfg.min_support_cases))
        pred = {g["code"] for g in gaps}
        t, f_p, f_n = len(pred & q.truth), len(pred - q.truth), len(q.truth - pred)
        tp, fp, fn = tp + t, fp + f_p, fn + f_n
        n = _ndcg5(q, ranked)
        if n is not None:
            ndcgs.append(n)
        per_case.append({"case_ref": q.case_ref, "truth": sorted(q.truth), "predicted": sorted(pred), "tp": t, "fp": f_p, "fn": f_n,
                         "ndcg5": round(n, 4) if n is not None else None})
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "ndcg5": round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else None, "tp": tp, "fp": fp, "fn": fn, "per_case": per_case}


def _objective(m: dict) -> tuple[float, float]:
    return (m["f1"], m["ndcg5"] or 0.0)


def tune(session: Session, *, cfg: IntelConfig | None = None, data_class: str | None = None, passes: int = 2) -> dict:
    cfg = cfg or IntelConfig.load(tuned_path=get_settings().repo_root / "config" / "__none__.yaml")  # tune from the defaults
    cats = load_catalogs(session)
    queries, skipped = build_queries(session, cfg, data_class=data_class, cats=cats)
    base_w, base_t = cfg.weights, cfg.support_threshold
    baseline = evaluate(queries, base_w, base_t, cats, cfg)
    best_w, best_t, best = dict(base_w), base_t, baseline
    if queries:
        for _ in range(passes):
            for t in THRESHOLDS:
                m = evaluate(queries, best_w, t, cats, cfg)
                if _objective(m) > _objective(best):
                    best_t, best = t, m
            for f in FEATURES:
                for mult in MULTIPLIERS:
                    w = {**best_w, f: round(base_w[f] * mult, 3) if base_w[f] else round(mult * 0.5, 3)}
                    if sum(w.values()) <= 0:
                        continue
                    m = evaluate(queries, w, best_t, cats, cfg)
                    if _objective(m) > _objective(best):
                        best_w, best = w, m
    return {"n_queries": len(queries), "skipped": skipped, "data_class": data_class,
            "default": {"weights": base_w, "support_threshold": base_t, "metrics": {k: v for k, v in baseline.items() if k != "per_case"}},
            "tuned": {"weights": best_w, "support_threshold": best_t, "metrics": {k: v for k, v in best.items() if k != "per_case"}},
            "per_case": best["per_case"], "baseline_per_case": baseline["per_case"],
            "cases": [q.case_ref for q in queries]}


def save(session: Session, result: dict, *, by: str = "cli") -> EvalRun:
    from rci.review.service import save_report

    run = save_report(session, "loo_tuning", result, {"by": by, "data_class": result["data_class"]})
    s = get_settings()
    doc = {
        "tuned": {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "eval_run_id": str(run.id),
                  "n_cases": result["n_queries"], "data_class": result["data_class"],
                  "objective": {"f1": result["tuned"]["metrics"]["f1"], "ndcg5": result["tuned"]["metrics"]["ndcg5"],
                                "default_f1": result["default"]["metrics"]["f1"]},
                  # the tuning set; the Phase 4 backtest refuses these weights if any of its holdouts is in here
                  "cases": sorted(result["cases"])},
        "support_threshold": result["tuned"]["support_threshold"],
        "weights": result["tuned"]["weights"],
    }
    header = ("# Written by `rci tune --save` (leave-one-out on precedents). Overrides config/intel.yaml for cases of\n"
              f"# data class {result['data_class'] or 'any'}; delete this file to go back to the defaults.\n")
    s.intel_tuned_file.write_text(header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return run


def report_markdown(r: dict) -> str:
    d, t = r["default"], r["tuned"]
    lines = ["# Leave-one-out tuning (precedents only)", "",
             f"Queries: {r['n_queries']} · skipped: {r['skipped']} · data class: {r['data_class'] or 'all'}", "",
             "| setting | support threshold | precision | recall | F1 | nDCG@5 |", "|---|---|---|---|---|---|",
             f"| default | {d['support_threshold']} | {d['metrics']['precision']} | {d['metrics']['recall']} | {d['metrics']['f1']} | {d['metrics']['ndcg5']} |",
             f"| tuned | {t['support_threshold']} | {t['metrics']['precision']} | {t['metrics']['recall']} | {t['metrics']['f1']} | {t['metrics']['ndcg5']} |",
             "", "| feature | default weight | tuned weight |", "|---|---|---|"]
    lines += [f"| {f} | {d['weights'][f]} | {t['weights'][f]} |" for f in FEATURES]
    lines += ["", "| case | queried (truth) | predicted gaps | tp | fp | fn |", "|---|---|---|---|---|---|"]
    lines += [f"| {c['case_ref']} | {', '.join(c['truth'])} | {', '.join(c['predicted'])} | {c['tp']} | {c['fp']} | {c['fn']} |"
              for c in r["per_case"]]
    lines += ["", "Tuned on the same precedents it is scored on (hyper-parameters only); the Phase 4 backtest on holdouts is the "
              "out-of-sample measure."]
    return "\n".join(lines) + "\n"
