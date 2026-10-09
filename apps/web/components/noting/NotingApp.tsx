"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  DraftsView, NotingBlock, NotingFact, NotingFacts, NotingFlag, NotingItem, NotingOp, NotingPage, NotingStage, NotingStatus, NotingView,
} from "@/lib/api";
import { get, pageImageUrl, send } from "@/lib/client";
import Icon from "../Icon";
import DocPreview from "../drafts/DocPreview";
import { Checks, Detail } from "../drafts/DraftsApp";
import { LEVEL } from "../intake/levels";

const POLL_MS = 2000;
const STUCK_MS = 180_000;
const QUEUED_MS = 90_000;  // "uploaded" = queued, not started: a minute and a half is already too long
const FIRST_THUMBS = 24;  // the letter is at the front; the annexures' thumbnails on request
const RUNNING: NotingStage[] = ["uploaded", "segmenting", "reading", "extracting", "generating"];
const STEPS = ["Upload", "Report pages", "Reading", "Review facts", "Noting"] as const;
const STEP_OF: Record<NotingStage, number> = {
  uploaded: 0, segmenting: 0, awaiting_range: 1, reading: 2, extracting: 2, awaiting_review: 3, generating: 4, done: 4, failed: 0,
};
const STAGE_TEXT: Record<NotingStage, string> = {
  uploaded: "Queued", segmenting: "Examining the pages", awaiting_range: "Confirm the report pages", reading: "Reading the letter",
  extracting: "Reading the facts", awaiting_review: "Review the facts", generating: "Writing the Noting", done: "Noting ready",
  failed: "Failed",
};
const GROUPS: [string, string[]][] = [
  ["The Collector's letter", ["letter_no", "letter_date", "office", "district"]],
  ["Society", ["society_name", "registration_no", "registration_date", "application_date"]],
  ["Land", ["village", "taluka", "cts_no", "area_sqm", "tenure", "land_use"]],
  ["Value and fee", ["asr_year", "market_rate", "land_value", "conversion_pct", "conversion_fee", "notification_date"]],
];
const AMOUNT_KINDS: Record<string, string> = {
  unearned_income: "Unearned income", transfer_fee: "Transfer fee", land_value: "Land value", conversion_fee: "Conversion fee", other: "Other",
};
const KIND_TEXT: Record<string, string> = {
  letter: "letter", annexure_letter: "other letter", report: "report", form: "form", property_card: "property card", map: "map",
  photo: "photo", table: "table", other: "other",
};
const ROLE_TEXT: Record<string, string> = {
  header: "letterhead", subject: "subject", reference: "reference", salutation: "salutation", intro: "opening", facts: "facts",
  history: "history", report: "report", payment: "payment", valuation: "valuation", legal: "legal", recommendation: "recommendation",
  signoff: "sign-off", copy_to: "copy to", other: "other",
};

const iso = (v: unknown) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(v ?? ""));
  return m ? `${m[3]}/${m[2]}/${m[1]}` : null;
};
const shown = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : iso(v) ?? String(v));
const sourceHref = (caseId: string, page: number | null | undefined, q?: string) =>
  page ? `/cases/${caseId}/source/${page}${q ? `?q=${encodeURIComponent(q.slice(0, 120))}` : ""}` : null;

export default function NotingApp({ caseId }: { caseId: string }) {
  const [view, setView] = useState<NotingView | null>(null);
  const [status, setStatus] = useState<NotingStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pollErr, setPollErr] = useState<string | null>(null);
  const [run, setRun] = useState(0);
  const [stuck, setStuck] = useState(false);
  const [showRange, setShowRange] = useState(false);
  const last = useRef({ key: "", at: Date.now() });
  const since = useRef({ stage: "", at: Date.now() });
  const seen = useRef("");  // what the full view was last read at (stage, prefetch done)

  const load = async () => setView(await get<NotingView>(`/notings/${caseId}`));

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const s = await get<NotingStatus>(`/notings/${caseId}/status`);
        if (stop) return;
        setStatus(s);
        if (s.stage !== since.current.stage) since.current = { stage: s.stage, at: Date.now() };
        // the full view (facts, paragraphs) is read once per change — that read is audited, the poll is not
        const prefetching = s.stage === "awaiting_range" && !!s.prefetch && !s.prefetch.done;
        const at = `${s.stage}:${s.prefetch?.done ?? ""}`;
        if (at !== seen.current) {
          const v = await get<NotingView>(`/notings/${caseId}`);
          if (stop) return;
          setView(v);
          seen.current = at;
        }
        setPollErr(null);
        if (!RUNNING.includes(s.stage)) {
          setStuck(false);
          if (prefetching) timer = setTimeout(tick, POLL_MS * 2);
          return;
        }
        const key = `${s.stage}:${s.progress.read}`;
        if (key !== last.current.key) last.current = { key, at: Date.now() };
        setStuck(Date.now() - last.current.at > (s.stage === "uploaded" ? QUEUED_MS : STUCK_MS));
        timer = setTimeout(tick, POLL_MS);
      } catch (e) {
        // a failed poll is retried (and the view read again); the notice goes once a poll gets through
        if (!stop) setPollErr((e as Error).message);
        timer = setTimeout(tick, POLL_MS * 3);
      }
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [caseId, run]);

  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
      last.current = { key: "", at: Date.now() };
      seen.current = "";
      setStuck(false);
      setShowRange(false);
      setRun((n) => n + 1);
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const stage = status?.stage ?? view?.stage ?? "uploaded";
  const busy = RUNNING.includes(stage);
  // the steps render from the full view's own stage: the poll can move on a moment before the new view arrives
  const shownStage = view && view.stage === stage ? stage : null;
  const failedStep = stage === "failed" ? STEP_OF[view?.failed_stage ?? status?.failed_stage ?? "uploaded"] : null;
  const step = failedStep ?? STEP_OF[stage];
  const title = view?.case.title ?? view?.case.case_ref ?? "Noting";
  return (
    <main className="page noting">
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href="/">Cases</a><span className="sep">/</span><span>{title}</span>
      </nav>
      <div className="page-head">
        <div>
          <h1>Prepare Noting</h1>
          <div className="sub">
            {view ? <>{view.case.case_ref}{view.case.society_name ? ` · ${view.case.society_name}` : ""} · {view.page_count} pages</> : "…"}
          </div>
        </div>
        <div className="actions-bar">
          <span className={`pill status lvl-${stage === "failed" ? "problem" : stage === "done" ? "ok" : busy ? "info" : "warning"}`}>
            {busy && <span className="spinner" />} {STAGE_TEXT[stage]}
          </span>
          {view && view.range.confirmed.length > 0 && !busy && stage !== "awaiting_range" && (
            <button className="small" onClick={() => setShowRange((x) => !x)}>{showRange ? "Back" : "Change report pages"}</button>
          )}
          <a className="btn small" href={`/cases/${caseId}/documents`}><Icon name="eye" size={14} /> Pages</a>
        </div>
      </div>
      <ol className="stepper" aria-label="Progress">
        {STEPS.map((s, i) => (
          <li key={s} className={i < step ? "done" : i === step ? (failedStep !== null ? "bad" : "on") : ""}
            aria-current={i === step ? "step" : undefined}>
            <span className="dot">{i < step ? <Icon name="check" size={12} /> : i + 1}</span>{s}
          </li>
        ))}
      </ol>
      {err && <div className="error" role="alert"><Icon name="alert" /> <span>{err}</span></div>}
      {pollErr && (
        <div className="notice row" role="status" title={pollErr}>
          <span className="spinner" /> <span>Could not reach the server — retrying…</span>
        </div>
      )}
      {stage === "failed" && (
        <div className="error stack" role="alert">
          <b>{STAGE_TEXT[(view?.failed_stage ?? status?.failed_stage) as NotingStage] ?? "A step"} failed</b>
          <span>{view?.error ?? status?.error}</span>
          <div className="row">
            <button onClick={() => act(() => send("POST", `/notings/${caseId}/retry`))}><Icon name="refresh" /> Retry</button>
            {view && view.range.proposed && <button onClick={() => setShowRange(true)}>Choose other pages</button>}
          </div>
        </div>
      )}
      {stuck && busy && (
        <div className="notice row">
          <span>{stage === "uploaded" ? "This step has not started yet — the worker may be down or busy."
            : "No progress for a few minutes — the server may have restarted."}</span>
          <button className="small" onClick={() => act(() => send("POST", `/notings/${caseId}/retry?restart=true`))}>
            {stage === "uploaded" ? "Start it again" : "Restart this step"}
          </button>
        </div>
      )}
      {busy && <Working stage={stage} status={status} since={since.current.stage === stage ? since.current.at : null} />}
      {view && !busy && shownStage && (showRange || shownStage === "awaiting_range") && (
        <RangeStep caseId={caseId} view={view} prefetch={status?.prefetch} onReload={() => load().catch(() => undefined)}
          onConfirm={(pages) => act(() => send("POST", `/notings/${caseId}/range`, { pages }))} />
      )}
      {view && !busy && !showRange && (shownStage === "awaiting_review" || shownStage === "done") && (
        <>
          {shownStage === "done" && view.draft && <NotingStep caseId={caseId} view={view}
            onRegenerate={() => act(() => send("POST", `/notings/${caseId}/generate`))} />}
          <FactsStep caseId={caseId} view={view} onSaved={setView} onError={setErr}
            onGenerate={() => act(() => send("POST", `/notings/${caseId}/generate`))} />
        </>
      )}
      {!view && !err && <p className="muted"><span className="spinner" /> Loading…</p>}
    </main>
  );
}

function useElapsed(since: number | null): string | null {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (since === null) return null;
  const s = Math.max(0, Math.round((now - since) / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

function Working({ stage, status, since }: { stage: NotingStage; status: NotingStatus | null; since: number | null }) {
  const p = status?.progress;
  const reading = stage === "reading" && p && p.pages > 0;
  const elapsed = useElapsed(since);
  return (
    <section className="card stack working">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b><span className="spinner" /> {STAGE_TEXT[stage]}…</b>
        {elapsed && <span className="muted">{elapsed}</span>}
      </div>
      {stage === "uploaded" && <span className="muted">Waiting for the worker to pick the report up — this normally takes a few seconds.</span>}
      {reading && (
        <>
          <div className="meter" aria-label={`${p.read} of ${p.pages} pages read`}><div style={{ width: `${(100 * p.read) / p.pages}%` }} /></div>
          <span className="muted">{p.read} of {p.pages} pages read. The pages are read together; a scanned page takes a minute or two
            (two independent readings, the lines they disagree on settled on a close-up, and look-alike digits (१ / ९) checked again),
            so the count jumps when they finish.</span>
        </>
      )}
      {stage === "segmenting" && <span className="muted">Finding blank pages and where the Collector&apos;s letter ends — usually under a
        minute. Annexures are not read.</span>}
      {stage === "extracting" && <span className="muted">Splitting the letter into paragraphs and reading the facts from it.</span>}
      {stage === "generating" && <span className="muted">Assembling the sections and the conditions, then the two .docx renders.</span>}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------
// Step: which pages are the letter
// ------------------------------------------------------------------------------------------------
function RangeStep({ caseId, view, prefetch, onReload, onConfirm }: {
  caseId: string; view: NotingView; prefetch?: NotingStatus["prefetch"]; onReload: () => void; onConfirm: (pages: number[]) => Promise<void>;
}) {
  const initial = view.range.confirmed.length ? view.range.confirmed : view.range.proposed?.pages ?? [];
  const [sel, setSel] = useState<Set<number>>(new Set(initial));
  const [showBlank, setShowBlank] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [sending, setSending] = useState(false);
  const blank = view.pages.filter((p) => p.blank).map((p) => p.page);
  const visible = view.pages.filter((p) => showBlank || !p.blank);
  const pages = showAll ? visible : visible.slice(0, FIRST_THUMBS);
  const hidden = visible.length - pages.length;
  const prop = view.range.proposed;
  const readSet = new Set(prefetch?.read ?? []);
  const readState = (n: number) => (readSet.has(n) ? "read" : prefetch && !prefetch.done && prefetch.pages.includes(n) ? "reading" : null);
  const confirm = async (xs: number[]) => {
    setSending(true);
    await onConfirm(xs);  // on success the step closes; on an error (shown above) the buttons come back
    setSending(false);
  };
  const prefetchNote = prefetch && prefetch.pages.length > 0 && (
    prefetch.pages.every((n) => readSet.has(n))
      ? "These pages are already read — confirming goes straight to the facts."
      : !prefetch.done ? `Already reading these pages (${prefetch.read.length} of ${prefetch.pages.length} done) — confirm now, nothing is lost.`
        : null);
  const toggle = (n: number) => setSel((s) => {
    const t = new Set(s);
    if (t.has(n)) t.delete(n); else t.add(n);
    return t;
  });
  const chosen = [...sel].sort((a, b) => a - b);
  const tooMany = chosen.length > view.range.max_pages;
  const isProposal = !!prop && prop.pages.length > 0 && chosen.join(",") === prop.pages.join(",");
  return (
    <section className="card stack">
      <div>
        <h2 style={{ margin: 0 }}>Which pages are the Collector&apos;s letter?</h2>
        <span className="muted">Only these pages are read. The annexures (the society&apos;s letters, reports, maps, photographs) are not.</span>
      </div>
      {prop && (
        <div className={`proposal lvl-${prop.confidence === "high" ? "ok" : prop.confidence === "medium" ? "warning" : "problem"}`}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <b>Proposed: page{prop.pages.length === 1 ? "" : "s"} {prop.pages.join(", ") || "—"}</b>
            {prop.pages.length > 0 && (
              <button className="primary" disabled={sending} onClick={() => confirm(prop.pages)}>
                <Icon name="check" /> {sending && isProposal ? "Starting…" : `Use page${prop.pages.length === 1 ? "" : "s"} ${prop.pages.join(", ")} & read`}
              </button>
            )}
          </div>
          <span className="pill">{prop.confidence} confidence · {prop.method === "text" ? "from the text layer" : prop.method === "vision" ? "from the page layout" : "guessed from the blank pages"}</span>
          <ul>{prop.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          {prefetchNote && <span className="muted">{prefetchNote}</span>}
        </div>
      )}
      <div className="row muted">
        <span>Or choose the pages below.</span>
        {blank.length > 0 && <span>{blank.length} blank page{blank.length === 1 ? "" : "s"} ({blank.join(", ")}) {showBlank ? "shown" : "hidden"}.</span>}
        {blank.length > 0 && <button className="small ghost" onClick={() => setShowBlank((x) => !x)}>{showBlank ? "Hide blank pages" : "Show blank pages"}</button>}
      </div>
      <div className="thumb-grid">
        {pages.map((p) => <Thumb key={p.page} caseId={caseId} p={p} selected={sel.has(p.page)} read={readState(p.page)}
          onToggle={() => !p.blank && toggle(p.page)} />)}
      </div>
      {hidden > 0 && (
        <button className="ghost" onClick={() => { setShowAll(true); onReload(); }}>Show the other {hidden} pages (annexures)</button>
      )}
      <div className="card row sticky-actions" style={{ justifyContent: "space-between" }}>
        <span className={tooMany ? "error" : "muted"}>
          {chosen.length ? `Selected: ${chosen.join(", ")}` : "Select the letter's pages."}
          {tooMany && ` — at most ${view.range.max_pages} pages`}
        </span>
        <button className="primary" disabled={!chosen.length || tooMany || sending} onClick={() => confirm(chosen)}>
          <Icon name="check" /> {sending ? "Starting…" : "Confirm pages & read"}
        </button>
      </div>
    </section>
  );
}

function Thumb({ caseId, p, selected, read, onToggle }: {
  caseId: string; p: NotingPage; selected: boolean; read: "read" | "reading" | null; onToggle: () => void;
}) {
  const badges: string[] = [];
  if (read === "read") badges.push("read ✓");
  if (read === "reading") badges.push("reading…");
  if (p.signature) badges.push("signature");
  if (p.copy_to) badges.push("प्रति");
  if (p.starts_document && p.issuer) badges.push(p.issuer);
  if (p.kind && p.kind !== "letter") badges.push(KIND_TEXT[p.kind] ?? p.kind);
  return (
    <label className={`thumb${selected ? " selected" : ""}${p.blank ? " blank" : ""}`}>
      <input type="checkbox" checked={selected} disabled={p.blank} onChange={onToggle} />
      <span className="thumb-img">
        {p.has_image ? <img src={pageImageUrl(caseId, p.page, false, 320)} alt={`Page ${p.page}`} loading="lazy" />
          : <span className="muted">{p.blank ? "blank" : "preview coming"}</span>}
      </span>
      <span className="thumb-foot">
        <b>p.{p.page}</b>
        {badges.map((b) => <span key={b} className="badge" title={b}>{b.length > 22 ? `${b.slice(0, 21)}…` : b}</span>)}
      </span>
    </label>
  );
}

// ------------------------------------------------------------------------------------------------
// Step: review the facts
// ------------------------------------------------------------------------------------------------
const NO_FACTS: NotingFacts = { scalars: {}, amounts: [], references: [], history: [], reports: [], instruments: [] };
type Pending = { ops: NotingOp[]; blocks: Record<string, { text?: string; role?: string }>; replace: { from: string; to: string }[] };
const EMPTY: Pending = { ops: [], blocks: {}, replace: [] };

function FactsStep({ caseId, view, onSaved, onError, onGenerate }: {
  caseId: string; view: NotingView; onSaved: (v: NotingView) => void; onError: (m: string | null) => void; onGenerate: () => void;
}) {
  const [pending, setPending] = useState<Pending>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const dirty = pending.ops.length + Object.keys(pending.blocks).length + pending.replace.length > 0;
  const f: NotingFacts = { ...NO_FACTS, ...(view.facts as Partial<NotingFacts>) };  // {} before the letter is read
  const flags = view.validation.flags ?? [];
  const byFact = useMemo(() => {
    const m: Record<string, NotingFlag[]> = {};
    flags.forEach((fl) => fl.facts.forEach((k) => (m[k] = [...(m[k] ?? []), fl])));
    return m;
  }, [flags]);

  const addOp = (op: NotingOp) => setPending((p) => ({
    ...p, ops: [...p.ops.filter((o) => !(o.op === op.op && o.target === op.target && o.name === op.name && o.id === op.id)), op],
  }));
  const save = async () => {
    setSaving(true);
    onError(null);
    try {
      const v = await send<NotingView>("PATCH", `/notings/${caseId}/facts`, {
        facts_version: view.facts_version, ops: pending.ops,
        blocks: Object.entries(pending.blocks).map(([id, b]) => ({ id, ...b })), replace_in_blocks: pending.replace,
      });
      setPending(EMPTY);
      onSaved(v);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const body = view.blocks.filter((b) => view.restated_roles.includes(b.role));
  const other = view.blocks.filter((b) => !view.restated_roles.includes(b.role));
  return (
    <section className="stack facts-step">
      <Flags flags={flags} onFix={(found, expected) => { setFrom(found); setTo(expected); }} />
      <div className="card stack">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <h2 style={{ margin: 0 }}>Facts read from the letter</h2>
            <span className="muted">
              Read by {view.extraction.method === "model" ? "the model from the redacted text" : "pattern matching (no model configured)"},
              each checked against its quote on the page.{view.extraction.rule && <> Rule in force: {view.extraction.rule.rule_set_code}
                {view.extraction.rule.rate_pct ? ` (${Number(view.extraction.rule.rate_pct)} %${view.extraction.rule.status !== "verified" ? ", to verify" : ""})` : ""}.</>}
            </span>
          </div>
          <span className="muted">version {view.facts_version}</span>
        </div>
        {GROUPS.map(([g, names]) => (
          <div key={g} className="fact-group">
            <h3>{g}</h3>
            {names.map((n) => (
              <FactRow key={n} caseId={caseId} name={n} label={view.labels[n] ?? n} fact={f.scalars[n]} flags={byFact[n]}
                pending={pending.ops.find((o) => o.target === "scalar" && o.name === n)} onOp={addOp} />
            ))}
          </div>
        ))}
        <div className="fact-group">
          <h3>Decision</h3>
          <ChoiceRow caseId={caseId} label="Agreement (करारनामा) executed" fact={f.scalars.agreement_executed}
            options={[["", "not stated"], ["true", "yes"], ["false", "no — stamp-duty condition"]]}
            value={pendingValue(pending, "agreement_executed", f.scalars.agreement_executed)}
            onChange={(v) => addOp(v === "" ? { op: "remove", target: "scalar", name: "agreement_executed" }
              : { op: "set", target: "scalar", name: "agreement_executed", value: v === "true" })} />
          <ChoiceRow caseId={caseId} label="Who decides" fact={f.scalars.competence}
            options={[["", "not stated"], ["state_government", "State Government"], ["collector", "Collector"]]}
            value={pendingValue(pending, "competence", f.scalars.competence)}
            onChange={(v) => addOp(v === "" ? { op: "remove", target: "scalar", name: "competence" }
              : { op: "set", target: "scalar", name: "competence", value: v })} />
          {f.scalars.recommendation && (
            <div className="fact-row"><span className="fact-label">Recommendation</span>
              <span className="fact-quote" lang="mr">{String(f.scalars.recommendation.value ?? "")}</span></div>
          )}
        </div>
        <Amounts caseId={caseId} items={f.amounts} byFact={byFact} pending={pending} onOp={addOp} />
        <ReadOnlyList title="References" items={f.references} caseId={caseId} />
        <ReadOnlyList title="Reports relied on" items={f.reports} caseId={caseId} />
        <ReadOnlyList title="History of the land" items={f.history} caseId={caseId} />
      </div>
      <div className="card stack">
        <div>
          <h2 style={{ margin: 0 }}>Paragraphs restated in section 03</h2>
          <span className="muted">The letter&apos;s body, in order. Correct a misread word here; the Noting restates these paragraphs.</span>
        </div>
        <div className="row replace-row">
          <input value={from} onChange={(e) => setFrom(e.target.value)} placeholder="Replace…" lang="mr" />
          <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="with…" lang="mr" />
          <button className="small" disabled={!from.trim()} onClick={() => {
            setPending((p) => ({ ...p, replace: [...p.replace, { from: from.trim(), to: to.trim() }] }));
            setFrom(""); setTo("");
          }}>Replace in paragraphs</button>
          {pending.replace.map((r, i) => <span key={i} className="badge">“{r.from}” → “{r.to}”</span>)}
        </div>
        {body.map((b, i) => (
          <BlockEditor key={b.id} caseId={caseId} n={i + 1} b={b} roles={view.roles} edit={pending.blocks[b.id]}
            onEdit={(e) => setPending((p) => ({ ...p, blocks: { ...p.blocks, [b.id]: { ...p.blocks[b.id], ...e } } }))} />
        ))}
        {other.length > 0 && (
          <details>
            <summary className="muted">Other parts of the letter ({other.length}: letterhead, subject, references, sign-off…) — not restated</summary>
            {other.map((b) => (
              <BlockEditor key={b.id} caseId={caseId} n={null} b={b} roles={view.roles} edit={pending.blocks[b.id]}
                onEdit={(e) => setPending((p) => ({ ...p, blocks: { ...p.blocks, [b.id]: { ...p.blocks[b.id], ...e } } }))} />
            ))}
          </details>
        )}
      </div>
      <div className="card row sticky-actions" style={{ justifyContent: "space-between" }}>
        <span className="muted">{dirty ? "Unsaved changes." : view.validation.ok === false ? "Problems remain — the Noting will show them as blanks or warnings." : "Facts saved."}</span>
        <div className="row">
          <button disabled={!dirty || saving} onClick={() => setPending(EMPTY)}>Discard</button>
          <button className="ok" disabled={!dirty || saving} onClick={save}>{saving ? "Saving…" : "Save"}</button>
          <button className="primary" disabled={dirty || saving} onClick={onGenerate}>
            <Icon name="fileText" /> {view.draft ? "Regenerate Noting" : "Generate Noting"}
          </button>
        </div>
      </div>
    </section>
  );
}

function pendingValue(p: Pending, name: string, fact: NotingFact | undefined): string {
  const op = p.ops.find((o) => o.target === "scalar" && o.name === name);
  if (op) return op.op === "remove" ? "" : String(op.value);
  return fact?.value === undefined || fact?.value === null ? "" : String(fact.value);
}

function Flags({ flags, onFix }: { flags: NotingFlag[]; onFix: (found: string, expected: string) => void }) {
  if (!flags.length) return <div className="success"><Icon name="check" /> All checks passed.</div>;
  const order: NotingFlag["level"][] = ["problem", "warning", "info"];
  return (
    <div className="card stack validation">
      <h2 style={{ margin: 0 }}>Checks</h2>
      {order.map((lvl) => {
        const xs = flags.filter((f) => f.level === lvl);
        if (!xs.length) return null;
        return (
          <div key={lvl} className="stack" style={{ gap: 6 }}>
            <b className="row"><span className={`lvl-icon lvl-${lvl}`}>{LEVEL[lvl].icon}</span> {LEVEL[lvl].label}</b>
            {xs.map((fl, i) => (
              <div key={i} className={`flag lvl-${lvl}`}>
                <span className="mono">{fl.code}</span> {fl.message}
                {fl.code === "FACT_NOT_IN_PARAGRAPHS" && typeof fl.found === "string" && typeof fl.expected === "string" && (
                  <button className="small" onClick={() => onFix(fl.found as string, fl.expected as string)}>Fix in paragraphs</button>
                )}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function FactBadges({ fact, flags }: { fact?: NotingFact; flags?: NotingFlag[] }) {
  if (!fact) return null;
  return (
    <span className="row" style={{ gap: 4 }}>
      {fact.source === "operator" && <span className="badge" title={fact.original ? `was “${fact.original.printed ?? shown(fact.original.value)}”` : ""}>edited</span>}
      {fact.confirmed && <span className="badge">confirmed</span>}
      {fact.grounded === false && fact.source !== "operator" && !fact.confirmed && <span className="badge bad">not found in its quote</span>}
      {fact.ocr_uncertain?.map((s) => <span key={`${s.line}${s.num}`} className="badge warn" title="The OCR doubted this digit">OCR unsure: {s.num} or {s.alt}?</span>)}
      {flags?.filter((fl) => fl.level === "problem").map((fl) => <span key={fl.code} className="badge bad">{fl.code}</span>)}
    </span>
  );
}

function FactRow({ caseId, name, label, fact, flags, pending, onOp }: {
  caseId: string; name: string; label: string; fact?: NotingFact; flags?: NotingFlag[]; pending?: NotingOp; onOp: (op: NotingOp) => void;
}) {
  const current = pending ? (pending.op === "remove" ? "" : pending.printed ?? "") : fact?.printed || (fact?.value != null ? String(fact.value) : "");
  const href = sourceHref(caseId, fact?.page, fact?.quote);
  const needsConfirm = fact && !fact.confirmed && fact.source !== "operator" && (fact.grounded === false || fact.ocr_uncertain?.length);
  return (
    <div className={`fact-row${pending ? " changed" : ""}`}>
      <label className="fact-label" htmlFor={`f-${name}`}>{label}</label>
      <div className="fact-main">
        <div className="row" style={{ gap: 6 }}>
          <input id={`f-${name}`} value={current} lang="mr" onChange={(e) => onOp(e.target.value.trim() === ""
            ? { op: "remove", target: "scalar", name } : { op: "set", target: "scalar", name, printed: e.target.value })} />
          <span className="muted fact-value" title="As read into the noting">{fact && !pending ? shown(fact.value) : ""}</span>
          <FactBadges fact={fact} flags={flags} />
          {needsConfirm && !pending && <button className="small" onClick={() => onOp({ op: "confirm", target: "scalar", name })}>Value is right</button>}
        </div>
        {fact?.quote && (
          <div className="fact-quote" lang="mr">“{fact.quote}”{href && <> <a className="cite" href={href} target="_blank" rel="noreferrer">p.{fact.page}</a></>}</div>
        )}
      </div>
    </div>
  );
}

function ChoiceRow({ caseId, label, fact, options, value, onChange }: {
  caseId: string; label: string; fact?: NotingFact; options: [string, string][]; value: string; onChange: (v: string) => void;
}) {
  const href = sourceHref(caseId, fact?.page, fact?.quote);
  return (
    <div className="fact-row">
      <span className="fact-label">{label}</span>
      <div className="fact-main">
        <div className="row" style={{ gap: 6 }}>
          <select value={value} onChange={(e) => onChange(e.target.value)}>
            {options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
          <FactBadges fact={fact} />
        </div>
        {fact?.quote && <div className="fact-quote" lang="mr">“{fact.quote}”{href && <> <a className="cite" href={href} target="_blank" rel="noreferrer">p.{fact.page}</a></>}</div>}
      </div>
    </div>
  );
}

function Amounts({ caseId, items, byFact, pending, onOp }: {
  caseId: string; items: NotingItem[]; byFact: Record<string, NotingFlag[]>; pending: Pending; onOp: (op: NotingOp) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ kind: "unearned_income", amount_printed: "", paid: true, paid_on: "", challan: "" });
  const edits = (id: string) => pending.ops.find((o) => o.target === "amount" && o.id === id);
  const set = (a: NotingItem, fields: Record<string, unknown>) =>
    onOp({ op: "set", target: "amount", id: a.id, fields: { ...(edits(a.id)?.fields ?? {}), ...fields } });
  return (
    <div className="fact-group">
      <h3>Amounts in the report</h3>
      <table className="data amounts">
        <thead><tr><th>Kind</th><th>Amount</th><th>Paid</th><th>Paid on</th><th>Challan</th><th /></tr></thead>
        <tbody>
          {items.map((a) => {
            const e = (edits(a.id)?.fields ?? {}) as Record<string, unknown>;
            const removed = pending.ops.some((o) => o.target === "amount" && o.id === a.id && o.op === "remove");
            const href = sourceHref(caseId, a.page, a.quote);
            return (
              <tr key={a.id} className={removed ? "removed" : e && Object.keys(e).length ? "changed" : ""}>
                <td>{AMOUNT_KINDS[a.kind ?? "other"] ?? a.kind}<div className="muted" lang="mr">{a.label}</div></td>
                <td><input value={String(e.amount_printed ?? a.printed ?? a.amount ?? "")} onChange={(ev) => set(a, { amount_printed: ev.target.value })} />
                  <FactBadges fact={a} flags={byFact[a.id]} /></td>
                <td><input type="checkbox" checked={Boolean(e.paid ?? a.paid)} onChange={(ev) => set(a, { paid: ev.target.checked })} /></td>
                <td><input value={String(e.paid_on ?? iso(a.paid_on) ?? "")} placeholder="dd/mm/yyyy" onChange={(ev) => set(a, { paid_on: ev.target.value })} /></td>
                <td><input value={String(e.challan ?? a.challan ?? "")} onChange={(ev) => set(a, { challan: ev.target.value })} /></td>
                <td className="row">
                  {href && <a className="cite" href={href} target="_blank" rel="noreferrer">p.{a.page}</a>}
                  <button className="small icon ghost" aria-label="Remove" onClick={() => onOp({ op: "remove", target: "amount", id: a.id })}><Icon name="trash" size={14} /></button>
                </td>
              </tr>
            );
          })}
          {pending.ops.filter((o) => o.target === "amount" && o.op === "add").map((o, i) => (
            <tr key={`new${i}`} className="changed"><td colSpan={6}>New: {AMOUNT_KINDS[String(o.fields?.kind)]} {String(o.fields?.amount_printed)}</td></tr>
          ))}
        </tbody>
      </table>
      {adding ? (
        <div className="row">
          <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
            {Object.entries(AMOUNT_KINDS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}
          </select>
          <input placeholder="Amount" value={draft.amount_printed} onChange={(e) => setDraft({ ...draft, amount_printed: e.target.value })} />
          <label className="row"><input type="checkbox" checked={draft.paid} onChange={(e) => setDraft({ ...draft, paid: e.target.checked })} /> paid</label>
          <input placeholder="Paid on dd/mm/yyyy" value={draft.paid_on} onChange={(e) => setDraft({ ...draft, paid_on: e.target.value })} />
          <input placeholder="Challan" value={draft.challan} onChange={(e) => setDraft({ ...draft, challan: e.target.value })} />
          <button className="small" disabled={!draft.amount_printed.trim()} onClick={() => {
            onOp({ op: "add", target: "amount", id: `new${Date.now()}`, fields: draft });
            setAdding(false);
          }}>Add</button>
        </div>
      ) : <button className="small ghost" onClick={() => setAdding(true)}><Icon name="plus" size={14} /> Add an amount</button>}
    </div>
  );
}

function ReadOnlyList({ title, items, caseId }: { title: string; items: NotingItem[]; caseId: string }) {
  if (!items?.length) return null;
  return (
    <details className="fact-group">
      <summary><b>{title}</b> <span className="muted">({items.length})</span></summary>
      <ul className="plain">
        {items.map((x) => {
          const href = sourceHref(caseId, x.page, x.quote);
          return (
            <li key={x.id} lang="mr">
              {x.date && <span className="muted">{iso(x.date)} · </span>}{x.by ? `${x.by}: ` : ""}{x.text}
              {href && <> <a className="cite" href={href} target="_blank" rel="noreferrer">p.{x.page}</a></>}
              {x.grounded === false && <span className="badge bad">not found in its quote</span>}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function BlockEditor({ caseId, n, b, roles, edit, onEdit }: {
  caseId: string; n: number | null; b: NotingBlock; roles: string[]; edit?: { text?: string; role?: string };
  onEdit: (e: { text?: string; role?: string }) => void;
}) {
  const href = sourceHref(caseId, b.pages[0]);
  return (
    <div className={`block-row${edit ? " changed" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="row" style={{ gap: 6 }}>
          {n !== null && <b className="mono">3.{n}</b>}
          <select value={edit?.role ?? b.role} onChange={(e) => onEdit({ role: e.target.value })} aria-label="Role">
            {roles.map((r) => <option key={r} value={r}>{ROLE_TEXT[r] ?? r}</option>)}
          </select>
          {b.edited && <span className="badge" title={b.original_text}>edited</span>}
        </span>
        {href && <a className="cite" href={href} target="_blank" rel="noreferrer">p.{b.pages.join(", ")}</a>}
      </div>
      <textarea lang="mr" rows={Math.min(8, Math.max(2, Math.ceil(b.text.length / 110)))} value={edit?.text ?? b.text}
        onChange={(e) => onEdit({ text: e.target.value })} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------
// Step: the Noting
// ------------------------------------------------------------------------------------------------
function NotingStep({ caseId, view, onRegenerate }: { caseId: string; view: NotingView; onRegenerate: () => void }) {
  const d = view.draft!;
  // the clean Noting is shown at once, laid out as it prints; the internal render (with its notes) a tab away
  const issueShown = !!d.issue?.downloadable;
  const [tab, setTab] = useState<"issue" | "internal">(issueShown ? "issue" : "internal");
  const [review, setReview] = useState(false);
  const [ratings, setRatings] = useState<DraftsView["my_ratings"]>({});
  const loadRatings = () => get<DraftsView>(`/cases/${caseId}/drafts`).then((v) => setRatings(v.my_ratings)).catch(() => undefined);
  useEffect(() => { loadRatings(); }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps
  const shownId = tab === "issue" && issueShown ? d.issue?.id : d.internal?.downloadable ? d.internal.id : undefined;
  return (
    <section className="card stack noting-ready">
      {d.stale && (
        <div className="notice row" style={{ justifyContent: "space-between" }}>
          <span><Icon name="alert" /> The facts changed after this Noting was written (v{d.version}).</span>
          <button className="small primary" onClick={onRegenerate}><Icon name="refresh" size={14} /> Regenerate</button>
        </div>
      )}
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h2 style={{ margin: 0 }}>टिप्पणी <small className="muted">Noting v{d.version}</small></h2>
          <span className="muted">Internal: provenance notes and the “AI-सहाय्यित मसुदा” watermark. Issue: clean, ready to edit in Word.</span>
        </div>
        <div className="row">
          {d.internal?.downloadable && <a className="btn" href={`/api/rci/drafts/${d.internal.id}/download`}>Internal .docx</a>}
          {d.issue?.downloadable && <a className="btn primary" href={`/api/rci/drafts/${d.issue.id}/download`}><Icon name="fileText" /> Noting .docx</a>}
          {d.issue && <button onClick={() => setReview((x) => !x)}>{review ? "Hide upload & rating" : "Upload edited copy / rate"}</button>}
        </div>
      </div>
      {d.issue && <Checks d={d.issue} />}
      {review && d.issue && <Detail issueId={d.issue.id} internalId={d.internal?.id} rating={ratings.NOTING} onChange={loadRatings} preview={false} />}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "issue"} className={tab === "issue" ? "on" : ""} disabled={!issueShown}
          title={issueShown ? undefined : "The issue render was withheld: its leak check failed"} onClick={() => setTab("issue")}>Noting</button>
        <button role="tab" aria-selected={tab === "internal"} className={tab === "internal" ? "on" : ""} onClick={() => setTab("internal")}>
          Internal (with notes)
        </button>
      </div>
      {shownId ? <DocPreview key={shownId} draftId={shownId} /> : <p className="muted">No render to preview.</p>}
    </section>
  );
}
