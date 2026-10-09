"use client";
import { useEffect, useMemo, useState } from "react";
import type { ClauseMeta, ClauseRow } from "@/lib/api";
import { get, send } from "@/lib/client";

const KIND_TEXT: Record<string, string> = {
  query_letter: "त्रुटी पत्र", demand_letter: "मागणी पत्र", collector_order: "जिल्हाधिकारी आदेश", scrutiny_note: "छाननी टिप्पणी",
};
const STATUS_COLOUR: Record<string, string> = { canonical: "var(--ok)", candidate: "var(--warn)", rejected: "var(--muted)" };

export default function ClausesApp({ isAdmin, canEdit }: { isAdmin: boolean; canEdit: boolean }) {
  const [meta, setMeta] = useState<ClauseMeta | null>(null);
  const [kind, setKind] = useState("query_letter");
  const [status, setStatus] = useState("");
  const [rows, setRows] = useState<ClauseRow[]>([]);
  const [sel, setSel] = useState<ClauseRow | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const q = new URLSearchParams({ kind, ...(status ? { status } : {}) });
    const r = await get<ClauseRow[]>(`/clauses?${q}`);
    setRows(r);
    if (sel) setSel(r.find((x) => x.id === sel.id) ?? null);
  };
  useEffect(() => { get<ClauseMeta>("/clauses/meta").then(setMeta); }, []);
  useEffect(() => { load(); }, [kind, status]); // eslint-disable-line react-hooks/exhaustive-deps

  const mine = async () => {
    setBusy(true);
    try {
      const r = await send<{ cases: number; distinct: number; created: number }>("POST", "/clauses/mine", {});
      setMsg(`Mined ${r.cases} precedent case(s): ${r.distinct} distinct clauses, ${r.created} new.`);
      await load();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const groups = useMemo(() => {
    const g = new Map<string, ClauseRow[]>();
    for (const r of rows) g.set(r.clause_key, [...(g.get(r.clause_key) ?? []), r]);
    return [...g.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [rows]);

  return (
    <main className="page" style={{ maxWidth: 1400 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0 }}>Clause library</h1>
        <div className="row">
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="status">
            <option value="">all statuses</option><option value="canonical">canonical</option>
            <option value="candidate">candidate</option><option value="rejected">rejected</option>
          </select>
          {isAdmin && <button className="primary" onClick={mine} disabled={busy}>{busy ? "Mining…" : "Mine precedents"}</button>}
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12.5 }}>
        Sentences mined from precedent letters and orders (holdouts never), with the case’s own facts replaced by slots and grouped into
        clusters of similar wording. Pick the canonical wording per clause key; drafts use the most specific canonical clause whose
        conditions hold. Seed wording ships with the system (to_verify) and is the fallback.
      </p>
      {msg && <div className="notice" style={{ marginBottom: 8 }}>{msg}</div>}
      <div className="tabs" style={{ position: "static" }}>
        {(meta?.kinds ?? Object.keys(KIND_TEXT)).map((k) => (
          <button key={k} className={k === kind ? "on" : ""} onClick={() => { setKind(k); setSel(null); }}>{KIND_TEXT[k] ?? k}</button>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.1fr) minmax(0, 1fr)", gap: 14, marginTop: 10, alignItems: "start" }}>
        <div className="stack" style={{ maxHeight: "75vh", overflow: "auto" }}>
          {groups.map(([key, rs]) => (
            <section key={key} className="card" style={{ padding: "8px 10px" }}>
              <div className="mono" style={{ fontWeight: 600 }}>{key}</div>
              {rs.map((r) => (
                <div key={r.id} onClick={() => setSel(r)}
                     style={{ cursor: "pointer", padding: "5px 6px", marginTop: 4, borderRadius: 4, background: sel?.id === r.id ? "var(--accent-soft)" : undefined }}>
                  <div className="row" style={{ gap: 5, fontSize: 11.5 }}>
                    <span className="pill" style={{ color: STATUS_COLOUR[r.status] }}>{r.status}</span>
                    <span className="badge">{r.origin}</span><span className="badge">{r.data_class}</span>
                    {r.cluster_id != null && <span className="badge">cluster {r.cluster_id}</span>}
                    {r.origin !== "seed" && <span className="badge">{r.member_count}× in {r.n_sources} case(s)</span>}
                    {Object.keys(r.conditions).length > 0 && <span className="badge">if {JSON.stringify(r.conditions)}</span>}
                    {r.flags.needs_slots ? <span className="badge" style={{ color: "var(--warn)" }}>needs slots</span> : null}
                    {r.flags.has_pii ? <span className="badge" style={{ color: "var(--bad)" }}>PII token</span> : null}
                    {r.verification === "verified" && <span className="badge" style={{ color: "var(--ok)" }}>verified</span>}
                  </div>
                  <div style={{ fontSize: 13, marginTop: 2 }}>{r.template ?? r.text_mr}</div>
                </div>
              ))}
            </section>
          ))}
          {!groups.length && <p className="muted">No clauses of this kind{status ? ` with status ${status}` : ""}.</p>}
        </div>
        <div style={{ position: "sticky", top: 10 }}>
          {sel ? <Editor key={sel.id} c={sel} meta={meta} canEdit={canEdit} isAdmin={isAdmin} onSaved={(m) => { setMsg(m); load(); }} />
            : <div className="card muted">Select a clause to curate it.</div>}
        </div>
      </div>
    </main>
  );
}

function Editor({ c, meta, canEdit, isAdmin, onSaved }: { c: ClauseRow; meta: ClauseMeta | null; canEdit: boolean; isAdmin: boolean; onSaved: (m: string) => void }) {
  const [key, setKey] = useState(c.clause_key);
  const [tpl, setTpl] = useState(c.template ?? c.text_mr);
  const [cond, setCond] = useState(JSON.stringify(c.conditions));
  const [rules, setRules] = useState(c.rule_set_codes.join(", "));
  const [caseId, setCaseId] = useState("");
  const [preview, setPreview] = useState<{ text: string | null; missing: string[]; problems: string[]; slots_from: string } | null>(null);
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    const t = setTimeout(() => {
      send<typeof preview>("POST", "/clauses/preview", { template: tpl, clause_key: key, case_id: caseId || null }).then(setPreview).catch(() => undefined);
    }, 250);
    return () => clearTimeout(t);
  }, [tpl, key, caseId]);

  const save = async (patch: Record<string, unknown>, label: string) => {
    let conditions: Record<string, unknown>;
    try {
      conditions = cond.trim() ? JSON.parse(cond) : {};
    } catch {
      setErrors(["conditions must be JSON, e.g. {\"tenure\": \"govt_lease\"}"]);
      return;
    }
    try {
      await send("PATCH", `/clauses/${c.id}`, {
        clause_key: key, template: tpl, conditions,
        rule_set_codes: rules.split(",").map((s) => s.trim()).filter(Boolean), ...patch,
      });
      setErrors([]);
      onSaved(`${key}: ${label}`);
    } catch (e) {
      const m = (e as Error).message;
      try {
        setErrors((JSON.parse(m) as { errors: string[] }).errors);
      } catch {
        setErrors([m]);
      }
    }
  };
  const slots = key.startsWith("noting.") ? meta?.noting_slots ?? []
    : [...(meta?.slots ?? []), ...(key.startsWith("query_item.") ? meta?.item_slots ?? [] : [])];
  return (
    <section className="card stack">
      <label>Clause key <input value={key} onChange={(e) => setKey(e.target.value)} style={{ width: "100%" }} disabled={!canEdit} /></label>
      <label>Wording (Jinja; every number / date must be a slot)
        <textarea value={tpl} onChange={(e) => setTpl(e.target.value)} rows={5} style={{ width: "100%", fontSize: 14 }} disabled={!canEdit} />
      </label>
      <div className="row" style={{ gap: 4 }}>
        {slots.map((s) => (
          <button key={s.name} className="small" title={`${s.description} — e.g. ${String(s.example)}`} disabled={!canEdit}
                  onClick={() => setTpl((t) => `${t}{{ ${s.name}${/date/.test(s.name) ? "|mr_date" : /amount|premium|rate$/.test(s.name) ? "|inr|mr_num" : /area|cts/.test(s.name) ? "|mr_num" : ""} }}`)}>
            {s.name}
          </button>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 11.5 }}>Filters: {meta?.filters.join(", ")}. Conditions: {meta?.condition_keys.join(", ")}.</div>
      <div className="grid2">
        <label>Conditions (JSON) <input value={cond} onChange={(e) => setCond(e.target.value)} placeholder='{"tenure": "govt_lease"}' disabled={!canEdit} /></label>
        <label>Rule sets (empty = any) <input value={rules} onChange={(e) => setRules(e.target.value)} placeholder="RULES_2019" disabled={!canEdit} /></label>
      </div>
      <div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <b style={{ fontSize: 12.5 }}>Preview ({preview?.slots_from ?? "…"})</b>
          <input value={caseId} onChange={(e) => setCaseId(e.target.value.trim())} placeholder="case id for real slot values (optional)" style={{ width: 260, fontSize: 11.5 }} />
        </div>
        <div style={{ background: "var(--panel-2)", padding: 8, borderRadius: 4, marginTop: 4, fontSize: 14 }}>{preview?.text ?? "—"}</div>
        {(preview?.problems.length ?? 0) > 0 && <ul className="error" style={{ margin: "6px 0", paddingLeft: 22, fontSize: 12.5 }}>{preview?.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
        {(preview?.missing.length ?? 0) > 0 && <div className="muted" style={{ fontSize: 12 }}>blank in this preview: {preview?.missing.join(", ")}</div>}
      </div>
      {errors.length > 0 && <ul className="error" style={{ margin: 0, paddingLeft: 22 }}>{errors.map((e) => <li key={e}>{e}</li>)}</ul>}
      {c.source_refs.length > 0 && (
        <div style={{ fontSize: 12 }}>
          <b>Found in</b>{" "}
          {c.source_refs.map((r, i) => (
            <a key={i} className="cite" style={{ marginRight: 6 }} href={r.page_seq ? `/cases/${r.case_id}/source/${r.page_seq}` : `/cases/${r.case_id}`} target="_blank" rel="noreferrer">
              {r.case_ref}{r.page_seq ? ` p.${r.page_seq}` : ""}
            </a>
          ))}
          {c.n_sources > c.source_refs.length && <span className="muted">+{c.n_sources - c.source_refs.length} more</span>}
          <div className="muted">Source wording: {c.text_mr}</div>
        </div>
      )}
      {canEdit && (
        <div className="row">
          <button className="primary" onClick={() => save({ status: "canonical" }, "canonical")}>Make canonical</button>
          <button onClick={() => save({ status: c.status === "canonical" ? "canonical" : "candidate" }, "saved")}>Save</button>
          {c.status !== "rejected" && <button className="danger" onClick={() => save({ status: "rejected" }, "rejected")}>Reject</button>}
          {isAdmin && (
            <button className="ok" onClick={() => save({ verification: c.verification === "verified" ? "to_verify" : "verified" }, "verification changed")}>
              {c.verification === "verified" ? "Mark to_verify" : "Mark verified (department-confirmed)"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
