"use client";
import { Fragment, useEffect, useMemo, useState } from "react";
import {
  BASIS_TEXT, GR_STATUS, STATUS_COLOUR, citeHref, fmtDate, sourceHref,
  type AcceptedCase, type Analysis, type Basis, type Cite, type Fee, type Gap, type GrSource, type Precedent, type Step,
} from "@/lib/api";
import { AppliedGrs } from "@/components/library/PrecedentPanel";
import { ApiError, get, send } from "@/lib/client";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const inr = (v: string | null) =>
  v == null ? "—" : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(Number(v));
const FEATURE_TEXT: Record<string, string> = {
  sim_facts: "facts text", sim_documents: "documents text", sim_issues: "issues text", tenure: "holding basis", location: "location",
  area: "area", regime: "rule stack", recency: "recency", validations: "failed checks", gaps: "open requirements",
};
const TENURE: Record<string, string> = { govt_lease: "Govt lease", occupancy_class_2: "Class-II", occupancy_class_1: "Class-I" };

// A link to the cited page (opens the source viewer in a new tab, snippet highlighted).
function CiteLink({ c, label }: { c: Cite; label?: string }) {
  const href = citeHref(c);
  const text = label ?? `${c.case_ref} · ${c.exhibit_type ?? "page"} p.${c.page_seq ?? "?"}`;
  return href ? <a className="cite" href={href} target="_blank" rel="noreferrer" title={c.snippet ?? undefined}>{text}</a> : <span className="muted">{text}</span>;
}

function BasisPill({ basis }: { basis: Basis }) {
  const b = BASIS_TEXT[basis];
  return <span className="pill" style={{ color: b.colour }} title={b.mr}>{b.en}</span>;
}

function Bar({ value, colour = "var(--accent)" }: { value: number | null; colour?: string }) {
  return (
    <span className="bar" title={pct(value)}>
      <span style={{ width: `${Math.max(0, Math.min(1, value ?? 0)) * 100}%`, background: colour }} />
    </span>
  );
}

export default function AnalysisApp({ caseId, canRun }: { caseId: string; canRun: boolean }) {
  const [a, setA] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [asOf, setAsOf] = useState("");

  useEffect(() => {
    get<Analysis>(`/cases/${caseId}/analysis`)
      .then(setA)
      .catch((e) => { if (!(e instanceof ApiError && e.status === 404)) setErr(e.message); })
      .finally(() => setLoading(false));
  }, [caseId]);

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setA(await send<Analysis>("POST", `/cases/${caseId}/analyse`, asOf ? { as_of: asOf } : {}));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page analysis">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 style={{ marginBottom: 2 }}>
            Case analysis {a && <>— {a.case.case_ref} <small className="muted">{a.query.society_name}</small></>}
          </h1>
          {a && (
            <span className="muted">
              Card v{a.card.version} ({a.card.status}) · analysed {a.created_at?.slice(0, 16).replace("T", " ")} · precedents as of {a.as_of}
            </span>
          )}
        </div>
        <div className="row">
          <a className="btn" href={`/cases/${caseId}`}>Case</a>
          {a && <a className="btn" href={`/cases/${caseId}/drafts`}>Drafts</a>}
          {canRun && (
            <>
              <label className="inline" title="Only precedents decided on or before this date (time travel). Empty = today.">
                As of <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
              </label>
              <button className="primary" onClick={run} disabled={busy}>{busy ? "Analysing…" : a ? "Re-run analysis" : "Run analysis"}</button>
            </>
          )}
        </div>
      </div>
      {err && <div className="error" style={{ marginTop: 10 }}>{err}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !a && !err && <p className="notice" style={{ marginTop: 12 }}>This case has not been analysed yet.{canRun ? " Run the analysis to see precedents, gaps, next steps and the fee trace." : ""}</p>}
      {a && <Body a={a} />}
    </main>
  );
}

function Body({ a }: { a: Analysis }) {
  const excluded = Object.entries(a.pool).filter(([k, v]) => !["approved_total", "pool", "recalled"].includes(k) && v > 0);
  return (
    <>
      <section className="card meta" style={{ marginTop: 12 }}>
        <div className="row" style={{ gap: 6 }}>
          <span className="badge">rules on {a.rule_date}: {a.regime.join(" < ") || "none"}</span>
          <span className="badge" title={excluded.map(([k, v]) => `${k}: ${v}`).join(", ")}>
            pool {a.pool.pool} of {a.pool.approved_total} approved{excluded.length ? ` (excluded: ${excluded.map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")})` : ""}
          </span>
          <span className="badge">{a.engine.intel} · {a.engine.fees}</span>
          <span className="badge">weights: {a.engine.weights_source}</span>
          <span className="badge">vectors: {a.engine.embedding_model ?? "none"}</span>
          <span className="badge">explanations: {a.explanations.source}</span>
          <span className="badge">{a.case.data_class}</span>
        </div>
        <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
          This case: {TENURE[a.query.tenure ?? ""] ?? a.query.tenure ?? "tenure ?"} · {a.query.village ?? "village ?"} · {a.query.area_sqm ?? "?"} sq m ·
          filed {a.query.application_date ?? "?"}{a.query.failed_validations.length ? ` · failed checks: ${a.query.failed_validations.join(", ")}` : ""}
        </div>
        {a.warnings.map((w) => <div key={w} className="notice" style={{ marginTop: 8 }}>{w}</div>)}
      </section>
      <Precedents rows={a.precedents} />
      <Gaps rows={a.gaps} />
      <Steps done={a.steps.done} next={a.steps.next} />
      <FeeTrace fee={a.fee} provisions={a.provisions ?? []} />
      <GrProvisions rows={a.provisions ?? []} on={a.rule_date} />
      <AcceptedCases rows={a.accepted_cases ?? []} on={a.rule_date} />
    </>
  );
}

// ------------------------------------------------------------------------------------------------
function Precedents({ rows }: { rows: Precedent[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section className="card section">
      <h2>Matched precedents <small className="muted">({rows.length})</small></h2>
      {!rows.length ? <p className="muted">No approved precedent passed the filters.</p> : (
        <table className="data">
          <thead><tr><th>#</th><th>Precedent</th><th style={{ width: 140 }}>Score</th><th>Why</th><th>Rules</th><th>Outcome</th><th>Source</th></tr></thead>
          <tbody>
            {rows.map((p) => (
              <Fragment key={p.case_id}>
                <tr onClick={() => setOpen(open === p.case_id ? null : p.case_id)} style={{ cursor: "pointer" }}>
                  <td>{p.rank}</td>
                  <td>
                    <a href={`/cases/${p.case_id}`} onClick={(e) => e.stopPropagation()}><b>{p.case_ref}</b></a>
                    <div className="muted" style={{ fontSize: 12 }}>{p.society_name}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{TENURE[p.tenure ?? ""] ?? p.tenure} · {p.village} · {p.area_sqm} sq m</div>
                  </td>
                  <td><b>{p.score.toFixed(3)}</b> <Bar value={p.score} /></td>
                  <td>
                    {p.reasons.slice(0, 4).map((r) => (
                      <div key={r.en} style={{ fontSize: 12.5, color: r.kind === "difference" ? "var(--warn)" : undefined }} title={r.mr}>
                        {r.kind === "difference" ? "≠ " : "✓ "}{r.en}
                      </div>
                    ))}
                  </td>
                  <td><span className="pill" style={{ color: p.regime_label === "same_rules" ? "var(--ok)" : "var(--warn)" }}>{p.regime_label.replace("_", " ")}</span></td>
                  <td style={{ fontSize: 12.5 }}>{p.decision ?? "—"}<div className="muted">{p.order_date ?? p.as_of}</div></td>
                  <td>{p.link && <span onClick={(e) => e.stopPropagation()}><CiteLink c={p.link} label={`${p.link.exhibit_type} p.${p.link.page_seq}`} /></span>}</td>
                </tr>
                {open === p.case_id && (
                  <tr>
                    <td />
                    <td colSpan={6}>
                      <div className="grid2">
                        <table className="data compact">
                          <thead><tr><th>Feature</th><th>Value</th><th>Weight</th><th>Contribution</th></tr></thead>
                          <tbody>
                            {Object.entries(p.features).map(([f, v]) => (
                              <tr key={f}><td>{FEATURE_TEXT[f] ?? f}</td><td>{v.value.toFixed(2)} <Bar value={v.value} /></td><td>{v.weight}</td><td>{v.contribution.toFixed(3)}</td></tr>
                            ))}
                          </tbody>
                        </table>
                        <div style={{ fontSize: 12.5 }}>
                          {p.summary && <p style={{ marginTop: 0 }}>{p.summary.mr}</p>}
                          <p className="muted">{p.reasons.map((r) => r.mr).join(" · ")}</p>
                          <p className="muted">Rule stack: {p.regime.join(" < ")}</p>
                          {p.open_codes.length > 0 && <p className="muted">Queried / open: {p.open_codes.join(", ")}</p>}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------
const BASES: Basis[] = ["STATUTORY", "PRACTICE", "PRACTICE_OLD_RULES", "SUPERSEDED"];
const STATUS_MR: Record<string, string> = { missing: "सादर नाही", invalid: "त्रुटीपूर्ण", queried: "मागवले" };

function Gaps({ rows }: { rows: Gap[] }) {
  const [basis, setBasis] = useState<Basis | "all">("all");
  const [kind, setKind] = useState<"all" | "missing" | "invalid" | "queried">("all");
  const shown = useMemo(() => rows.filter((g) => (basis === "all" || g.basis === basis) && (kind === "all" || g.query_status === kind)), [rows, basis, kind]);
  return (
    <section className="card section">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Gaps <small className="muted">({shown.length} of {rows.length})</small></h2>
        <div className="row" style={{ gap: 4 }}>
          <span className="muted" style={{ fontSize: 12 }}>Basis</span>
          <div className="seg">
            <button className={basis === "all" ? "on" : ""} onClick={() => setBasis("all")}>All</button>
            {BASES.map((b) => (
              <button key={b} className={basis === b ? "on" : ""} onClick={() => setBasis(b)} disabled={!rows.some((g) => g.basis === b)}>
                {BASIS_TEXT[b].en} ({rows.filter((g) => g.basis === b).length})
              </button>
            ))}
          </div>
          <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} aria-label="Status">
            <option value="all">any status</option><option value="missing">missing</option><option value="invalid">invalid</option><option value="queried">queried</option>
          </select>
        </div>
      </div>
      {!shown.length && <p className="muted">No gaps{rows.length ? " for this filter" : ""}.</p>}
      {shown.map((g) => (
        <div key={g.code} className={`gap ${g.query_status}`}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <b>{g.name_mr}</b> <span className="muted">· {g.name_en}</span> <code>{g.code}</code>
            </div>
            <div className="row" style={{ gap: 6 }}>
              <span className="pill" style={{ color: STATUS_COLOUR[g.query_status] }}>{g.query_status} · {STATUS_MR[g.query_status]}</span>
              <BasisPill basis={g.basis} />
            </div>
          </div>
          <p style={{ margin: "6px 0 2px" }}>{g.explanation_mr}</p>
          <p className="muted" style={{ margin: "0 0 6px", fontSize: 12.5 }}>{g.explanation_en}</p>
          <div className="gap-grid">
            <div>
              <span className="k">Precedent support</span>
              {g.support == null ? <span className="muted">no applicable precedent</span> : (
                <span>{pct(g.support)} <Bar value={g.support} /> <span className="muted">{g.n_supporting}/{g.n_applicable} cases, queried in {g.n_queried}</span></span>
              )}
            </div>
            {g.was_queried_in.length > 0 && (
              <div><span className="k">Queried in</span>{g.was_queried_in.map((c) => <CiteLink key={`${c.case_id}-${c.page_seq}`} c={c} label={`${c.case_ref} p.${c.page_seq}`} />)}</div>
            )}
            {g.citations.filter((c) => c.how !== "queried").length > 0 && (
              <div><span className="k">Supplied in</span>{g.citations.filter((c) => c.how !== "queried").map((c) => <CiteLink key={`${c.case_id}-${c.exhibit_id}`} c={c} label={`${c.case_ref} ${c.exhibit_type} p.${c.page_seq}`} />)}</div>
            )}
            {g.rules.map((r) => (
              <div key={r.rule_set_code}><span className="k">Rule</span><span className="badge">{r.rule_set_code}</span>{" "}
                <span className="pill" style={{ color: STATUS_COLOUR[r.status] }}>{r.status}</span>{r.source_ref ? ` ${r.source_ref}` : ""}</div>
            ))}
            {g.validators.map((v) => (
              <div key={v.code}><span className="k">Check</span><b style={{ color: "var(--bad)" }}>{v.code}</b> — {v.message}{" "}
                {v.evidence.map((c, i) => <CiteLink key={i} c={c} label={`${c.exhibit_type} p.${c.page_seq}`} />)}</div>
            ))}
            {g.query_evidence.map((c) => <div key="qe"><span className="k">This case</span><CiteLink c={c} label={`query letter p.${c.page_seq}`} /></div>)}
          </div>
        </div>
      ))}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------
function StepRow({ s }: { s: Step }) {
  return (
    <li className={`step ${s.status}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <b>{s.name_mr}</b> <span className="muted">· {s.name_en}</span> {s.kind === "check" && <span className="badge">check</span>}
        </div>
        <div className="row" style={{ gap: 6 }}>
          {s.status === "done" ? <span className="pill" style={{ color: "var(--ok)" }}>done {s.date ?? ""}</span> : <BasisPill basis={s.basis} />}
        </div>
      </div>
      <div className="muted" style={{ fontSize: 12.5 }}>
        {s.n_precedents > 0
          ? <>in {pct(s.frequency)} of precedents ({s.n_precedents}){s.median_days_from_filing != null && ` · typically day ${s.median_days_from_filing} after filing`}
              {s.median_days_from_previous != null && ` · ${s.median_days_from_previous} days after the previous step`}
              {s.status === "next" && s.expected_by && ` · expected by ${s.expected_by}`}</>
          : "not seen in the matched precedents"}
      </div>
      {s.triggers.map((t) => (
        <div key={t.code} style={{ fontSize: 12.5 }}>
          <span className="badge">{t.kind === "rule" ? `rule ${t.rule_set_code}` : "case fact"}</span> {t.text_mr ?? t.code}
          {t.status && <span className="muted"> ({t.status}{t.applies == null && t.kind === "rule" ? ", condition undecided" : ""})</span>}{" "}
          {(t.evidence ?? []).map((c, i) => <CiteLink key={i} c={c} label={`${c.exhibit_type ?? "page"} p.${c.page_seq}`} />)}
        </div>
      ))}
      <div style={{ fontSize: 12 }}>
        {s.own_evidence && <CiteLink c={s.own_evidence} label={`this case p.${s.own_evidence.page_seq}`} />}
        {s.status === "next" && s.citations.map((c) => <CiteLink key={c.case_id} c={c} label={`${c.case_ref} ${c.date ?? ""} p.${c.page_seq}`} />)}
      </div>
    </li>
  );
}

function Steps({ done, next }: { done: Step[]; next: Step[] }) {
  return (
    <section className="card section">
      <h2>Next steps <small className="muted">({next.length})</small></h2>
      <ol className="timeline">
        {done.map((s) => <StepRow key={s.code} s={s} />)}
        {next.map((s) => <StepRow key={s.code} s={s} />)}
      </ol>
    </section>
  );
}

// ------------------------------------------------------------------------------------------------
const FEE_STATUS: Record<Fee["status"], { cls: string; text: string }> = {
  computed: { cls: "success", text: "Computed from verified rules, ASR and parameters." },
  unverified: { cls: "notice", text: "Would apply — rests on rules, ASR rows or parameters marked to_verify. Not a final figure." },
  blocked: { cls: "error", text: "Cannot be computed yet — see what is missing below. Every step that could run is shown." },
};

function GrChip({ g }: { g: GrSource }) {
  const href = g.found ? sourceHref({ document_id: g.document_id, provision_id: g.provision_id, page_no: g.page_no, quote: g.quote }) : null;
  const text = `${g.doc_key}${g.label ? ` ${g.label}` : ""}${g.page_no ? ` · p.${g.page_no}` : ""}`;
  return href
    ? <a className="cite-chip" href={href} target="_blank" rel="noreferrer" title={g.quote ?? undefined}>{text}</a>
    : <span className="pill status lvl-warning" title={g.quote ?? undefined}>{g.doc_key}: not in the library</span>;
}

function FeeTrace({ fee, provisions }: { fee: Fee; provisions: GrSource[] }) {
  const st = FEE_STATUS[fee.status];
  const byRule = new Map(provisions.filter((p) => p.rule).map((p) => [p.rule as string, p]));
  return (
    <section className="card section">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Fee trace</h2>
        <span className="muted" style={{ fontSize: 12 }}>{fee.engine_version}{fee.rule_set_code ? ` · ${fee.rule_set_code}` : ""}</span>
      </div>
      <div className={st.cls}>
        <b style={{ fontSize: 16 }}>{fee.total ? inr(fee.total) : "No total"}</b> — {st.text}
        {fee.blockers.length > 0 && <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{fee.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
      </div>
      {fee.unverified.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary className="muted">{fee.unverified.length} unverified input(s)</summary>
          <ul style={{ fontSize: 12.5 }}>{fee.unverified.map((u) => <li key={u}>{u}</li>)}</ul>
        </details>
      )}
      <table className="data" style={{ marginTop: 10 }}>
        <thead><tr><th>Step</th><th>Value</th><th>How</th><th>Status</th><th>Source</th></tr></thead>
        <tbody>
          {fee.trace.map((t) => (
            <tr key={t.step}>
              <td><b>{t.label}</b><div className="muted mono">{t.step}</div></td>
              <td className="mono">{t.value ?? "—"}</td>
              <td style={{ fontSize: 12.5 }}>{t.formula}{t.note && <div className="muted">{t.note}</div>}</td>
              <td><span className="pill" style={{ color: { ok: "var(--ok)", unverified: "var(--warn)", assumption: "var(--warn)", blocked: "var(--bad)", skipped: "var(--muted)" }[t.status] }}>{t.status}</span></td>
              <td style={{ fontSize: 12 }}>
                {t.sources.map((s, i) => s.kind === "evidence" && s.case_id
                  ? <CiteLink key={i} c={{ case_id: s.case_id, case_ref: "", exhibit_id: null, exhibit_type: s.exhibit_type ?? null, page_seq: s.page_seq ?? null, snippet: s.snippet }}
                              label={`${s.exhibit_type ?? "page"} p.${s.page_seq}`} />
                  : <span key={i} className="badge" title={s.source_ref ?? undefined}>
                      {s.kind === "asr" ? `ASR ${s.village} ${s.year} ₹${s.rate ?? "—"}` : s.seed_key ?? s.rule_set_code ?? s.code}
                      {s.status ? ` · ${s.status}` : ""}
                    </span>)}
                {t.sources.map((s, i) => {
                  const g = byRule.get(s.seed_key ?? s.code ?? "");
                  return g ? <GrChip key={`gr${i}`} g={g} /> : null;
                })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

// ------------------------------------------------------------------------------------------------
function GrProvisions({ rows, on }: { rows: GrSource[]; on: string }) {
  return (
    <section className="card section">
      <h2>GR provisions in force on {fmtDate(on)} <small className="muted">({rows.length})</small></h2>
      <p className="muted" style={{ marginTop: 0 }}>
        The provisions behind the rate and the competent authority above, the obligations in force, and what GR search finds
        for this kind of case on the rule date. Each opens the GR page with the words marked.
      </p>
      {!rows.length ? <p className="muted">No GR provisions found (the library may not be loaded).</p> : (
        <table className="data">
          <thead><tr><th>Why</th><th>GR</th><th>Words</th><th>On {fmtDate(on)}</th></tr></thead>
          <tbody>
            {rows.map((g, i) => (
              <tr key={i}>
                <td style={{ whiteSpace: "nowrap" }}>{g.why}{g.rule && <div className="muted mono" style={{ fontSize: 11 }}>{g.rule}</div>}</td>
                <td><GrChip g={g} /><div className="muted" style={{ fontSize: 12 }}>{g.type_label} {fmtDate(g.doc_date)}</div></td>
                <td lang="mr" style={{ fontSize: 12.5, maxWidth: 520 }}>{g.quote}</td>
                <td>{g.status ? <span className={`pill status ${GR_STATUS[g.status].lvl}`}>{GR_STATUS[g.status].text}</span> : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function AcceptedCases({ rows, on }: { rows: AcceptedCase[]; on: string }) {
  return (
    <section className="card section">
      <h2>Accepted cases <small className="muted">({rows.length})</small></h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Cases the department decided, read from its case notes, closest first (kind of case, holding, location, area, then
        the note&apos;s text). With so few notes this is a demonstrator: it grows as the department sends more decided cases.
      </p>
      {!rows.length ? <p className="muted">No accepted case notes loaded yet.</p> : rows.map((c) => (
        <article key={c.precedent_id} className="lib-accepted">
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <a href={`/library/${c.document_id}?tab=details`} target="_blank" rel="noreferrer"><b>{c.title ?? c.key}</b></a>
            <span className="badge">match {Math.round(c.score * 100)}%</span>
            {c.decided_on && <span className="muted">decided {fmtDate(c.decided_on)}</span>}
            {c.status !== "verified" && <span className="pill status lvl-unchecked">To verify</span>}
          </div>
          <p className="lib-statement">{c.statement.en}</p>
          {c.reasons.length > 0 && <div className="muted" style={{ fontSize: 12.5 }}>{c.reasons.join(" · ")}</div>}
          <h4 style={{ margin: "8px 0 4px" }}>GRs it applied, on {fmtDate(on)}</h4>
          <AppliedGrs grs={c.applied_grs} />
        </article>
      ))}
    </section>
  );
}
