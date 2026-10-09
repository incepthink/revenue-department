"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  citeHref, viewerHref,
  type Evidence, type IntakeDoc, type IntakeFinding, type IntakeLevel, type IntakeRef, type IntakeReport, type IntakeRule,
  type IntakeRuleResult, type IntakeSlot, type IntakeStage, type IntakeView,
} from "@/lib/api";
import { get, send } from "@/lib/client";
import Icon from "../Icon";
import { LEVEL, LEVEL_ORDER, VERDICT } from "./levels";

const POLL_MS = 2000;
const STUCK_MS = 120_000; // no progress for this long → offer a restart (e.g. the API restarted mid-run)

const STAGE: Record<IntakeStage, { label: string; busy?: boolean; done?: boolean; bad?: boolean }> = {
  queued: { label: "Waiting" },
  reading: { label: "Reading pages", busy: true },
  read: { label: "Waiting to be checked", busy: true },
  identifying: { label: "Identifying the document", busy: true },
  checking: { label: "Checking contents", busy: true },
  checked: { label: "Checked", done: true },
  failed: { label: "Failed", bad: true },
};
const VERDICT_OF_RULE: Record<IntakeRuleResult["verdict"], { label: string; level: IntakeLevel }> = {
  met: { label: "Met", level: "ok" },
  not_met: { label: "Not met", level: "problem" },
  cannot_tell: { label: "Not shown", level: "unchecked" },
  not_applicable: { label: "Not applicable", level: "info" },
};
const TENURE: Record<string, string> = { occupancy_class_2: "Occupancy Class-II", govt_lease: "Government lease", occupancy_class_1: "Occupancy Class-I" };
const AMOUNT_FIELDS = new Set(["amount_paid", "arrears", "consideration"]);

function fmt(field: string | undefined, v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  const s = String(v);
  if (field === "tenure") return TENURE[s] ?? s;
  if (field === "area_sqm") return `${s} sq m`;
  if (field && AMOUNT_FIELDS.has(field)) return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(Number(s));
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  return d ? `${d[3]}/${d[2]}/${d[1]}` : s;
}

// A page of the case in the document viewer (`href` overrides: a finding's or a fact's own link).
function PageLink({ caseId, seq, snippet, href: to, children }: {
  caseId: string; seq: number | null | undefined; snippet?: string | null; href?: string; children?: ReactNode;
}) {
  if (!seq) return null;
  const href = to ?? viewerHref(caseId, { seq });
  return <a className="cite" href={href} target="_blank" rel="noreferrer" title={snippet ?? "Open the page"}>{children ?? `p.${seq}`}</a>;
}

// `toPages`: a case just created — once the check is done, the results open on the pages, findings drawn on them.
export default function IntakeApp({ caseId, toPages = false }: { caseId: string; toPages?: boolean }) {
  const [view, setView] = useState<IntakeView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState(0); // bumped when a run is started: restarts the polling
  const [stuck, setStuck] = useState(false);
  const lastProgress = useRef({ key: "", at: Date.now() });

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const s = await get<IntakeView>(`/cases/${caseId}/intake/status`);
        if (stop) return;
        if (s.state === "done" && toPages) {
          window.location.replace(`/cases/${caseId}/documents`);
          return;
        }
        if (s.state === "done" || s.state === "failed" || s.state === "not_started") {
          // the full view (extracted values, findings) is read once — that read is audited, the poll is not
          const full = s.state === "done" ? await get<IntakeView>(`/cases/${caseId}/intake`) : s;
          if (!stop) setView(full);
          if (s.state === "done" && s.assembling) timer = setTimeout(tick, POLL_MS * 3);
          return;
        }
        setView(s);
        const key = s.documents.map((d) => d.stage).join();
        if (key !== lastProgress.current.key) lastProgress.current = { key, at: Date.now() };
        setStuck(Date.now() - lastProgress.current.at > STUCK_MS);
        timer = setTimeout(tick, POLL_MS);
      } catch (e) {
        if (!stop) setErr((e as Error).message);
        timer = setTimeout(tick, POLL_MS * 3);
      }
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [caseId, run, toPages]);

  const start = async (restart = false) => {
    setBusy(true);
    setErr(null);
    try {
      await send("POST", `/cases/${caseId}/intake${restart ? "?restart=true" : ""}`);
      lastProgress.current = { key: "", at: Date.now() };
      setStuck(false);
      setView((v) => (v ? { ...v, state: "processing", report: null } : v));
      setRun((n) => n + 1);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const c = view?.case;
  const cardReady = view?.state === "done" && !view.assembling;
  return (
    <main className="page intake">
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href="/">Cases</a><span className="sep">/</span><span>{c ? c.title ?? c.case_ref : "…"}</span>
      </nav>
      <div className="page-head">
        <div>
          <h1>Document check</h1>
          <div className="sub">
            {c && <><b style={{ color: "var(--text-2)", fontWeight: 600 }}>{c.title ?? c.case_ref}</b>{c.society_name && <> · {c.society_name}</>}</>}
            {view?.report_at && view.state === "done" && (
              <> · <Icon name="clock" size={13} /> Checked {String(view.report_at).slice(0, 16).replace("T", " ")} · {view.documents.length} file(s)</>
            )}
          </div>
        </div>
        <div className="actions-bar">
          {view?.state === "done" && <button onClick={() => start()} disabled={busy}><Icon name="refresh" /> Check again</button>}
          {cardReady && <a className="btn primary" href={`/cases/${caseId}/documents`}><Icon name="eye" /> View documents</a>}
        </div>
      </div>
      {err && <div className="error" style={{ marginBottom: 12 }}><Icon name="alert" /> <span>{err}</span></div>}
      {!view && !err && <p className="muted"><span className="spinner" /> Loading…</p>}
      {view?.state === "processing" && <Progress view={view} stuck={stuck} onRestart={() => start(true)} busy={busy} />}
      {view?.state === "failed" && (
        <div className="error">
          <Icon name="alert" />
          <span style={{ flex: 1 }}>The document check stopped: {view.case.last_error ?? "unknown error"}.</span>
          <button className="small" onClick={() => start()} disabled={busy}><Icon name="refresh" size={14} /> Try again</button>
        </div>
      )}
      {view?.state === "not_started" && (
        <div className="notice" style={{ alignItems: "center" }}>
          <Icon name="info" />
          {view.documents.some((d) => d.slot)
            ? <><span style={{ flex: 1 }}>The documents of this case have not been checked yet.</span> <button className="small primary" onClick={() => start()} disabled={busy}>Check documents</button></>
            : <span>This case was filed without the per-document upload form, so there is no document check for it.</span>}
        </div>
      )}
      {view?.state === "done" && view.report && <Results view={view} report={view.report} caseId={caseId} />}
    </main>
  );
}

// ------------------------------------------------------------------------------------------------
// While the run goes on
// ------------------------------------------------------------------------------------------------
function Progress({ view, stuck, onRestart, busy }: { view: IntakeView; stuck: boolean; onRestart: () => void; busy: boolean }) {
  const { total, read, checked } = view.progress;
  const pct = total ? Math.round(((read + checked) / (2 * total)) * 100) : 0;
  const bySlot = orderBySlot(view.slots, view.documents.filter((d) => d.slot));
  return (
    <section className="card intake-progress">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h2 style={{ margin: 0 }}><span className="spinner" style={{ marginRight: 8 }} />Checking the documents…</h2>
          <span className="muted">
            Each document is read, identified and checked on its own{view.llm.available ? " by the AI reader" : ""}; the documents are then compared with each other.
          </span>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="stat-value">{pct}%</div>
          <div className="stat-label">{checked} of {total} checked</div>
        </div>
      </div>
      <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${pct}%` }} /></div>
      <ul className="stage-list">
        {bySlot.map(({ slot, docs }) => docs.map((d) => {
          const st = STAGE[d.stage];
          return (
            <li key={d.id} className={st.done ? "done" : st.bad ? "bad" : st.busy ? "busy" : ""}>
              <span className="stage-icon">
                {st.done ? <Icon name="check" size={13} /> : st.bad ? <Icon name="x" size={13} /> : st.busy ? <span className="spinner" /> : <Icon name="clock" size={13} />}
              </span>
              <span className="stage-doc"><b>{slot.label}</b> <span className="muted">{d.filename}</span></span>
              <span className="stage-label">{st.label}</span>
            </li>
          );
        }))}
      </ul>
      {checked === total && total > 0 && <p className="muted" style={{ margin: "10px 0 0" }}><span className="spinner" /> Comparing the documents with each other…</p>}
      {stuck && (
        <div className="notice" style={{ marginTop: 12, alignItems: "center" }}>
          <Icon name="alert" />
          <span style={{ flex: 1 }}>No progress for two minutes — the check may have been interrupted.</span>
          <button className="small" onClick={onRestart} disabled={busy}><Icon name="refresh" size={14} /> Restart the check</button>
        </div>
      )}
    </section>
  );
}

const AUTO: IntakeSlot = { key: "auto", label: "Not identified yet", label_mr: "", multiple: true };

function orderBySlot(slots: IntakeSlot[], docs: IntakeDoc[]) {
  return [...slots, AUTO].map((slot) => ({ slot, docs: docs.filter((d) => d.slot === slot.key) })).filter((g) => g.docs.length);
}

// ------------------------------------------------------------------------------------------------
// Results
// ------------------------------------------------------------------------------------------------
function Results({ view, report, caseId }: { view: IntakeView; report: IntakeReport; caseId: string }) {
  const v = VERDICT[report.verdict];
  const passed = report.counts.ok;
  return (
    <>
      <section className={`verdict ${report.verdict}`}>
        <div className="verdict-icon" aria-hidden>{v.icon}</div>
        <div style={{ flex: 1 }}>
          <div className="verdict-label">{v.label}</div>
          <div className="verdict-headline">{report.headline}</div>
          <div className="row" style={{ gap: 6, marginTop: 8 }}>
            {report.counts.problem > 0 && <span className="pill lvl-problem">{report.counts.problem} problem{report.counts.problem === 1 ? "" : "s"}</span>}
            {report.counts.warning > 0 && <span className="pill lvl-warning">{report.counts.warning} to note</span>}
            <span className="pill lvl-ok">{passed} check{passed === 1 ? "" : "s"} passed</span>
            {report.counts.unchecked > 0 && <span className="pill lvl-unchecked">{report.counts.unchecked} not checked</span>}
          </div>
        </div>
      </section>
      {report.limited && (
        <div className="notice" style={{ marginTop: 12 }}>
          <Icon name="alert" />
          <span style={{ flex: 1 }}>
            The documents were read with pattern rules only{view.llm.available ? "" : " (no AI reader is configured)"}, so wrong documents and
            problems inside a document are not detected. Treat this result as partial.
          </span>
        </div>
      )}
      {view.assembling && <p className="muted" style={{ marginTop: 10 }}><span className="spinner" /> Preparing the case file for review and analysis…</p>}

      <div className="grid2" style={{ marginTop: 16 }}>
        <section className="card">
          <h3 className="card-title"><Icon name="clipboard" /> What the applicant needs to do</h3>
          {report.actions.length ? (
            <ol className="actions">{report.actions.map((a) => <li key={a}>{a}</li>)}</ol>
          ) : (
            <p className="muted" style={{ margin: 0, display: "flex", gap: 8, alignItems: "center" }}>
              <Icon name="checkCircle" style={{ color: "var(--ok)" }} /> Nothing — no document needs to be replaced or added.
            </p>
          )}
        </section>
        <section className="card">
          <h3 className="card-title"><Icon name="layers" /> What we found</h3>
          {report.facts.length ? (
            <table className="data facts">
              <tbody>
                {report.facts.map((f, i) => (
                  <tr key={f.fact}>
                    <td className="muted" style={{ width: 170 }}>{f.fact}</td>
                    <td><b>{fmt(f.field, f.value)}</b></td>
                    <td className="muted" style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {slotLabel(view.slots, f.slot)} <PageLink caseId={caseId} seq={f.page_seq} snippet={f.snippet}
                        href={f.anchor ? viewerHref(caseId, { fact: i }) : undefined} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="muted" style={{ margin: 0 }}>No facts could be read from the documents.</p>}
          <div className="card-note muted">
            Dates are compared with {report.reference_date_source} ({fmt(undefined, report.reference_date)}).
            <RulesApplied report={report} />
          </div>
        </section>
      </div>

      <div className="section-title"><h2>Documents</h2><span className="count-badge">{view.documents.length}</span></div>
      <Documents view={view} report={report} caseId={caseId} />

      <div className="section-title"><h2>Checks</h2><span className="count-badge">{report.findings.length}</span></div>
      <Findings findings={report.findings} slots={view.slots} caseId={caseId} />
    </>
  );
}

function RulesApplied({ report }: { report: IntakeReport }) {
  const applied = report.rules_applied;
  if (!applied) return null;
  const unverified = applied.rule_sets.filter((r) => r.status !== "verified").length;
  const tenure = report.rule_facts?.tenure;
  return (
    <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>
      Rules in force on {fmt(undefined, applied.on)}:{" "}
      {applied.rule_sets.length ? applied.rule_sets.map((r, i) => (
        <span key={r.code}>{i > 0 && " · "}<a href="/admin/rules" title={`${r.kind}, in force from ${fmt(undefined, r.effective_from)}`}>{r.code}</a></span>
      )) : <b style={{ color: "var(--warn)" }}>none</b>}
      {unverified > 0 && <> — {unverified} not yet verified</>}
      {tenure ? `; applied for ${TENURE[tenure] ?? tenure} land` : "; tenure not settled by the documents"}.
    </p>
  );
}

function RuleBadge({ rule }: { rule: IntakeRule }) {
  const cite = `${rule.rule_set_code}${rule.source_ref ? `, ${rule.source_ref}` : ""}`;
  return (
    <>
      <span className="badge" title={`${rule.code} — ${rule.rule_set_name}`}>Rule {cite}</span>
      {rule.label && <span className="badge warn" title="Applied, but the rule row or its rule set is still to_verify">{rule.label}</span>}
    </>
  );
}

function slotLabel(slots: IntakeSlot[], key: string | null | undefined) {
  return [...slots, AUTO].find((s) => s.key === key)?.label ?? key ?? "";
}

function Findings({ findings, slots, caseId }: { findings: IntakeFinding[]; slots: IntakeSlot[]; caseId: string }) {
  return (
    <div className="stack">
      {LEVEL_ORDER.map((lvl) => {
        const rows = findings.filter((f) => f.level === lvl);
        if (!rows.length) return null;
        const body = <ul className="findings">{rows.map((f, i) => <FindingRow key={`${f.code}-${i}`} f={f} slots={slots} caseId={caseId} />)}</ul>;
        const head = <><span className={`lvl-icon lvl-${lvl}`}>{LEVEL[lvl].icon}</span> {LEVEL[lvl].label} <span className="count-pill">{rows.length}</span></>;
        // passed / unchecked are folded away: the officer opens them when they want the detail
        return lvl === "ok" || lvl === "unchecked" ? (
          <details key={lvl} className="card finding-group">
            <summary className="finding-head">{head}</summary>
            {body}
          </details>
        ) : (
          <section key={lvl} className="card finding-group">
            <div className="finding-head">{head}</div>
            {body}
          </section>
        );
      })}
    </div>
  );
}

function FindingRow({ f, slots, caseId }: { f: IntakeFinding; slots: IntakeSlot[]; caseId: string }) {
  const unverified = f.parameters.filter((p) => p.status !== "verified");
  return (
    <li>
      <div className="row" style={{ gap: 6 }}>
        <b>{f.title}</b>
        {f.source === "ai" && <span className="badge" title="Observed by the AI reader — confirm on the cited page">AI observation</span>}
        {f.source === "rule" && <span className="badge" title="Judged by the AI reader against the rule — confirm on the cited page">AI rule check</span>}
        {f.rule && <RuleBadge rule={f.rule} />}
        {f.depends_on_unverified && (
          <span className="badge warn" title={unverified.map((p) => `${p.key} = ${String(p.value)}`).join(", ")}>rule to be confirmed</span>
        )}
      </div>
      {f.detail && <div className="muted">{f.detail}</div>}
      {f.evidence.length > 0 && (
        <div className="evidence">
          {f.evidence.map((e, i) => (
            <EvidenceChip key={i} e={e} slots={slots} caseId={caseId}
              href={f.id && f.anchors ? viewerHref(caseId, { f: f.id, a: i }) : undefined} />
          ))}
        </div>
      )}
    </li>
  );
}

function EvidenceChip({ e, slots, caseId, href: to }: { e: IntakeRef; slots: IntakeSlot[]; caseId: string; href?: string }) {
  const text = `${slotLabel(slots, e.slot)}${e.field ? `: ${fmt(e.field, e.value)}` : ""}`;
  const href = e.page_seq ? to ?? citeHref({ case_id: caseId, page_seq: e.page_seq, snippet: e.snippet }) : null;
  return href ? <a className="ev-chip" href={href} target="_blank" rel="noreferrer" title={e.snippet ?? undefined}>{text} · p.{e.page_seq}</a>
    : <span className="ev-chip">{text}</span>;
}

// ------------------------------------------------------------------------------------------------
// Per document
// ------------------------------------------------------------------------------------------------
function docStatus(d: IntakeDoc): { label: string; level: IntakeLevel } {
  const r = d.result;
  if (d.stage === "failed") return { label: "Could not be processed", level: "problem" };
  if (!r) return { label: "Not checked", level: "unchecked" };
  if (!r.readable) return { label: "Unreadable", level: "problem" };
  if (r.relevance === "unrelated") return { label: "Not about this case", level: "warning" };
  if (r.document_matches === false) return d.identified ? { label: "Type uncertain", level: "warning" } : { label: "Wrong document", level: "problem" };
  if (r.observations.some((o) => o.severity === "problem")) return { label: "Has problems", level: "problem" };
  if (r.relevance === "supporting") return { label: "Additional", level: "info" };
  if (r.document_matches === true) return { label: "Looks right", level: "ok" };
  return { label: "Read (type not verified)", level: "unchecked" };
}

function Documents({ view, report, caseId }: { view: IntakeView; report: IntakeReport; caseId: string }) {
  // whether a document is required comes from the rules in force (the DOC_MISSING finding for its slot)
  const missing = new Map(report.findings.filter((f) => f.code === "DOC_MISSING").flatMap((f) => f.slots.map((s) => [s, f] as const)));
  return (
    <div className="doc-grid">
      {[...view.slots, AUTO].map((slot) => {
        const docs = view.documents.filter((d) => d.slot === slot.key);
        if (!docs.length && (slot.key === "other" || slot.key === AUTO.key)) return null;
        if (!docs.length) {
          const f = missing.get(slot.key);
          const required = f?.level === "problem";
          const maybe = f?.level === "unchecked";
          const lvl = required ? "problem" : "unchecked";
          return (
            <section key={slot.key} className="card doc-card empty-slot">
              <div className="doc-head">
                <div className="doc-head-main">
                  <span className={`doc-icon lvl-${lvl}`}><Icon name={required ? "alert" : "file"} size={18} /></span>
                  <div className="doc-title">
                    <b>{slot.label}</b>
                    {slot.label_mr && <span className="muted">{slot.label_mr}</span>}
                  </div>
                </div>
                <span className={`pill status lvl-${lvl}`}>{required ? "Missing" : maybe ? "May be required" : "Not uploaded"}</span>
              </div>
              <p className="doc-empty muted">
                {required ? `Required by ${f?.rule?.rule_set_code ?? "the rules in force"} and not uploaded.`
                  : maybe ? f?.detail : `Optional — not uploaded.${f?.detail ? ` ${f.detail}` : ""}`}
              </p>
            </section>
          );
        }
        return docs.map((d) => <DocCard key={d.id} d={d} slot={slot} caseId={caseId} />);
      })}
    </div>
  );
}

function DocCard({ d, slot, caseId }: { d: IntakeDoc; slot: IntakeSlot; caseId: string }) {
  const r = d.result;
  const st = docStatus(d);
  const other = slot.key === "other";
  const title = other ? r?.detected_as ?? d.identified?.detected_as ?? slot.label : slot.label;
  const sub = other ? (title === slot.label ? slot.label_mr : slot.label) : slot.label_mr;
  const labels = d.field_labels ?? {};
  const fields = r ? Object.entries(r.fields) : [];
  const found = fields.filter(([, ev]) => ev) as [string, Evidence][];
  const notFound = fields.filter(([, ev]) => !ev).map(([k]) => labels[k] ?? k);
  const fileHref = r && r.pages.length > 0 ? viewerHref(caseId, { seq: r.pages[0] }) : null;
  const hasFooter = !!r && (r.notes.length > 0 || r.unverified.length > 0);
  return (
    <section className="card doc-card">
      <div className="doc-head">
        <div className="doc-head-main">
          <span className={`doc-icon lvl-${st.level}`}><Icon name="fileText" size={18} /></span>
          <div className="doc-title">
            <b>{title}</b>
            {sub && <span className="muted">{sub}</span>}
          </div>
        </div>
        <span className={`pill status lvl-${st.level}`}>{st.label}</span>
      </div>
      <div className="doc-meta">
        {fileHref
          ? <a href={fileHref} target="_blank" rel="noreferrer" title="Open the document">{d.filename} <Icon name="external" size={12} /></a>
          : <span>{d.filename}</span>}
        <span className="muted"> · {d.pages ?? "?"} page{d.pages === 1 ? "" : "s"}</span>
        {d.identified && <Identified id={d.identified} />}
      </div>
      {d.error && <div className="error"><Icon name="alert" /> <span>{d.error}</span></div>}
      {other && r?.summary && <p className="doc-note">{r.summary}</p>}
      {other && r?.satisfies_requirement && <p className="doc-small">Filed as the required <b>{r.satisfies_requirement}</b>.</p>}
      {!other && r?.document_matches === false && (
        <p className="doc-note">
          {d.identified ? "Read in full, this looks more like" : "This looks like"} <b>{r.detected_as ?? "a different document"}</b>. {r.match_reason}
        </p>
      )}
      {found.length > 0 && (
        <table className="data doc-fields">
          <tbody>
            {found.map(([k, ev]) => (
              <tr key={k}>
                <td className="muted">{labels[k] ?? k}</td>
                <td>{fmt(k, ev.value)}</td>
                <td className="doc-cite"><PageLink caseId={caseId} seq={ev.page_seq} snippet={ev.snippet} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {r?.readable && notFound.length > 0 && <div className="doc-small muted"><b>Not found:</b> {notFound.join(", ")}</div>}
      {r && r.observations.length > 0 && (
        <ul className="observations">
          {r.observations.map((o, i) => (
            <li key={i} className={`obs-${o.severity}`}>
              <span className={`lvl-icon lvl-${o.severity}`}>{LEVEL[o.severity].icon}</span>
              <span>{o.text} <PageLink caseId={caseId} seq={o.page_seq} /></span>
            </li>
          ))}
        </ul>
      )}
      {r && <RulesChecked results={(r.rule_results ?? []).filter((x) => x.verdict !== "not_applicable")} caseId={caseId} />}
      {r && hasFooter && (
        <div className="doc-foot doc-small muted">
          {r.notes.length > 0 && <div>{r.notes.join(" · ")}</div>}
          {r.unverified.length > 0 && (
            <div title={r.unverified.map((u) => `${u.field}: ${u.raw} (${u.reason})`).join("\n")}>
              {r.unverified.length} value{r.unverified.length === 1 ? "" : "s"} set aside: the text was not found on the page.
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function RulesChecked({ results, caseId }: { results: IntakeRuleResult[]; caseId: string }) {
  if (!results.length) return null;
  return (
    <div className="doc-small">
      <b>Rules checked</b>
      <ul className="observations">
        {results.map((x) => {
          const v = VERDICT_OF_RULE[x.verdict];
          return (
            <li key={x.code} className={`obs-${v.level}`} title={x.code}>
              <span className={`lvl-icon lvl-${v.level}`}>{LEVEL[v.level].icon}</span>
              <span>
                <b>{v.label}:</b> {x.text}
                {x.quote && <> — “{x.quote}” <PageLink caseId={caseId} seq={x.page_seq} snippet={x.snippet} /></>}
                {x.reason && <span className="muted"> {x.reason}</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Identified({ id }: { id: NonNullable<IntakeDoc["identified"]> }) {
  const how = id.method === "llm"
    ? `by the AI reader${id.confidence !== undefined ? ` · ${Math.round(id.confidence * 100)}% sure` : ""}`
    : "by keywords — confirm";
  return (
    <div className="muted ident" title={[id.reason, ...id.notes].filter(Boolean).join("\n") || undefined}>
      Identified automatically ({how}){id.reason ? `: ${id.reason}` : ""}
      {id.bundled.length > 0 && <div style={{ color: "var(--warn)" }}>Also in this file: {id.bundled.join("; ")}</div>}
    </div>
  );
}
