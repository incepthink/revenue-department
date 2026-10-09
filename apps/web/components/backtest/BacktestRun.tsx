"use client";
import { useEffect, useMemo, useState } from "react";
import { BASIS_TEXT, type BacktestRun, type FpItem } from "@/lib/api";
import { get, send } from "@/lib/client";

const f = (v: unknown) => (v == null ? "—" : typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : String(v));
const HEAD: [string, [string, string][]][] = [
  ["Queries", [["query_precision", "precision"], ["query_recall", "recall"], ["query_f1", "F1"], ["first_letter_completeness", "first-letter completeness"],
               ["office_first_letter_completeness", "office’s own first letter"], ["statutory_false_negatives", "statutory false negatives"],
               ["adjudicated_precision", "adjudicated precision"], ["adjudications_pending", "adjudications pending"]]],
  ["Next steps", [["steps_precision", "precision"], ["steps_recall", "recall"], ["steps_f1", "F1"], ["kendall_tau", "Kendall τ"]]],
  ["Fee", [["fee_coverage", "coverage"], ["fee_exact_match_rate", "exact match"], ["fee_mean_abs_pct_error", "mean abs % error"]]],
  ["Precedents", [["p_at_5", "P@5 (officer)"], ["ndcg_at_5", "nDCG@5 (officer)"], ["relevance_label_coverage", "label coverage"],
                  ["proxy_ndcg_at_5", "proxy nDCG@5"]]],
  ["Drafts", [["draft_render_ok_rate", "render ok"], ["drafts_complete_rate", "no blanks"], ["leak_failures", "leak failures"],
              ["draft_rating_mean", "officer rating"], ["draft_ratings_n", "ratings"]]],
];
type Tab = "summary" | "cases" | "adjudication" | "relevance";

export default function BacktestRunView({ runId, canLabel }: { runId: string; canLabel: boolean }) {
  const [r, setR] = useState<BacktestRun | null>(null);
  const [tab, setTab] = useState<Tab>("summary");
  const [err, setErr] = useState<string | null>(null);
  const load = () => get<BacktestRun>(`/backtest/runs/${runId}`).then(setR).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <main className="page"><div className="error">{err}</div></main>;
  if (!r) return <main className="page"><p className="muted">Loading…</p></main>;
  const m = r.metrics;
  return (
    <main className="page" style={{ maxWidth: 1400 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 style={{ marginBottom: 2 }}>Backtest — {r.config.data_class} · {r.cases.length} holdouts</h1>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {r.created_at.slice(0, 16).replace("T", " ")} · code {r.code_version} · weights {r.config.intel.source} · fee world {r.config.fee_world} ·
            drafts {r.config.drafts} · set {r.config.holdouts_hash}
          </span>
        </div>
        <div className="row">
          <a className="btn" href="/backtest">All runs</a>
          <a className="btn" href={`/api/rci/backtest/runs/${runId}/report.md`} target="_blank" rel="noreferrer">Markdown</a>
          <a className="btn primary" href={`/api/rci/backtest/runs/${runId}/report.html`} target="_blank" rel="noreferrer">HTML report</a>
        </div>
      </div>
      <div className={r.gate.passed ? "success" : "error"} style={{ marginTop: 10 }}>
        <b>Gate {r.gate.passed ? "PASS" : "FAIL"}</b>{r.gate.failed.length ? ` — ${r.gate.failed.join(", ")}` : ""}
        <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>{r.gate.baseline ? `baseline ${r.gate.baseline.written_at?.slice(0, 10)} (${r.gate.baseline.holdouts_hash})` : "no baseline"}</span>
      </div>
      {r.warnings.map((w) => <div key={w} className="notice" style={{ marginTop: 6 }}>{w}</div>)}
      <div className="tabs" style={{ position: "static", marginTop: 12 }}>
        {(["summary", "cases", "adjudication", "relevance"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {{ summary: "Summary", cases: "Per holdout", adjudication: "False-positive adjudication", relevance: "Precedent relevance" }[t]}
            {t === "adjudication" && (m.adjudications_pending ?? 0) > 0 && <span className="count">{m.adjudications_pending}</span>}
          </button>
        ))}
      </div>
      {tab === "summary" && (
        <>
          <div className="grid2" style={{ marginTop: 12 }}>
            {HEAD.map(([title, keys]) => (
              <section key={title} className="card">
                <h3 style={{ marginTop: 0 }}>{title}</h3>
                <table className="data">
                  <tbody>{keys.map(([k, label]) => <tr key={k}><td>{label}</td><td><b>{f(m[k])}</b></td></tr>)}</tbody>
                </table>
              </section>
            ))}
            <section className="card">
              <h3 style={{ marginTop: 0 }}>Gate checks</h3>
              <table className="data" style={{ fontSize: 12.5 }}>
                <tbody>{r.gate.checks.map((c) => (
                  <tr key={c.check}><td>{c.check}</td><td>{f(c.value)}</td><td className="muted">{c.rule}</td>
                    <td style={{ color: c.status === "pass" ? "var(--ok)" : c.status === "fail" ? "var(--bad)" : "var(--muted)" }}>{c.status}</td></tr>
                ))}</tbody>
              </table>
            </section>
          </div>
          {Object.entries(r.strata).map(([dim, groups]) => (
            <section key={dim} className="card" style={{ marginTop: 12 }}>
              <h3 style={{ marginTop: 0 }}>By {dim}</h3>
              <table className="data">
                <thead><tr><th>{dim}</th><th>n</th><th>P</th><th>R</th><th>F1</th><th>first letter</th><th>stat. FN</th><th>steps F1</th><th>τ</th><th>fee exact</th></tr></thead>
                <tbody>
                  {Object.entries(groups).map(([v, s]) => (
                    <tr key={v}><td>{v}</td><td>{s.n_holdouts}</td><td>{f(s.query_precision)}</td><td>{f(s.query_recall)}</td><td>{f(s.query_f1)}</td>
                      <td>{f(s.first_letter_completeness)}</td><td>{f(s.statutory_false_negatives)}</td><td>{f(s.steps_f1)}</td><td>{f(s.kendall_tau)}</td>
                      <td>{f(s.fee_exact_match_rate)}</td></tr>
                  ))}
                </tbody>
              </table>
            </section>
          ))}
        </>
      )}
      {tab === "cases" && <Cases r={r} />}
      {tab === "adjudication" && <Adjudication runId={runId} canLabel={canLabel} onChange={load} />}
      {tab === "relevance" && <Relevance runId={runId} canLabel={canLabel} onChange={load} />}
    </main>
  );
}

function Cases({ r }: { r: BacktestRun }) {
  return (
    <table className="data" style={{ marginTop: 12, fontSize: 12.5 }}>
      <thead><tr><th>Holdout</th><th>Stratum</th><th>Office queried</th><th>Tool predicted</th><th>Missed statutory</th><th>Steps (true → predicted)</th><th>Fee pred / obs</th><th>Drafts</th></tr></thead>
      <tbody>
        {r.cases.map((c) => {
          const fp = new Set(c.metrics.false_positives);
          const drafts = Object.values(c.drafts ?? {});
          return (
            <tr key={c.case_id}>
              <td><a href={`/cases/${c.case_id}`}>{c.case_ref}</a><div className="muted">filed {c.filing_date}</div></td>
              <td>{Object.values(c.stratum).join(" · ")}</td>
              <td>{c.truth_queried.map((x) => <div key={x} style={{ color: c.predicted.includes(x) ? "var(--ok)" : "var(--bad)" }}>{x}</div>)}</td>
              <td>{c.predicted.map((x) => <div key={x} style={{ color: fp.has(x) ? "var(--warn)" : "var(--ok)" }}>{x}</div>)}</td>
              <td style={{ color: c.metrics.statutory_false_negatives.length ? "var(--bad)" : undefined }}>{c.metrics.statutory_false_negatives.join(", ") || "—"}</td>
              <td className="muted">{c.steps_truth.join(" → ")}<div>{c.steps_predicted.join(" → ")}</div>τ {f(c.metrics.kendall_tau)}</td>
              <td>{c.fee.predicted ?? "—"} / {c.fee.observed ?? "—"}<div className="muted">{c.fee.world}</div></td>
              <td>{drafts.length ? `${drafts.filter((d) => d.render_ok).length}/${drafts.length} ok, ${drafts.filter((d) => d.render_ok && !d.leak_ok).length} leak` : "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Adjudication({ runId, canLabel, onChange }: { runId: string; canLabel: boolean; onChange: () => void }) {
  const [items, setItems] = useState<FpItem[]>([]);
  const load = () => get<FpItem[]>(`/backtest/runs/${runId}/adjudication`).then(setItems);
  useEffect(() => { load(); }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps
  const decide = async (it: FpItem, verdict: string) => {
    await send("POST", "/backtest/adjudications", { case_id: it.case_id, code: it.code, verdict, run_id: runId });
    await load();
    onChange();
  };
  const pending = useMemo(() => items.filter((i) => !i.verdict).length, [items]);
  return (
    <section className="card" style={{ marginTop: 12 }}>
      <p className="muted" style={{ marginTop: 0, fontSize: 12.5 }}>
        Requirements the tool would have asked for but the office did not ({items.length}, {pending} pending). “Valid but not asked” counts
        as correct in the adjudicated precision; “truly unnecessary” stays a false positive. Verdicts are kept per holdout and requirement,
        so later runs reuse them.
      </p>
      <table className="data">
        <thead><tr><th>Holdout</th><th>Requirement</th><th>Basis</th><th>Support</th><th>Office asked for</th><th>Verdict</th></tr></thead>
        <tbody>
          {items.map((it) => (
            <tr key={`${it.case_id}:${it.code}`}>
              <td><a href={`/cases/${it.case_id}`}>{it.case_ref}</a></td>
              <td><b>{it.name_mr ?? it.code}</b><div className="muted mono">{it.code}</div></td>
              <td>{it.basis ? <span className="pill" style={{ color: BASIS_TEXT[it.basis].colour }}>{BASIS_TEXT[it.basis].en}</span> : "—"}</td>
              <td>{it.support != null ? `${Math.round(it.support * 100)}% of ${it.n_applicable}` : "—"}</td>
              <td className="muted" style={{ fontSize: 12 }}>{it.office_queried.join(", ") || "nothing"}</td>
              <td>
                {canLabel ? (
                  <div className="row" style={{ gap: 4 }}>
                    <button className={it.verdict === "valid_not_asked" ? "ok small" : "small"} onClick={() => decide(it, "valid_not_asked")}>valid but not asked</button>
                    <button className={it.verdict === "truly_unnecessary" ? "danger small" : "small"} onClick={() => decide(it, "truly_unnecessary")}>truly unnecessary</button>
                  </div>
                ) : (it.verdict ?? "pending")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

type RelItem = { case_id: string; case_ref: string; truth_queried: string[]; top5: { case_id: string; case_ref: string; score: number; society_name: string | null; relevance: number | null }[] };

function Relevance({ runId, canLabel, onChange }: { runId: string; canLabel: boolean; onChange: () => void }) {
  const [items, setItems] = useState<RelItem[]>([]);
  const load = () => get<RelItem[]>(`/backtest/runs/${runId}/relevance`).then(setItems);
  useEffect(() => { load(); }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps
  const label = async (h: RelItem, t: RelItem["top5"][number], relevance: number) => {
    await send("POST", "/backtest/relevance", { case_id: h.case_id, target_case_id: t.case_id, relevance, run_id: runId });
    await load();
    onChange();
  };
  return (
    <div className="stack" style={{ marginTop: 12 }}>
      <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
        For each holdout, how useful is each of the five precedents the tool ranked first? 0 not relevant · 1 somewhat · 2 highly. These labels
        drive P@5 and nDCG@5.
      </p>
      {items.map((h) => (
        <section key={h.case_id} className="card">
          <b>{h.case_ref}</b> <span className="muted" style={{ fontSize: 12 }}>office queried: {h.truth_queried.join(", ") || "nothing"}</span>
          {!h.top5.length && <p className="muted" style={{ margin: "6px 0 0", fontSize: 12.5 }}>No precedent had been decided before this application was filed — nothing to label.</p>}
          {h.top5.length > 0 && <table className="data" style={{ marginTop: 6 }}>
            <tbody>
              {h.top5.map((t, i) => (
                <tr key={t.case_id}>
                  <td style={{ width: 30 }}>{i + 1}</td>
                  <td><a href={`/cases/${t.case_id}`} target="_blank" rel="noreferrer">{t.case_ref}</a> <span className="muted">{t.society_name}</span></td>
                  <td style={{ width: 70 }}>{t.score.toFixed(3)}</td>
                  <td style={{ width: 200 }}>
                    {canLabel ? [0, 1, 2].map((v) => (
                      <button key={v} className={t.relevance === v ? "primary small" : "small"} style={{ marginRight: 4 }} onClick={() => label(h, t, v)}>{v}</button>
                    )) : (t.relevance ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>}
        </section>
      ))}
    </div>
  );
}
