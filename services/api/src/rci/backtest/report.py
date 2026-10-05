"""Backtest report: Markdown (CI logs, PR comments) and a self-contained HTML page (stored per run, served by the API)."""
from __future__ import annotations

import html as _html

HEADLINE = [
    ("Queries", [("query_precision", "precision"), ("query_recall", "recall"), ("query_f1", "F1"),
                 ("first_letter_completeness", "first-letter completeness"),
                 ("office_first_letter_completeness", "office's own first letter"), ("complete_first_letter_rate", "complete first letter"),
                 ("statutory_false_negatives", "statutory false negatives"), ("adjudicated_precision", "adjudicated precision"),
                 ("adjudications_pending", "FP adjudications pending")]),
    ("Next steps", [("steps_precision", "precision"), ("steps_recall", "recall"), ("steps_f1", "F1"), ("kendall_tau", "Kendall τ")]),
    ("Fee", [("fee_coverage", "coverage"), ("fee_exact_match_rate", "exact-match rate"), ("fee_mean_abs_pct_error", "mean abs % error"),
             ("fee_median_abs_pct_error", "median abs % error")]),
    ("Precedents", [("p_at_5", "P@5 (officer labels)"), ("ndcg_at_5", "nDCG@5 (officer labels)"),
                    ("relevance_label_coverage", "label coverage"), ("proxy_ndcg_at_5", "proxy nDCG@5 (not gated)")]),
    ("Drafts", [("draft_render_ok_rate", "render ok"), ("drafts_complete_rate", "complete (no blanks)"), ("leak_failures", "leak failures"),
                ("draft_rating_mean", "officer rating (1–5)"), ("draft_ratings_n", "ratings")]),
    ("Integrity", [("n_holdouts", "holdouts"), ("holdout_leaks", "holdout / time-travel / same-society leaks"),
                   ("tuning_leak", "tuning leak"), ("clause_leaks", "clauses citing a holdout"), ("errors", "replay errors")]),
]


def fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _gate_rows(gate: dict | None) -> list[tuple]:
    return [(c["check"], fmt(c["value"]), c["rule"], c["status"], c.get("reason", "")) for c in (gate or {}).get("checks", [])]


def _case_rows(result: dict) -> list[tuple]:
    rows = []
    for c in result["cases"]:
        m, f = c["metrics"], c["fee"]
        rows.append((c["case_ref"], " / ".join(str(v) for v in c["stratum"].values()), ", ".join(c["truth_queried"]) or "—",
                     ", ".join(c["predicted"]) or "—", f"{fmt(m['query_precision'])} / {fmt(m['query_recall'])}",
                     ", ".join(m["statutory_false_negatives"]) or "—", fmt(m["kendall_tau"]),
                     f"{f['predicted'] or '—'} / {f['observed'] or '—'}", fmt(m["fee_abs_pct_error"]),
                     f"{m['drafts_ok']}/{m['drafts_attempted']}" + (f" ({m['leak_failures']} leak)" if m["leak_failures"] else "")))
    return rows


CASE_HEAD = ("holdout", "stratum", "office queried", "tool predicted", "P / R", "statutory FN", "τ", "fee pred / obs", "fee err %", "drafts")


def markdown(result: dict, gate: dict | None = None) -> str:
    c, m = result["config"], result["metrics"]
    out = [f"# Backtest — {c['data_class']} ({m['n_holdouts']} holdouts)", "",
           f"Run `{result.get('run_id') or 'unsaved'}` · {result.get('created_at', '')} · code `{result.get('code_version')}` · "
           f"weights: {c['intel']['source']} (`{c['intel']['fingerprint']}`) · fee world: {c['fee_world']} · drafts: {c['drafts']} · "
           f"holdout set `{c['holdouts_hash']}`", ""]
    if gate is not None:
        out += [f"## Gate: {'PASS' if gate['passed'] else 'FAIL'}" + (f" — failed: {', '.join(gate['failed'])}" if gate["failed"] else ""),
                "", "| check | value | rule | status | note |", "|---|---|---|---|---|"]
        out += [f"| {a} | {b} | {r} | {s} | {n} |" for a, b, r, s, n in _gate_rows(gate)]
        out.append("")
    for title, keys in HEADLINE:
        out += [f"## {title}", "", "| metric | value |", "|---|---|"] + [f"| {label} | {fmt(m.get(k))} |" for k, label in keys] + [""]
    for dim, groups in (result.get("strata") or {}).items():
        out += [f"## By {dim}", "", "| " + dim + " | n | P | R | F1 | first-letter | stat. FN | steps F1 | τ | fee exact | fee err % |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for v, s in groups.items():
            out.append(f"| {v} | {s['n_holdouts']} | {fmt(s['query_precision'])} | {fmt(s['query_recall'])} | {fmt(s['query_f1'])} | "
                       f"{fmt(s['first_letter_completeness'])} | {s['statutory_false_negatives']} | {fmt(s['steps_f1'])} | "
                       f"{fmt(s['kendall_tau'])} | {fmt(s['fee_exact_match_rate'])} | {fmt(s['fee_mean_abs_pct_error'])} |")
        out.append("")
    out += ["## Per holdout", "", "| " + " | ".join(CASE_HEAD) + " |", "|" + "---|" * len(CASE_HEAD)]
    out += ["| " + " | ".join(r) + " |" for r in _case_rows(result)]
    if result.get("warnings") or result.get("errors"):
        out += ["", "## Warnings / errors", ""] + [f"- {w}" for w in result.get("warnings", [])] + \
               [f"- **{e['case_ref']}**: {e['error']}" for e in result.get("errors", [])]
    out += ["", "Holdouts replayed at their filing stage (filing exhibits only), precedents limited to those decided before the "
            "application date, same society and holdouts excluded. Label-based metrics (adjudicated precision, P@5, nDCG@5, draft "
            "rating) use the officer labels current when the report was produced."]
    return "\n".join(out) + "\n"


CSS = """body{font-family:"Nirmala UI","Noto Sans Devanagari",system-ui,sans-serif;margin:24px;color:#1b1f24;background:#fff;font-size:14px}
h1{font-size:20px}h2{font-size:16px;margin-top:22px}table{border-collapse:collapse;margin:6px 0 14px;width:100%}
th,td{border:1px solid #dde1e7;padding:4px 7px;text-align:left;vertical-align:top}th{background:#f1f3f6;font-size:12px}
.pass{color:#1b7f3b;font-weight:600}.fail{color:#b42318;font-weight:600}.skipped{color:#69717d}.muted{color:#69717d}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}code{font-size:12px}"""


def html(result: dict, gate: dict | None = None) -> str:
    e = lambda x: _html.escape(str(x))  # noqa: E731
    c, m = result["config"], result["metrics"]
    parts = [f"<!doctype html><html lang='mr'><head><meta charset='utf-8'><title>Backtest {e(c['data_class'])}</title>"
             f"<style>{CSS}</style></head><body>",
             f"<h1>Backtest — {e(c['data_class'])} ({m['n_holdouts']} holdouts)</h1>",
             f"<p class='muted'>Run <code>{e(result.get('run_id') or 'unsaved')}</code> · {e(result.get('created_at', ''))} · code "
             f"<code>{e(result.get('code_version'))}</code> · weights {e(c['intel']['source'])} · fee world {e(c['fee_world'])} · "
             f"holdout set <code>{e(c['holdouts_hash'])}</code></p>"]
    if gate is not None:
        verdict = "PASS" if gate["passed"] else "FAIL"
        parts.append(f"<h2>Gate: <span class='{verdict.lower()}'>{verdict}</span></h2><table><tr><th>check</th><th>value</th>"
                     "<th>rule</th><th>status</th><th>note</th></tr>" +
                     "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(r)}</td><td class='{e(s)}'>{e(s)}</td><td>{e(n)}</td></tr>"
                             for a, b, r, s, n in _gate_rows(gate)) + "</table>")
    parts.append("<div class='grid'>")
    for title, keys in HEADLINE:
        parts.append(f"<div><h2>{e(title)}</h2><table>" + "".join(f"<tr><td>{e(label)}</td><td><b>{e(fmt(m.get(k)))}</b></td></tr>"
                                                                   for k, label in keys) + "</table></div>")
    parts.append("</div>")
    for dim, groups in (result.get("strata") or {}).items():
        parts.append(f"<h2>By {e(dim)}</h2><table><tr><th>{e(dim)}</th><th>n</th><th>P</th><th>R</th><th>F1</th><th>first-letter</th>"
                     "<th>stat. FN</th><th>steps F1</th><th>τ</th><th>fee exact</th><th>fee err %</th></tr>" +
                     "".join(f"<tr><td>{e(v)}</td><td>{s['n_holdouts']}</td><td>{fmt(s['query_precision'])}</td><td>{fmt(s['query_recall'])}</td>"
                             f"<td>{fmt(s['query_f1'])}</td><td>{fmt(s['first_letter_completeness'])}</td><td>{s['statutory_false_negatives']}</td>"
                             f"<td>{fmt(s['steps_f1'])}</td><td>{fmt(s['kendall_tau'])}</td><td>{fmt(s['fee_exact_match_rate'])}</td>"
                             f"<td>{fmt(s['fee_mean_abs_pct_error'])}</td></tr>" for v, s in groups.items()) + "</table>")
    parts.append("<h2>Per holdout</h2><table><tr>" + "".join(f"<th>{e(h)}</th>" for h in CASE_HEAD) + "</tr>" +
                 "".join("<tr>" + "".join(f"<td>{e(x)}</td>" for x in r) + "</tr>" for r in _case_rows(result)) + "</table>")
    if result.get("warnings") or result.get("errors"):
        parts.append("<h2>Warnings / errors</h2><ul>" + "".join(f"<li>{e(w)}</li>" for w in result.get("warnings", [])) +
                     "".join(f"<li><b>{e(x['case_ref'])}</b>: {e(x['error'])}</li>" for x in result.get("errors", [])) + "</ul>")
    parts.append("</body></html>")
    return "".join(parts)
