"use client";
import { useEffect, useState } from "react";
import { get, send } from "@/lib/client";

type RunRow = {
  id: string; created_at: string; status: string; data_class: string; holdouts: number; holdouts_hash: string; gate: boolean | null;
  headline: Record<string, number | null>;
};
type Holdouts = {
  data_class: string; holdouts_hash: string; n_target: number;
  holdouts: { case_id: string; case_ref: string; society_name: string | null; locked: boolean; stratum: Record<string, string> }[];
};
const f = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(3));

export default function BacktestHome({ isAdmin }: { isAdmin: boolean }) {
  const [dataClass, setDataClass] = useState<"real" | "synthetic">("synthetic");
  const [prefix, setPrefix] = useState("SYN-P");
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [hold, setHold] = useState<Holdouts | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    setRuns(await get<RunRow[]>("/backtest/runs"));
    const q = new URLSearchParams({ data_class: dataClass, ...(prefix ? { prefix } : {}) });
    setHold(await get<Holdouts>(`/backtest/holdouts?${q}`));
  };
  useEffect(() => { load().catch((e) => setMsg(e.message)); }, [dataClass, prefix]); // eslint-disable-line react-hooks/exhaustive-deps

  const select = async () => {
    setBusy(true);
    try {
      const r = await send<{ selected: boolean; reason?: string; holdouts: string[] }>("POST", "/backtest/holdouts/select",
        { data_class: dataClass, prefix: prefix || null });
      setMsg(r.selected ? `Selected and locked ${r.holdouts.length} holdouts.` : r.reason ?? "unchanged");
      await load();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const run = async () => {
    setBusy(true);
    setMsg("Replaying every holdout at its filing stage… (about a second per case)");
    try {
      const r = await send<{ run_id: string }>("POST", "/backtest/runs", { data_class: dataClass, prefix: prefix || null });
      window.location.href = `/backtest/${r.run_id}`;
    } catch (e) {
      setMsg((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <main className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0 }}>Backtest</h1>
        <div className="row">
          <label className="inline">Data class
            <select value={dataClass} onChange={(e) => setDataClass(e.target.value as "real" | "synthetic")}>
              <option value="synthetic">synthetic</option><option value="real">real</option>
            </select>
          </label>
          <label className="inline">Case-ref prefix <input value={prefix} onChange={(e) => setPrefix(e.target.value)} style={{ width: 90 }} /></label>
          {isAdmin && <button onClick={select} disabled={busy || (hold?.holdouts.length ?? 0) > 0}>Select holdouts</button>}
          {isAdmin && <button className="primary" onClick={run} disabled={busy || !hold?.holdouts.length}>Run backtest</button>}
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12.5 }}>
        Holdouts are approved cases set aside (role locked): each is replayed as if just filed — filing-stage documents only, precedents
        decided before its application date, same society excluded — and the tool’s gaps, next steps, fee and drafts are compared with
        what the office actually did. The CI gate compares every run with the committed baseline.
      </p>
      {msg && <div className="notice" style={{ marginBottom: 10 }}>{msg}</div>}
      <section className="card">
        <h2 style={{ marginTop: 0 }}>Runs</h2>
        {!runs.length ? <p className="muted">No backtest run yet.</p> : (
          <table className="data">
            <thead><tr><th>When</th><th>Data</th><th>Holdouts</th><th>Gate</th><th>Query F1</th><th>Stat. FN</th><th>Steps F1</th><th>Fee exact</th><th>Drafts ok</th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td><a href={`/backtest/${r.id}`}>{r.created_at.slice(0, 16).replace("T", " ")}</a></td>
                  <td>{r.data_class}</td><td>{r.holdouts} <span className="muted mono">{r.holdouts_hash}</span></td>
                  <td>{r.gate == null ? "—" : <b style={{ color: r.gate ? "var(--ok)" : "var(--bad)" }}>{r.gate ? "pass" : "fail"}</b>}</td>
                  <td>{f(r.headline.query_f1)}</td><td>{r.headline.statutory_false_negatives ?? "—"}</td><td>{f(r.headline.steps_f1)}</td>
                  <td>{f(r.headline.fee_exact_match_rate)}</td><td>{f(r.headline.draft_render_ok_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section className="card" style={{ marginTop: 14 }}>
        <h2 style={{ marginTop: 0 }}>Holdouts <small className="muted">{hold?.holdouts.length ?? 0} of {hold?.n_target ?? 20} · set {hold?.holdouts_hash}</small></h2>
        <table className="data">
          <thead><tr><th>Case</th><th>Society</th><th>Tenure</th><th>Era</th><th>Decision</th><th>Queried</th><th>Locked</th></tr></thead>
          <tbody>
            {hold?.holdouts.map((h) => (
              <tr key={h.case_id}><td><a href={`/cases/${h.case_id}`}>{h.case_ref}</a></td><td>{h.society_name}</td><td>{h.stratum.tenure}</td>
                <td>{h.stratum.era}</td><td>{h.stratum.decision}</td><td>{h.stratum.queried}</td><td>{h.locked ? "yes" : "no"}</td></tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
