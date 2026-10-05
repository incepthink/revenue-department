"""rci command line.

  rci migrate                      alembic upgrade head
  rci seed                         load catalogs + pilot office (idempotent)
  rci gen-keys                     print fresh vault keys for .env
  rci init-env                     create .env from .env.example with fresh keys; on an existing .env only add missing keys
  rci ingest <folder> [...]        one case per sub-folder (or the folder itself), run the pipeline, write card JSON
  rci export-schemas               regenerate packages/schemas/extraction/*.json from the field specs
  rci create-user <email> <role>   add a reviewer / officer / admin (password from --password or prompted)
  rci asr-import <file.csv>        validate an ASR CSV (dry run); --commit to import, --replace to overwrite same keys
  rci rules-gaps [--on DATE]       active CHS premium slabs that are not usable (to_verify / rate missing)
  rci report extraction|agreement  field-level extraction accuracy / double-review agreement (--save, --out FILE.md)
  rci seed-precedents [--n 40]     synthetic approved precedent pool (data_class=synthetic); --delete removes it
  rci analyse <case_ref> [...]     precedents, gaps, next steps and fee trace for a case (stored; --out FILE.json)
  rci fee-golden [--out FILE.md]   recompute premiums of approved precedents vs their challans; exit 1 if any is unexplained
  rci tune [--save]                leave-one-out tuning of SUPPORT_THRESHOLD + rerank weights on precedents (never holdouts)
  rci build-templates              regenerate config/templates/*.docx (layout in drafting/docxbuild.py, font from drafting.yaml)
  rci clauses mine|seed|list|slots clause library: mine precedent letters / orders, load seed wording, list, slot catalogue
  rci draft <case_ref> [...]       the four Marathi drafts (internal + issue) from the case's latest analysis (--store: DB + blobs)
  rci holdouts select|list|release stratified 20-case holdout selection (locks role = holdout)
  rci backtest [--gate]            replay the holdouts at filing stage; report; --gate compares with eval/backtest/baseline.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import zipfile
from pathlib import Path

from rci.config import get_settings


def _configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def cmd_migrate(args) -> int:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(get_settings().repo_root / "services" / "api" / "alembic.ini"))
    if args.database_url:
        cfg.attributes["url"] = args.database_url
    command.upgrade(cfg, "head")
    return 0


def cmd_seed(args) -> int:
    from rci.db.session import session_scope
    from rci.seed import seed_all

    with session_scope(args.database_url) as s:
        print(json.dumps(seed_all(s)))
    return 0


def cmd_gen_keys(args) -> int:
    from rci.pipeline.redact.vault import generate_keys

    for k, v in generate_keys().items():
        print(f"{k}={v}")
    return 0


def cmd_init_env(args) -> int:
    from rci.pipeline.redact.vault import generate_keys

    root = get_settings().repo_root
    target = root / ".env"
    if target.exists():
        text = target.read_text(encoding="utf-8")
        present = {line.split("=", 1)[0].strip() for line in text.splitlines() if "=" in line and not line.lstrip().startswith("#")}
        missing = {k: v for k, v in generate_keys().items() if k not in present}
        if not missing:
            print(".env exists and has every key; leaving it unchanged")
            return 0
        with target.open("a", encoding="utf-8") as f:
            f.write("\n# added by `rci init-env`\n" + "".join(f"{k}={v}\n" for k, v in missing.items()))
        print(f"added {sorted(missing)} to {target}")
        return 0
    text = (root / ".env.example").read_text(encoding="utf-8")
    for k, v in generate_keys().items():
        text = text.replace(f"{k}=REPLACE_WITH_gen-keys_OUTPUT", f"{k}={v}")
    target.write_text(text, encoding="utf-8")
    print(f"created {target}")
    return 0


def _case_folders(root: Path) -> list[Path]:
    from rci.pipeline.runner import SUPPORTED

    def has_files(p: Path) -> bool:
        return any(f.is_file() and (f.suffix.lower() in SUPPORTED or f.suffix.lower() == ".zip") for f in p.iterdir())

    if has_files(root):
        return [root]
    return sorted(p for p in root.iterdir() if p.is_dir() and has_files(p))


def _files(folder: Path) -> list[tuple[str, bytes]]:
    from rci.pipeline.runner import SUPPORTED

    out = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in SUPPORTED:
            out.append((f.name, f.read_bytes()))
        elif f.suffix.lower() == ".zip":
            with zipfile.ZipFile(f) as z:
                for info in sorted(z.infolist(), key=lambda i: i.filename):
                    if not info.is_dir() and Path(info.filename).suffix.lower() in SUPPORTED:
                        out.append((f"{f.name}::{info.filename}", z.read(info)))
    return out


def cmd_ingest(args) -> int:
    from rci.db.session import session_scope
    from rci.pipeline.runner import PipelineContext, add_document, get_or_create_case, latest_card, process_case

    root = Path(args.folder)
    if not root.is_dir():
        print(f"not a folder: {root}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else get_settings().output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext.default()
    failures = 0
    for folder in _case_folders(root):
        case_ref = folder.name
        try:
            with session_scope(args.database_url) as s:
                case, created = get_or_create_case(s, case_ref=case_ref, data_class=args.data_class,
                                                   office_code=args.office, actor_kind="cli")
                added = 0
                for name, data in _files(folder):
                    _, new = add_document(s, ctx.blobs, case, name, data, actor_kind="cli")
                    added += int(new)
                summary = process_case(s, case.id, ctx, force=args.force, actor_kind="cli")
                card = latest_card(s, case.id)
                if card:
                    (out_dir / f"{case_ref}.card.json").write_text(json.dumps(card.card, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"case_ref": case_ref, "case_created": created, "documents_added": added, **summary.__dict__},
                             ensure_ascii=False, default=str))
        except Exception as e:  # keep going with the other dockets
            failures += 1
            logging.exception("ingest failed for %s", case_ref)
            print(json.dumps({"case_ref": case_ref, "error": str(e)}), file=sys.stderr)
    return 1 if failures else 0


def cmd_export_schemas(args) -> int:
    from rci.pipeline.extract.extractor import extraction_schema
    from rci.pipeline.extract.fields import FIELDS

    out = get_settings().schemas_dir / "extraction"
    out.mkdir(parents=True, exist_ok=True)
    for t in FIELDS:
        (out / f"{t}.json").write_text(json.dumps(extraction_schema(t), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(FIELDS)} schemas to {out}")
    return 0


def cmd_create_user(args) -> int:
    import getpass

    from rci.auth import create_user
    from rci.db.session import session_scope

    password = args.password or getpass.getpass("password (min 8 chars): ")
    with session_scope(args.database_url) as s:
        try:
            u = create_user(s, email=args.email, display_name=args.name or args.email.split("@")[0], role=args.role,
                            password=password)
        except ValueError as e:
            if args.exist_ok and "exists" in str(e):
                print(f"{args.email} exists; unchanged")
                return 0
            print(str(e), file=sys.stderr)
            return 1
        print(json.dumps({"id": str(u.id), "email": u.email, "role": u.role}))
    return 0


def cmd_asr_import(args) -> int:
    from rci.asr import import_asr
    from rci.db.session import session_scope

    with session_scope(args.database_url) as s:
        report = import_asr(s, Path(args.file).read_bytes(), dry_run=not args.commit, replace=args.replace)
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2, default=str))
    return 0 if report.ok else 1


def cmd_rules_gaps(args) -> int:
    import datetime as dt

    from rci.db.session import session_scope
    from rci.rules import verification_gaps

    on = dt.date.fromisoformat(args.on) if args.on else dt.date.today()
    with session_scope(args.database_url) as s:
        gaps = verification_gaps(s, on)
    print(json.dumps({"on": on.isoformat(), "active_chs_slabs_unverified": len(gaps),
                      "gaps": [{k: g[k] for k in ("rule_set_code", "seed_key", "holding_basis", "land_use", "rate_pct", "reasons")}
                               for g in gaps]}, ensure_ascii=False, indent=2))
    return 0


def cmd_report(args) -> int:
    from rci.db.session import session_scope
    from rci.review import service

    kind = "extraction_accuracy" if args.kind == "extraction" else "agreement"
    with session_scope(args.database_url) as s:
        m = service.extraction_accuracy(s) if kind == "extraction_accuracy" else service.agreement_report(s)
        if args.save:
            run = service.save_report(s, kind, m, {"by": "cli"})
            print(f"saved eval_run {run.id}", file=sys.stderr)
    md = service.report_markdown(kind, m)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    print(md)
    return 0


def _case_by_ref(s, ref: str, office: str = "PILOT"):
    from sqlalchemy import select

    from rci.db.models import Case, Office

    case = s.scalar(select(Case).join(Office, Case.office_id == Office.id).where(Case.case_ref == ref, Office.code == office))
    if case is None:
        raise SystemExit(f"case {ref!r} not found in office {office}")
    return case


def cmd_seed_precedents(args) -> int:
    from rci.db.session import session_scope
    from rci.intel.synthetic import delete_pool, generate_pool

    with session_scope(args.database_url) as s:
        if args.delete:
            print(json.dumps({"deleted": delete_pool(s, args.prefix)}))
            return 0
        out = generate_pool(s, args.n, seed=args.seed, prefix=args.prefix)
    print(json.dumps({"created": len(out["created"]), "skipped": len(out["skipped"]), "prefix": args.prefix}))
    return 0


def cmd_analyse(args) -> int:
    import datetime as dt

    from rci.db.session import session_scope
    from rci.intel.service import analyse_case

    with session_scope(args.database_url) as s:
        case = _case_by_ref(s, args.case_ref, args.office)
        r = analyse_case(s, case, as_of=dt.date.fromisoformat(args.as_of) if args.as_of else None, store=not args.no_store,
                         explain_llm=False if args.no_llm else None)
    if args.out:
        Path(args.out).write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    brief = {
        "case_ref": r["case"]["case_ref"], "analysis_id": r.get("analysis_id"), "as_of": r["as_of"], "rule_date": r["rule_date"],
        "regime": r["regime"], "pool": r["pool"], "engine": {k: r["engine"][k] for k in ("intel", "fees", "weights_source", "embedding_model")},
        "precedents": [f"{p['rank']}. {p['case_ref']} {p['score']:.3f} ({p['regime_label']})" for p in r["precedents"]],
        "gaps": [f"{g['code']} [{g['basis']}] {g['query_status']} support={g['support']} queried_in={len(g['was_queried_in'])}"
                 for g in r["gaps"]],
        "next_steps": [f"{st['code']} [{st['basis']}] freq={st['frequency']} "
                       f"{'triggers=' + ','.join(t['code'] for t in st['triggers']) if st['triggers'] else ''}".strip()
                       for st in r["steps"]["next"]],
        "fee": {k: r["fee"][k] for k in ("status", "total", "blockers", "unverified")},
        "warnings": r["warnings"],
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_fee_golden(args) -> int:
    from rci.db.session import session_scope
    from rci.fees import golden
    from rci.review.service import save_report

    with session_scope(args.database_url) as s:
        r = golden.precedent_report(s)
        if args.save:
            run = save_report(s, "fee_golden", r, {"by": "cli"})
            print(f"saved eval_run {run.id}", file=sys.stderr)
    md = golden.report_markdown(r)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    print(md)
    return 1 if r["unexplained"] else 0


def cmd_tune(args) -> int:
    from rci.db.session import session_scope
    from rci.intel import tuning

    with session_scope(args.database_url) as s:
        r = tuning.tune(s, data_class=args.data_class)
        if args.save:
            run = tuning.save(s, r)
            print(f"saved eval_run {run.id} and {get_settings().intel_tuned_file}", file=sys.stderr)
    md = tuning.report_markdown(r)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    print(md)
    return 0 if r["n_queries"] else 1


def cmd_build_templates(args) -> int:
    from rci.drafting.config import DraftingConfig
    from rci.drafting.docxbuild import build_all

    cfg = DraftingConfig.load()
    for p in build_all(cfg.templates_dir, str(cfg.value("font", "Nirmala UI")), float(cfg.value("font_size_pt", 12))):
        print(p)
    return 0


def cmd_clauses(args) -> int:
    from sqlalchemy import select

    from rci.db.models import Clause
    from rci.db.session import session_scope
    from rci.drafting import clauses as cl

    if args.action == "slots":
        for name, (desc, ex) in {**cl.SLOTS, **cl.ITEM_SLOTS}.items():
            print(f"{name:20} {desc}  (e.g. {ex})")
        return 0
    with session_scope(args.database_url) as s:
        if args.action == "seed":
            print(json.dumps({"seeded": cl.seed_clauses(s)}))
        elif args.action == "mine":
            print(json.dumps(cl.mine(s, data_class=args.data_class), ensure_ascii=False, indent=2))
        else:
            q = select(Clause).order_by(Clause.kind, Clause.clause_key, Clause.status)
            if args.kind:
                q = q.where(Clause.kind == args.kind)
            if args.status:
                q = q.where(Clause.status == args.status)
            for c in s.scalars(q):
                print(f"{c.status:9} {c.origin:7} {c.data_class:9} {c.kind:15} {c.clause_key:40} n={c.member_count:<3} "
                      f"cl={c.cluster_id or '-'!s:<3} {(c.template or c.text_mr)[:90]}")
    return 0


def cmd_draft(args) -> int:
    from rci.db.session import session_scope
    from rci.drafting import service

    out_dir = Path(args.out) if args.out else get_settings().output_dir / "drafts"
    out_dir.mkdir(parents=True, exist_ok=True)
    codes = args.template or None
    with session_scope(args.database_url) as s:
        case = _case_by_ref(s, args.case_ref, args.office)
        if args.store:
            res = service.generate(s, case, templates=codes)
            print(json.dumps([{k: r[k] for k in ("template_code", "version", "status")} | {"warnings": r["issue"]["warnings"]}
                              for r in res], ensure_ascii=False, indent=2, default=str))
            return 0
        inputs, warnings = service.load_inputs(s, case)
        own = service.own_token_checker(s, case.id)
        for code in codes or list(service.TEMPLATES):
            r = service.render_template(inputs, code, own)
            for mode, x in r.items():
                path = out_dir / f"{args.case_ref}_{code}_{mode}.docx"
                path.write_bytes(x["data"])
                m = x["model"]
                print(json.dumps({"file": str(path), "complete": m.complete, "missing": m.missing_slots, "warnings": warnings + m.warnings,
                                  "leak_check": x["leak"], "crosscheck": m.crosscheck}, ensure_ascii=False))
    return 0


def cmd_holdouts(args) -> int:
    from rci.backtest import holdouts
    from rci.db.session import session_scope

    with session_scope(args.database_url) as s:
        if args.action == "select":
            r = holdouts.select_holdouts(s, data_class=args.data_class, n=args.n, seed=args.seed, prefix=args.prefix)
        elif args.action == "release":
            if not args.yes:
                print("releasing holdouts changes the backtest set; pass --yes to confirm", file=sys.stderr)
                return 2
            r = {"released": holdouts.release_holdouts(s, data_class=args.data_class, prefix=args.prefix)}
        else:
            refs = [c.case_ref for c, _ in holdouts.holdout_cases(s, args.data_class, args.prefix)]
            r = {"data_class": args.data_class, "holdouts": refs, "holdouts_hash": holdouts.holdouts_hash(refs)}
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_backtest(args) -> int:
    from rci.backtest import gate as G
    from rci.backtest.harness import BacktestError, run_backtest, write_reports
    from rci.backtest.holdouts import BacktestConfig
    from rci.db.session import session_scope

    bcfg = BacktestConfig.load()
    with session_scope(args.database_url) as s:
        try:
            result = run_backtest(s, data_class=args.data_class, prefix=args.prefix, drafts=args.drafts, save=not args.no_save, bcfg=bcfg)
        except BacktestError as e:
            print(str(e), file=sys.stderr)
            return 2
        if args.update_baseline:
            print(f"wrote {G.write_baseline(result, bcfg.gate)}", file=sys.stderr)
        verdict = G.evaluate(result, G.load_baseline(bcfg.gate), bcfg.gate) if (args.gate or args.update_baseline) else None
        paths = write_reports(s, result, Path(args.out_dir) if args.out_dir else get_settings().output_dir / "backtest", verdict)
    m = result["metrics"]
    brief = {"run_id": result["run_id"], "holdouts": m["n_holdouts"], "reports": paths,
             **{k: m.get(k) for k in ("query_precision", "query_recall", "query_f1", "first_letter_completeness", "statutory_false_negatives",
                                      "steps_f1", "kendall_tau", "fee_coverage", "fee_exact_match_rate", "fee_mean_abs_pct_error",
                                      "p_at_5", "proxy_ndcg_at_5", "draft_render_ok_rate", "leak_failures", "holdout_leaks",
                                      "tuning_leak", "errors")},
             "warnings": result["warnings"], "errors": result["errors"]}
    print(json.dumps(brief, ensure_ascii=False, indent=2, default=str))
    if verdict is not None:
        for c in verdict["checks"]:
            print(f"  [{c['status']:7}] {c['check']:28} {c['value']!s:>10}  {c['rule']}  {c.get('reason', '')}", file=sys.stderr)
        print(f"GATE {'PASS' if verdict['passed'] else 'FAIL'}" + (f": {', '.join(verdict['failed'])}" if verdict["failed"] else ""),
              file=sys.stderr)
        if args.gate and not verdict["passed"]:
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="rci")
    p.add_argument("--database-url", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)
    sub.add_parser("gen-keys").set_defaults(fn=cmd_gen_keys)
    sub.add_parser("init-env").set_defaults(fn=cmd_init_env)
    sub.add_parser("export-schemas").set_defaults(fn=cmd_export_schemas)
    ing = sub.add_parser("ingest", help="ingest a folder of dockets")
    ing.add_argument("folder")
    ing.add_argument("--data-class", choices=["real", "synthetic"], default="real",
                     help="real (default, strictest routing) or synthetic (sample/test documents)")
    ing.add_argument("--office", default="PILOT")
    ing.add_argument("--force", action="store_true", help="ignore step caches and recompute")
    ing.add_argument("--out", default=None, help="where to write <case_ref>.card.json (default var/out)")
    ing.set_defaults(fn=cmd_ingest)
    cu = sub.add_parser("create-user", help="add a user")
    cu.add_argument("email")
    cu.add_argument("role", choices=["reviewer", "officer", "admin"])
    cu.add_argument("--name", default=None)
    cu.add_argument("--password", default=None, help="omit to be prompted")
    cu.add_argument("--exist-ok", action="store_true")
    cu.set_defaults(fn=cmd_create_user)
    asr = sub.add_parser("asr-import", help="validate / import an ASR CSV")
    asr.add_argument("file")
    asr.add_argument("--commit", action="store_true", help="import (default: dry run)")
    asr.add_argument("--replace", action="store_true", help="replace rows with the same key")
    asr.set_defaults(fn=cmd_asr_import)
    rg = sub.add_parser("rules-gaps", help="unverified active CHS slabs")
    rg.add_argument("--on", default=None, help="YYYY-MM-DD (default today)")
    rg.set_defaults(fn=cmd_rules_gaps)
    rp = sub.add_parser("report", help="extraction accuracy / agreement report")
    rp.add_argument("kind", choices=["extraction", "agreement"])
    rp.add_argument("--save", action="store_true", help="store as an eval_runs row")
    rp.add_argument("--out", default=None)
    rp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("seed-precedents", help="synthetic approved precedent pool (dev / tests)")
    sp.add_argument("--n", type=int, default=40)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--prefix", default="SYN-P")
    sp.add_argument("--delete", action="store_true", help="delete the pool with this prefix instead")
    sp.set_defaults(fn=cmd_seed_precedents)
    an = sub.add_parser("analyse", help="analyse a case: precedents, gaps, next steps, fee")
    an.add_argument("case_ref")
    an.add_argument("--office", default="PILOT")
    an.add_argument("--as-of", default=None, help="time-travel date for the precedent pool (default today)")
    an.add_argument("--no-store", action="store_true")
    an.add_argument("--no-llm", action="store_true", help="template explanations only")
    an.add_argument("--out", default=None, help="write the full result JSON here")
    an.set_defaults(fn=cmd_analyse)
    fg = sub.add_parser("fee-golden", help="fee engine vs precedent challans, every mismatch classified")
    fg.add_argument("--save", action="store_true", help="store as an eval_runs row")
    fg.add_argument("--out", default=None)
    fg.set_defaults(fn=cmd_fee_golden)
    tu = sub.add_parser("tune", help="leave-one-out tuning on precedents")
    tu.add_argument("--save", action="store_true", help="write config/intel_tuned.yaml + an eval_runs row")
    tu.add_argument("--data-class", choices=["real", "synthetic"], default=None, help="restrict to one data class")
    tu.add_argument("--out", default=None)
    tu.set_defaults(fn=cmd_tune)
    sub.add_parser("build-templates", help="regenerate the docx templates").set_defaults(fn=cmd_build_templates)
    clp = sub.add_parser("clauses", help="clause library")
    clp.add_argument("action", choices=["mine", "seed", "list", "slots"])
    clp.add_argument("--data-class", choices=["real", "synthetic"], default=None)
    clp.add_argument("--kind", default=None)
    clp.add_argument("--status", choices=["candidate", "canonical", "rejected"], default=None)
    clp.set_defaults(fn=cmd_clauses)
    dr = sub.add_parser("draft", help="render the four drafts of a case")
    dr.add_argument("case_ref")
    dr.add_argument("--office", default="PILOT")
    dr.add_argument("--template", action="append", choices=["SCRUTINY_NOTE", "QUERY_LETTER", "DEMAND_LETTER", "COLLECTOR_ORDER"])
    dr.add_argument("--out", default=None, help="folder for the .docx files (default var/out/drafts)")
    dr.add_argument("--store", action="store_true", help="store as draft versions (DB + blob store) instead of writing files")
    dr.set_defaults(fn=cmd_draft)
    ho = sub.add_parser("holdouts", help="backtest holdout set")
    ho.add_argument("action", choices=["select", "list", "release"])
    ho.add_argument("--data-class", choices=["real", "synthetic"], default="real")
    ho.add_argument("--n", type=int, default=None)
    ho.add_argument("--seed", type=int, default=None)
    ho.add_argument("--prefix", default=None, help="only cases whose ref starts with this")
    ho.add_argument("--yes", action="store_true")
    ho.set_defaults(fn=cmd_holdouts)
    bt = sub.add_parser("backtest", help="backtest on the holdouts")
    bt.add_argument("--data-class", choices=["real", "synthetic"], default="real")
    bt.add_argument("--prefix", default=None)
    bt.add_argument("--drafts", choices=["check", "store", "off"], default=None)
    bt.add_argument("--no-save", action="store_true")
    bt.add_argument("--gate", action="store_true", help="exit 1 when the gate fails")
    bt.add_argument("--update-baseline", action="store_true", help="write this run as eval/backtest/baseline.json")
    bt.add_argument("--out-dir", default=None)
    bt.set_defaults(fn=cmd_backtest)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
