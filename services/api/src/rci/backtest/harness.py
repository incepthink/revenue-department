"""The 20-case backtest: replay each holdout as if it had just been filed and compare with what the office did.

Per holdout (role = holdout, approved card):
  mask         the approved card cut back to its filing stage (intel.tuning.filing_card: filing exhibits only, validators
               and requirements recomputed) — no query letter, no office document, no later fact
  time travel  analyse_case(as_of = the application date): only precedents decided by then, never a holdout (role
               filter), never the same society; each result is checked for all three (holdout_leaks)
  predict      gaps → query items, next steps, fee (fee world per data class), top-5 precedents, and — unless drafts: off —
               all four drafts in both render modes (render ok / leak check / complete)
  truth        from the approved card: every code the office queried (and those of its first letter), the events after
               filing, the premium actually paid / ordered / demanded, the rules' statutory requirements on the rule date
Scores (metrics.py) are re-computed with the officer labels current at read time, so adjudications, relevance labels
and draft ratings added after a run count without re-running it.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.adapters.llm import NullLLMClient
from rci.backtest import metrics as M
from rci.backtest.holdouts import BacktestConfig, holdout_cases, holdouts_hash, stratum_of
from rci.config import get_settings
from rci.db.models import Case, CaseCard, Clause, EvalCaseResult, EvalLabel, EvalRun
from rci.drafting.config import DRAFTING_VERSION, DraftingConfig
from rci.fees import golden
from rci.fees.prepare import AsrRow, calculate
from rci.fees.service import load_params
from rci.intel.config import IntelConfig
from rci.intel.profile import build_profile
from rci.intel.rank import jaccard
from rci.intel.service import RulesView, analyse_case, load_catalogs, same_society
from rci.intel.tuning import filing_card
from rci.pipeline.runner import audit, code_fingerprint

TEMPLATE_CODES = ("SCRUTINY_NOTE", "QUERY_LETTER", "DEMAND_LETTER", "COLLECTOR_ORDER")


class BacktestError(Exception):
    pass


# ------------------------------------------------------------------------------------------------
# Configuration pieces
# ------------------------------------------------------------------------------------------------
def intel_config(data_class: str, holdout_refs: set[str]) -> tuple[IntelConfig, list[str], bool]:
    """(config, warnings, tuning_leak). Tuned weights are used only when their recorded tuning set has no holdout."""
    cfg = IntelConfig.load(data_class=data_class)
    defaults = lambda: IntelConfig.load(tuned_path=get_settings().repo_root / "config" / "__no_tuned__.yaml")  # noqa: E731
    if not cfg.tuned:
        return cfg, [], False
    cases = cfg.tuned.get("cases")
    if cases is None:
        return defaults(), ["tuned weights do not record their tuning set — using the defaults (re-run `rci tune --save`)"], False
    leaked = sorted(set(cases) & holdout_refs)
    if leaked:
        return defaults(), [f"tuned weights were tuned on holdouts {leaked[:5]}{'…' if len(leaked) > 5 else ''} — using the "
                            "defaults; re-run `rci tune --save` after selecting holdouts"], True
    return cfg, [], False


def fee_world(name: str):
    """(stack_loader or None for the database, asr rows or None, label)."""
    if name == "synthetic_5pct":
        from rci.intel.synthetic import RATE_PCT, VILLAGES, asr_rate
        from rci.rules.resolve import RuleSetRow, SlabRow, build_stack

        sets = [RuleSetRow("SYN", "base", dt.date(2015, 1, 1), status="verified")]
        slabs = [SlabRow("SYN", "CHS", h, "residential", Decimal(RATE_PCT), "asr_land_value", status="verified")
                 for h in ("occupancy_class_2", "govt_lease")]
        asr = [AsrRow(y, d, v, asr_rate(r, y), status="verified") for v, _, d, r in VILLAGES for y in range(2015, 2031)]
        return (lambda on: build_stack(on, sets, slabs)), asr, name
    return None, None, "database"


# ------------------------------------------------------------------------------------------------
# Labels
# ------------------------------------------------------------------------------------------------
def load_labels(session: Session, case_ids: list[uuid.UUID]) -> dict[str, dict]:
    out: dict[str, dict] = {str(c): {"fp": {}, "relevance": {}, "ratings": []} for c in case_ids}
    rows = session.scalars(select(EvalLabel).where(EvalLabel.case_id.in_(case_ids)).order_by(EvalLabel.updated_at)).all()
    rel: dict[tuple[str, str], list[float]] = {}
    ratings: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        cid = str(r.case_id)
        if r.label_kind == "fp_adjudication" and r.value.get("verdict") in M.FP_VERDICTS:
            out[cid]["fp"][r.label_key] = r.value["verdict"]  # latest wins
        elif r.label_kind == "precedent_relevance" and r.value.get("relevance") is not None:
            rel.setdefault((cid, r.label_key), []).append(float(r.value["relevance"]))
        elif r.label_kind == "draft_rating" and r.value.get("rating"):
            ratings[(cid, r.label_key, str(r.labeller_id))] = r.value
    for (cid, target), vs in rel.items():
        out[cid]["relevance"][target] = sum(vs) / len(vs)
    for (cid, template, _), v in ratings.items():
        out[cid]["ratings"].append({"template": template, **v})
    return out


def score(outputs: list[dict], labels: dict[str, dict], dims: list[str]) -> tuple[dict, list[dict], dict]:
    per_case = [M.case_metrics(o, labels.get(o["case_id"], {})) for o in outputs]
    return M.aggregate(per_case), per_case, M.by_stratum(outputs, per_case, dims)


# ------------------------------------------------------------------------------------------------
# One holdout
# ------------------------------------------------------------------------------------------------
def _steps_truth(card: dict) -> list[str]:
    seen, out = set(), []
    for e in sorted(card.get("events") or [], key=lambda e: e["seq"]):
        if e["code"] != "APPLICATION_FILED" and e["code"] not in seen:
            seen.add(e["code"])
            out.append(e["code"])
    return out


def _first_letter(card: dict) -> list[str]:
    qs = sorted(card["facts"].get("queries") or [], key=lambda q: ((q.get("letter_date") or {}).get("value") or ""))
    if not qs:
        return []
    codes = {c for it in qs[0]["items"] for c in it.get("requirement_codes") or []}
    codes |= {r["code"] for r in card.get("requirements") or [] if r.get("was_queried_in") == qs[0]["exhibit_id"]}
    return sorted(codes)


def replay(session: Session, case: Case, card_row: CaseCard, ctx: dict) -> dict:
    card = card_row.card
    prof = build_profile(card)
    filed = prof.application_date
    masked = filing_card(session, case, card)
    a = analyse_case(session, case, card=masked, as_of=filed, store=False, explain_llm=False, today=filed,
                     cfg=ctx["intel"], rules=ctx["rules"], cats=ctx["cats"])
    q = build_profile(masked)
    leaks = []
    ids = [uuid.UUID(p["case_id"]) for p in a["precedents"]]
    roles = dict(session.execute(select(Case.id, Case.role).where(Case.id.in_(ids))).all()) if ids else {}
    for p in a["precedents"]:
        if roles.get(uuid.UUID(p["case_id"])) == "holdout":
            leaks.append({"kind": "holdout_in_pool", "case_ref": p["case_ref"]})
        if p.get("as_of") and dt.date.fromisoformat(p["as_of"]) > filed:
            leaks.append({"kind": "decided_after_filing", "case_ref": p["case_ref"]})
        pp = ctx["profiles"].get(p["card_id"]) or build_profile(session.get(CaseCard, uuid.UUID(p["card_id"])).card)
        ctx["profiles"][p["card_id"]] = pp
        if same_society(q, pp):
            leaks.append({"kind": "same_society", "case_ref": p["case_ref"]})
    predicted = [g for g in a["gaps"] if g["basis"] in ctx["include_bases"]]
    truth = sorted(prof.req_queried)
    rule_date = prof.rule_date(ctx["fee_params"].rule_date_basis) or filed
    statutory = sorted(ctx["rules"].statutory(rule_date, prof.tenure))
    # fee
    loader, asr, world = ctx["fee_world"]
    if loader is None:
        fee = a["fee"]
    else:
        fee = calculate(masked, ctx["fee_params"], loader, asr, today=filed).to_json()
    observed, source, _ = golden.observed_premium(card, ctx["fee_params"])
    # precedents: top five + proxy relevance (Jaccard of queried codes, as in tuning)
    tops = a["precedents"][:5]
    prof_of = lambda p: ctx["profiles"][p["card_id"]]  # noqa: E731
    proxy = [jaccard(set(truth), set(prof_of(p).req_queried), empty=0.0) for p in tops]
    ideal = sorted((jaccard(set(truth), set(prof_of(p).req_queried), empty=0.0) for p in a["precedents"]), reverse=True)
    proxy_ndcg = (M.dcg(proxy) / M.dcg(ideal[:5])) if truth and M.dcg(ideal[:5]) else None
    out = {
        "case_id": str(case.id), "case_ref": case.case_ref, "stratum": stratum_of(card, ctx["bcfg"]),
        "filing_date": filed.isoformat(), "rule_date": rule_date.isoformat(), "regime": a["regime"], "pool": a["pool"],
        "predicted": [g["code"] for g in predicted],
        "predicted_detail": [{k: g.get(k) for k in ("code", "name_mr", "name_en", "basis", "support", "n_applicable", "n_supporting",
                                                   "reported_because", "query_status")} for g in predicted],
        "excluded_gaps": [g["code"] for g in a["gaps"] if g not in predicted],
        "truth_queried": truth, "truth_first_letter": _first_letter(card), "statutory_codes": statutory,
        "steps_predicted": [s["code"] for s in a["steps"]["next"] if s["kind"] == "event"],
        "steps_checks": [s["code"] for s in a["steps"]["next"] if s["kind"] != "event"],
        "steps_truth": _steps_truth(card),
        "fee": {"world": world, "status": fee["status"], "predicted": fee["total"], "blockers": fee["blockers"],
                "observed": str(observed) if observed is not None else None, "observed_source": source},
        "top5": [{"case_id": p["case_id"], "case_ref": p["case_ref"], "score": p["score"], "society_name": p.get("society_name")}
                 for p in tops],
        "proxy_ndcg_at_5": round(proxy_ndcg, 4) if proxy_ndcg is not None else None,
        "leaks": leaks, "warnings": a["warnings"],
    }
    if ctx["drafts"] != "off":
        out["drafts"] = render_drafts(session, case, masked, a, filed, ctx)
    return out


def render_drafts(session: Session, case: Case, masked: dict, analysis: dict, filed: dt.date, ctx: dict) -> dict:
    from rci.drafting import service as drafting

    res: dict[str, dict] = {}
    try:
        inputs, _ = drafting.load_inputs(session, case, analysis=analysis, card=masked, today=filed, llm=NullLLMClient(),
                                         cfg=ctx["draft_cfg"], cats=ctx["draft_cats"], rules=ctx["draft_rules"])
    except Exception as e:  # noqa: BLE001 — recorded per template
        return {c: {"render_ok": False, "error": f"inputs: {e}"} for c in TEMPLATE_CODES}
    own = drafting.own_token_checker(session, case.id)
    for code in TEMPLATE_CODES:
        try:
            r = drafting.render_template(inputs, code, own)
            issue = r["issue"]
            res[code] = {"render_ok": True, "leak_ok": issue["leak"]["ok"], "leaks": issue["leak"]["found"][:5],
                         "complete": issue["model"].complete, "missing": issue["model"].missing_slots,
                         "paragraphs": len(issue["paragraphs"])}
        except Exception as e:  # noqa: BLE001
            res[code] = {"render_ok": False, "error": str(e)[:300]}
    if ctx["drafts"] == "store" and ctx.get("run_id"):
        drafting.generate(session, case, analysis=analysis, card=masked, run_id=ctx["run_id"], today=filed, llm=NullLLMClient())
    return res


# ------------------------------------------------------------------------------------------------
# The run
# ------------------------------------------------------------------------------------------------
def run_backtest(session: Session, *, data_class: str = "synthetic", prefix: str | None = None, drafts: str | None = None,
                 save: bool = True, by: str = "cli", bcfg: BacktestConfig | None = None) -> dict:
    from rci.drafting import service as drafting

    bcfg = bcfg or BacktestConfig.load()
    hold = holdout_cases(session, data_class, prefix)
    if not hold:
        raise BacktestError(f"no holdouts for data class {data_class!r} — run `rci holdouts select` first")
    refs = [c.case_ref for c, _ in hold]
    intel, warnings, tuning_leak = intel_config(data_class, set(refs))
    dcfg = DraftingConfig.load()
    drafts = drafts or bcfg.raw.get("drafts", "check")
    ctx = {"bcfg": bcfg, "intel": intel, "rules": RulesView(session), "cats": load_catalogs(session), "fee_params": load_params(),
           "fee_world": fee_world((bcfg.raw.get("fee_world") or {}).get(data_class, "database")),
           "include_bases": set(dcfg.value("query_letter.include_bases", ["STATUTORY", "PRACTICE", "PRACTICE_OLD_RULES"])),
           "drafts": drafts, "draft_cfg": dcfg, "draft_cats": drafting.catalogs(session), "draft_rules": drafting.rule_sets(session),
           "profiles": {}}
    run = None
    if save:
        run = EvalRun(kind="backtest", status="running", code_version=code_fingerprint(), config={})
        session.add(run)
        session.flush()
        ctx["run_id"] = run.id
    outputs, errors = [], []
    for case, cc in hold:
        try:
            with session.begin_nested():
                outputs.append(replay(session, case, cc, ctx))
        except Exception as e:  # noqa: BLE001 — one broken holdout must not hide the others
            errors.append({"case_ref": case.case_ref, "error": f"{type(e).__name__}: {e}"[:500]})
    holdout_ids = {str(c.id) for c, _ in hold}
    clause_leaks = [str(c.id) for c in session.scalars(select(Clause).where(Clause.status == "canonical"))
                    if set(c.source_case_ids or []) & holdout_ids]
    if clause_leaks:
        warnings.append(f"{len(clause_leaks)} canonical clause(s) cite a holdout as source — re-mine / re-curate after selection")
    dims = bcfg.raw.get("report_by", ["tenure", "era"])
    labels = load_labels(session, [c.id for c, _ in hold])
    metrics, per_case, strata = score(outputs, labels, dims)
    metrics.update(tuning_leak=int(tuning_leak), clause_leaks=len(clause_leaks), errors=len(errors))
    config = {"data_class": data_class, "prefix": prefix, "holdouts": refs, "holdouts_hash": holdouts_hash(refs),
              "intel": {"source": intel.source(), "fingerprint": intel.fingerprint(), "support_threshold": intel.support_threshold,
                        "weights": intel.weights}, "fee_world": ctx["fee_world"][2], "drafts": drafts,
              "drafting_version": f"{DRAFTING_VERSION}+{dcfg.fingerprint()}", "include_bases": sorted(ctx["include_bases"]),
              "report_by": dims, "by": by}
    result = {"run_id": str(run.id) if run else None, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "code_version": code_fingerprint(), "config": config, "metrics": metrics, "strata": strata, "warnings": warnings,
              "errors": errors, "cases": [{**o, "metrics": m} for o, m in zip(outputs, per_case)]}
    if run is not None:
        run.config = config
        run.metrics = {"metrics": metrics, "strata": strata, "warnings": warnings, "errors": errors}
        run.status, run.finished_at = "done", dt.datetime.now(dt.timezone.utc)
        for o, m in zip(outputs, per_case):
            session.add(EvalCaseResult(run_id=run.id, case_id=uuid.UUID(o["case_id"]),
                                       stratum="|".join(str(o["stratum"].get(d)) for d in dims), metrics=m, outputs=o))
        session.flush()
        audit(session, "backtest.run", actor_kind="user" if by != "cli" else "cli", entity_type="eval_run", entity_id=run.id,
              holdouts=len(refs), query_f1=metrics["query_f1"], errors=len(errors))
    return result


def load_run(session: Session, run_id: uuid.UUID, *, rescore: bool = True) -> dict:
    """A stored run, re-scored with the labels current now (metrics_at_run keeps what the run itself saw)."""
    run = session.get(EvalRun, run_id)
    if run is None or run.kind != "backtest":
        raise BacktestError("backtest run not found")
    rows = session.scalars(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id)).all()
    outputs = sorted((r.outputs for r in rows), key=lambda o: o["case_ref"])
    stored = run.metrics or {}
    dims = run.config.get("report_by", ["tenure", "era"])
    if rescore and outputs:
        labels = load_labels(session, [uuid.UUID(o["case_id"]) for o in outputs])
        metrics, per_case, strata = score(outputs, labels, dims)
        for k in ("tuning_leak", "clause_leaks", "errors"):
            metrics[k] = stored.get("metrics", {}).get(k)
    else:
        metrics, strata = stored.get("metrics", {}), stored.get("strata", {})
        per_case = [r.metrics for r in sorted(rows, key=lambda r: r.outputs["case_ref"])]
    return {"run_id": str(run.id), "created_at": run.created_at.isoformat() if run.created_at else None,
            "code_version": run.code_version, "config": run.config, "metrics": metrics, "metrics_at_run": stored.get("metrics"),
            "strata": strata, "warnings": stored.get("warnings", []), "errors": stored.get("errors", []),
            "report_blob_key": run.report_blob_key, "cases": [{**o, "metrics": m} for o, m in zip(outputs, per_case)]}


def write_reports(session: Session, result: dict, out_dir: Path | None = None, gate: dict | None = None) -> dict:
    from rci.backtest.report import html, markdown
    from rci.storage import get_blob_store

    md, page = markdown(result, gate), html(result, gate)
    paths = {}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"backtest_{result['config']['data_class']}"
        (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")
        (out_dir / f"{stem}.html").write_text(page, encoding="utf-8")
        paths = {"md": str(out_dir / f"{stem}.md"), "html": str(out_dir / f"{stem}.html")}
    if result.get("run_id"):
        blobs = get_blob_store()
        key = f"reports/backtest/{result['run_id']}"
        blobs.put("out", f"{key}.md", md.encode("utf-8"), "text/markdown; charset=utf-8")
        blobs.put("out", f"{key}.html", page.encode("utf-8"), "text/html; charset=utf-8")
        run = session.get(EvalRun, uuid.UUID(result["run_id"]))
        run.report_blob_key = f"{key}.html"
        if gate is not None:
            run.metrics = {**(run.metrics or {}), "gate": gate}
        session.flush()
    return paths
