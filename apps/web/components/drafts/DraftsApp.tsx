"use client";
import { useEffect, useState } from "react";
import { STATUS_COLOUR, type DraftRow, type DraftsView, type TemplateCode } from "@/lib/api";
import { ApiError, get, send } from "@/lib/client";

const STATUS_TEXT: Record<string, string> = {
  generated: "ready", incomplete: "has blanks", officer_edited: "edited by officer", superseded: "superseded",
};
const USABILITY: [string, string][] = [["as_is", "usable as is"], ["minor_edits", "minor edits"], ["major_edits", "major edits"], ["unusable", "not usable"]];

export default function DraftsApp({ caseId, caseRef }: { caseId: string; caseRef: string }) {
  const [view, setView] = useState<DraftsView | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<TemplateCode | null>(null);

  const load = () => get<DraftsView>(`/cases/${caseId}/drafts`).then(setView).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps

  const generate = async (templates?: TemplateCode[]) => {
    setBusy(true);
    setErr(null);
    try {
      await send("POST", `/cases/${caseId}/drafts`, templates ? { templates } : {});
      await load();
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 409 ? `${e.message}` : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const latest = new Map((view?.latest ?? []).map((l) => [l.template_code, l]));
  return (
    <main className="page">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 style={{ marginBottom: 2 }}>Drafts — {caseRef}</h1>
          <span className="muted">Built from the latest case analysis and the clause library. Internal render: provenance notes + “AI-सहाय्यित मसुदा” watermark.
            Issue render: clean, no other case’s reference.</span>
        </div>
        <div className="row">
          <a className="btn" href={`/cases/${caseId}`}>Case</a>
          <a className="btn" href={`/cases/${caseId}/analysis`}>Case analysis</a>
          <button className="primary" onClick={() => generate()} disabled={busy}>{busy ? "Generating…" : view?.latest.length ? "Regenerate all four" : "Generate all four drafts"}</button>
        </div>
      </div>
      {err && <div className="error" style={{ marginTop: 10 }}>{err}</div>}
      <div className="stack" style={{ marginTop: 14 }}>
        {(view?.templates ?? []).map((t) => {
          const l = latest.get(t.code);
          return (
            <section key={t.code} className="card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <h2 style={{ margin: 0 }}>{t.name_mr} <small className="muted">{t.name_en}</small></h2>
                  {l ? (
                    <span className="muted" style={{ fontSize: 12.5 }}>
                      v{l.version} · <span className="pill" style={{ color: STATUS_COLOUR[l.status] ?? (l.status === "generated" ? "var(--ok)" : l.status === "incomplete" ? "var(--warn)" : "var(--accent)") }}>{STATUS_TEXT[l.status] ?? l.status}</span>
                      {l.issue?.drafting_version && <> · {l.issue.drafting_version}</>}
                    </span>
                  ) : <span className="muted">not generated yet</span>}
                </div>
                <div className="row">
                  {l?.internal?.downloadable && <a className="btn" href={`/api/rci/drafts/${l.internal.id}/download`}>Internal .docx</a>}
                  {l?.issue?.downloadable && <a className="btn primary" href={`/api/rci/drafts/${l.issue.id}/download`}>Issue .docx</a>}
                  <button onClick={() => generate([t.code])} disabled={busy}>Regenerate</button>
                  {l && <button onClick={() => setOpen(open === t.code ? null : t.code)}>{open === t.code ? "Hide" : "Review"}</button>}
                </div>
              </div>
              {l?.issue && <Checks d={l.issue} />}
              {l?.issue && open === t.code && <Detail issueId={l.issue.id} internalId={l.internal?.id} rating={view?.my_ratings[t.code]} onChange={load} />}
            </section>
          );
        })}
      </div>
      {view && view.history.length > 0 && (
        <details style={{ marginTop: 16 }}>
          <summary className="muted">Version history ({view.history.length} renders)</summary>
          <table className="data" style={{ marginTop: 8 }}>
            <thead><tr><th>Template</th><th>Version</th><th>Mode</th><th>Status</th><th>Created</th><th>Officer upload</th></tr></thead>
            <tbody>
              {view.history.map((h) => (
                <tr key={h.id}><td>{h.template_code}</td><td>v{h.version}</td><td>{h.render_mode}</td><td>{h.status}</td>
                  <td>{h.created_at.slice(0, 16).replace("T", " ")}</td><td>{h.uploaded_at ? h.uploaded_at.slice(0, 16).replace("T", " ") : "—"}</td></tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </main>
  );
}

function Checks({ d }: { d: DraftRow }) {
  return (
    <div className="row" style={{ gap: 6, marginTop: 8 }}>
      <span className={`badge`} style={{ color: d.leak_check?.ok ? "var(--ok)" : "var(--bad)" }}>
        leak check {d.leak_check?.ok ? "passed" : `FAILED (${d.leak_check?.found.map((f) => f.value).join(", ")})`}
      </span>
      <span className="badge" style={{ color: d.crosscheck.length ? "var(--bad)" : "var(--ok)" }}>
        figures {d.crosscheck.length ? `${d.crosscheck.length} untraceable` : "all traced to the case"}
      </span>
      {d.missing_slots.length > 0 && <span className="badge" style={{ color: "var(--warn)" }}>blanks: {d.missing_slots.join(", ")}</span>}
      {d.warnings.filter((w) => !w.startsWith("blank slots")).map((w) => <span key={w} className="badge" style={{ color: "var(--warn)" }}>{w.replace(/^incomplete: /, "")}</span>)}
      {d.officer_diff && <span className="badge" style={{ color: "var(--accent)" }}>officer edit: {Math.round(d.officer_diff.similarity * 100)}% same, {d.officer_diff.clauses_edited} clause(s) reworded</span>}
    </div>
  );
}

function Detail({ issueId, internalId, rating, onChange }: { issueId: string; internalId?: string; rating?: { rating: number; usability: string; comment: string | null }; onChange: () => void }) {
  const [mode, setMode] = useState<"issue" | "internal">("internal");
  const [issue, setIssue] = useState<DraftRow | null>(null);
  const [internal, setInternal] = useState<DraftRow | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [stars, setStars] = useState(rating?.rating ?? 0);
  const [usability, setUsability] = useState(rating?.usability ?? "minor_edits");
  const [comment, setComment] = useState(rating?.comment ?? "");

  useEffect(() => {
    get<DraftRow>(`/drafts/${issueId}`).then(setIssue);
    if (internalId) get<DraftRow>(`/drafts/${internalId}`).then(setInternal);
  }, [issueId, internalId]);

  const upload = async (f: File | undefined) => {
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      await send("POST", `/drafts/${issueId}/upload`, fd);
      setIssue(await get<DraftRow>(`/drafts/${issueId}`));
      setMsg("Edited version stored; the diff is below.");
      onChange();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };
  const rate = async () => {
    try {
      await send("POST", `/drafts/${issueId}/rating`, { rating: stars, usability, comment: comment || null });
      setMsg("Rating saved.");
      onChange();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };
  const shown = mode === "issue" ? issue : internal;
  return (
    <div style={{ marginTop: 10 }}>
      <div className="tabs" style={{ position: "static" }}>
        <button className={mode === "internal" ? "on" : ""} onClick={() => setMode("internal")}>Internal (with provenance)</button>
        <button className={mode === "issue" ? "on" : ""} onClick={() => setMode("issue")}>Issue (clean)</button>
      </div>
      <div className="grid2" style={{ marginTop: 10, alignItems: "start" }}>
        <div className="card" style={{ background: "var(--panel-2)", maxHeight: 560, overflow: "auto" }}>
          {mode === "internal" && <div className="notice" style={{ marginBottom: 8, fontSize: 12 }}>AI-सहाय्यित मसुदा — अंतर्गत वापरासाठी</div>}
          {shown?.paragraphs?.map((p, i) => <p key={i} style={{ margin: "3px 0", fontSize: 13.5, whiteSpace: "pre-wrap" }}>{p}</p>) ?? <p className="muted">Loading…</p>}
        </div>
        <div className="stack">
          {mode === "internal" && internal && (
            <div>
              <h3 style={{ marginTop: 0 }}>Clauses used</h3>
              <table className="data" style={{ fontSize: 12 }}>
                <thead><tr><th>Key</th><th>Origin</th><th>Status</th></tr></thead>
                <tbody>
                  {internal.clauses_used?.map((c, i) => (
                    <tr key={i}><td className="mono">{c.clause_key}</td><td>{c.origin}</td>
                      <td style={{ color: c.verification === "verified" ? "var(--ok)" : "var(--warn)" }}>{c.verification}</td></tr>
                  ))}
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: 12 }}>
                Narrative: {internal.narratives?.map((n) => `${n.source}${n.check ? ` (${n.check})` : ""}`).join(", ") || "—"}
              </p>
            </div>
          )}
          <div>
            <h3 style={{ marginTop: 0 }}>Officer’s edited version</h3>
            <p className="muted" style={{ fontSize: 12 }}>Download the issue .docx, edit it in Word, upload it here. The diff (with PII re-redacted) is stored with the draft.</p>
            <input type="file" accept=".docx" onChange={(e) => upload(e.target.files?.[0])} />
            {issue?.officer_diff && <DiffView d={issue.officer_diff} />}
          </div>
          <div>
            <h3>Rate this draft</h3>
            <div className="row">
              {[1, 2, 3, 4, 5].map((n) => (
                <button key={n} className={n <= stars ? "primary small" : "small"} onClick={() => setStars(n)} aria-label={`${n} of 5`}>{n}</button>
              ))}
              <select value={usability} onChange={(e) => setUsability(e.target.value)}>
                {USABILITY.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
              </select>
            </div>
            <textarea rows={2} style={{ width: "100%", marginTop: 6 }} placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
            <button className="ok" onClick={rate} disabled={!stars}>Save rating</button>
          </div>
          {msg && <div className="notice">{msg}</div>}
        </div>
      </div>
    </div>
  );
}

function DiffView({ d }: { d: NonNullable<DraftRow["officer_diff"]> }) {
  return (
    <div style={{ fontSize: 12.5, marginTop: 8 }}>
      <div className="row" style={{ gap: 6 }}>
        <span className="badge">similarity {Math.round(d.similarity * 100)}%</span>
        <span className="badge">{d.paragraphs.unchanged} unchanged · {d.paragraphs.changed} changed · {d.paragraphs.inserted} added · {d.paragraphs.deleted} removed</span>
        {(d.numbers_added?.length ?? 0) > 0 && <span className="badge">numbers added: {d.numbers_added?.join(", ")}</span>}
      </div>
      {d.ops?.slice(0, 12).map((o, i) => (
        <div key={i} style={{ borderLeft: "3px solid var(--line-strong)", paddingLeft: 8, margin: "6px 0" }}>
          {o.generated.map((g, j) => <div key={`g${j}`} style={{ color: "var(--bad)" }}>− {g}</div>)}
          {o.uploaded.map((u, j) => <div key={`u${j}`} style={{ color: "var(--ok)" }}>+ {u}</div>)}
        </div>
      ))}
    </div>
  );
}
