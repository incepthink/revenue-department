"""Backtest metrics (pure). Per-case results come from harness.py; officer labels are applied here, so a stored run
can be re-scored when adjudications / relevance labels / draft ratings arrive later.

Queries      truth = every requirement code the office asked for (all query letters of the approved card);
             predicted = the gaps the draft query letter would carry (drafting query_letter.include_bases)
               precision / recall / F1           micro-averaged over holdouts
               first_letter_completeness         |predicted ∩ truth| / |truth|, mean over holdouts that were queried
                                                 (the tool's single first letter vs everything the office eventually asked)
               office_first_letter_completeness  the office's own first letter against the same truth (baseline)
               statutory_false_negatives         truth codes required by the rules in force that the tool missed
               adjudicated_precision             (TP + false positives adjudicated "valid but not asked") / predicted
Steps        truth = event codes after filing, in order; predicted = next-step events in timeline order
               precision / recall / F1 (micro), Kendall tau on the common events (mean over holdouts with ≥ 2)
Fee          predicted = the engine's total on the filing-stage card; observed = challans, else order, else demand
               coverage, exact-match rate (to the rupee), mean / median absolute % error
Precedents   P@5 and nDCG@5 from officer relevance labels (0 / 1 / 2) on the top five; proxy nDCG@5 (Jaccard of
             queried codes, as in tuning) is reported alongside and never gated
Drafts       render-ok rate, leak failures, complete rate; officer ratings (1–5) mean
"""
from __future__ import annotations

import math
import statistics
from decimal import Decimal

FP_VERDICTS = ("truly_unnecessary", "valid_not_asked")


def prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p is not None and r is not None and p + r else (0.0 if p is not None and r is not None else None)
    return p, r, f


def kendall_tau(predicted: list[str], truth: list[str]) -> float | None:
    """Tau-a over the items both orders contain (no ties: each list is an order of distinct codes)."""
    common = [c for c in truth if c in predicted]
    n = len(common)
    if n < 2:
        return None
    pos = {c: i for i, c in enumerate(predicted)}
    conc = disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            if pos[common[i]] < pos[common[j]]:
                conc += 1
            else:
                disc += 1
    return (conc - disc) / (n * (n - 1) / 2)


def dcg(rels: list[float]) -> float:
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels))


def ndcg_at_k(rels_in_rank_order: list[float | None], all_labelled: list[float], k: int = 5) -> float | None:
    """Unlabelled positions count as 0; the ideal ranking uses every labelled precedent of the holdout."""
    top = [r or 0.0 for r in rels_in_rank_order[:k]]
    ideal = dcg(sorted(all_labelled, reverse=True)[:k])
    return dcg(top) / ideal if ideal else None


def p_at_k(rels_in_rank_order: list[float | None], k: int = 5) -> float | None:
    labelled = [r for r in rels_in_rank_order[:k] if r is not None]
    return sum(1 for r in labelled if r >= 1) / len(labelled) if labelled else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _r(x):
    return round(x, 4) if isinstance(x, float) else x


def case_metrics(o: dict, labels: dict) -> dict:
    """o = one holdout's outputs; labels = {"fp": {code: verdict}, "relevance": {case_id: rel}, "ratings": [..]}."""
    pred, truth = set(o["predicted"]), set(o["truth_queried"])
    tp, fp, fn = len(pred & truth), len(pred - truth), len(truth - pred)
    p, r, f = prf(tp, fp, fn)
    fps = sorted(pred - truth)
    verdicts = {c: labels.get("fp", {}).get(c) for c in fps}
    first = set(o["truth_first_letter"])
    s_tp = len(set(o["steps_predicted"]) & set(o["steps_truth"]))
    s_fp = len(set(o["steps_predicted"]) - set(o["steps_truth"]))
    s_fn = len(set(o["steps_truth"]) - set(o["steps_predicted"]))
    fee = o.get("fee") or {}
    pred_fee, obs_fee = fee.get("predicted"), fee.get("observed")
    err = exact = None
    if pred_fee is not None and obs_fee not in (None, "0", "0.00"):
        pv, ov = Decimal(str(pred_fee)), Decimal(str(obs_fee))
        err = float(abs(pv - ov) / ov * 100)
        exact = pv.quantize(Decimal("1")) == ov.quantize(Decimal("1"))
    rel = labels.get("relevance", {})
    top = [rel.get(t["case_id"]) for t in o.get("top5", [])]
    drafts = o.get("drafts") or {}
    return {
        "q_tp": tp, "q_fp": fp, "q_fn": fn, "query_precision": _r(p), "query_recall": _r(r), "query_f1": _r(f),
        "first_letter_completeness": _r(len(pred & truth) / len(truth)) if truth else None,
        "office_first_letter_completeness": _r(len(first & truth) / len(truth)) if truth else None,
        "complete_first_letter": (truth <= pred) if truth else None,
        "statutory_false_negatives": sorted((truth & set(o["statutory_codes"])) - pred),
        "false_positives": fps, "fp_verdicts": verdicts,
        "fp_valid_not_asked": sum(1 for v in verdicts.values() if v == "valid_not_asked"),
        "fp_truly_unnecessary": sum(1 for v in verdicts.values() if v == "truly_unnecessary"),
        "fp_pending": sum(1 for v in verdicts.values() if v is None),
        "s_tp": s_tp, "s_fp": s_fp, "s_fn": s_fn, "kendall_tau": _r(kendall_tau(o["steps_predicted"], o["steps_truth"])),
        "fee_predicted": pred_fee is not None, "fee_observed": obs_fee is not None, "fee_abs_pct_error": _r(err), "fee_exact": exact,
        "p_at_5": _r(p_at_k(top)), "ndcg_at_5": _r(ndcg_at_k(top, [v for v in rel.values() if v is not None])),
        "relevance_labelled": sum(1 for x in top if x is not None), "proxy_ndcg_at_5": o.get("proxy_ndcg_at_5"),
        "drafts_attempted": len(drafts), "drafts_ok": sum(1 for d in drafts.values() if d.get("render_ok")),
        "drafts_complete": sum(1 for d in drafts.values() if d.get("complete")),
        "leak_failures": sum(1 for d in drafts.values() if d.get("render_ok") and not d.get("leak_ok")),
        "holdout_leaks": len(o.get("leaks") or []),
        "ratings": labels.get("ratings", []),
    }


def aggregate(per_case: list[dict]) -> dict:
    """Headline metrics over a set of case_metrics rows."""
    n = len(per_case)
    tp, fp, fn = (sum(c[k] for c in per_case) for k in ("q_tp", "q_fp", "q_fn"))
    p, r, f = prf(tp, fp, fn)
    stp, sfp, sfn = (sum(c[k] for c in per_case) for k in ("s_tp", "s_fp", "s_fn"))
    sp, sr, sf = prf(stp, sfp, sfn)
    valid = sum(c["fp_valid_not_asked"] for c in per_case)
    adjudicated = sum(c["fp_valid_not_asked"] + c["fp_truly_unnecessary"] for c in per_case)
    fee_obs = [c for c in per_case if c["fee_observed"]]
    fee_both = [c for c in fee_obs if c["fee_abs_pct_error"] is not None]
    errs = [c["fee_abs_pct_error"] for c in fee_both]
    attempted = sum(c["drafts_attempted"] for c in per_case)
    ratings = [x["rating"] for c in per_case for x in c["ratings"]]
    first = [c for c in per_case if c["complete_first_letter"] is not None]
    return {
        "n_holdouts": n,
        "query_precision": _r(p), "query_recall": _r(r), "query_f1": _r(f), "query_tp": tp, "query_fp": fp, "query_fn": fn,
        "first_letter_completeness": _mean(c["first_letter_completeness"] for c in per_case),
        "office_first_letter_completeness": _mean(c["office_first_letter_completeness"] for c in per_case),
        "complete_first_letter_rate": _r(sum(1 for c in first if c["complete_first_letter"]) / len(first)) if first else None,
        "statutory_false_negatives": sum(len(c["statutory_false_negatives"]) for c in per_case),
        "adjudicated_precision": _r((tp + valid) / (tp + fp)) if adjudicated and tp + fp else None,
        "adjudications_done": adjudicated, "adjudications_pending": sum(c["fp_pending"] for c in per_case),
        "steps_precision": _r(sp), "steps_recall": _r(sr), "steps_f1": _r(sf),
        "kendall_tau": _mean(c["kendall_tau"] for c in per_case),
        "fee_coverage": _r(len(fee_both) / len(fee_obs)) if fee_obs else None,
        "fee_exact_match_rate": _r(sum(1 for c in fee_both if c["fee_exact"]) / len(fee_both)) if fee_both else None,
        "fee_mean_abs_pct_error": _mean(errs), "fee_median_abs_pct_error": _r(statistics.median(errs)) if errs else None,
        "p_at_5": _mean(c["p_at_5"] for c in per_case), "ndcg_at_5": _mean(c["ndcg_at_5"] for c in per_case),
        "relevance_label_coverage": _r(sum(c["relevance_labelled"] for c in per_case) / (5 * n)) if n else None,
        "proxy_ndcg_at_5": _mean(c["proxy_ndcg_at_5"] for c in per_case),
        "draft_render_ok_rate": _r(sum(c["drafts_ok"] for c in per_case) / attempted) if attempted else None,
        "drafts_complete_rate": _r(sum(c["drafts_complete"] for c in per_case) / attempted) if attempted else None,
        "leak_failures": sum(c["leak_failures"] for c in per_case) if attempted else None,
        "draft_rating_mean": _mean(ratings), "draft_ratings_n": len(ratings),
        "holdout_leaks": sum(c["holdout_leaks"] for c in per_case),
    }


STRATUM_METRICS = ("n_holdouts", "query_precision", "query_recall", "query_f1", "first_letter_completeness",
                   "statutory_false_negatives", "steps_f1", "kendall_tau", "fee_exact_match_rate", "fee_mean_abs_pct_error",
                   "p_at_5", "draft_render_ok_rate")


def by_stratum(outputs: list[dict], per_case: list[dict], dims: list[str]) -> dict:
    out: dict[str, dict] = {}
    for dim in dims:
        groups: dict[str, list[dict]] = {}
        for o, c in zip(outputs, per_case):
            groups.setdefault(str(o["stratum"].get(dim)), []).append(c)
        out[dim] = {v: {k: m[k] for k in STRATUM_METRICS} for v, cs in sorted(groups.items()) for m in [aggregate(cs)]}
    return out
