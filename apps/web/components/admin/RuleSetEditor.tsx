"use client";
import { useCallback, useEffect, useState } from "react";
import { STATUS_COLOUR } from "@/lib/api";
import { get, send } from "@/lib/client";
import Icon from "../Icon";

type Row = Record<string, unknown> & { id: string; status: string; seed_key: string | null; source_ref: string | null };
type CatalogDoc = { code: string; name_en: string; name_mr: string | null; category: string; intake_slot: string | null };
type Slot = { key: string; label: string; label_mr: string };
type RuleSet = {
  id: string; code: string; name: string; kind: string; notification_ref: string | null; notification_date: string | null;
  effective_from: string; effective_to: string | null; supersedes_code: string | null; status: string; notes: string | null;
  source_filename: string | null; has_source: boolean;
  slabs: Row[]; requirements: Row[]; obligations: Row[]; adjustments: Row[]; document_checks: Row[];
  catalog: CatalogDoc[]; slots: Slot[];
};
type Table = "slabs" | "requirements" | "obligations" | "adjustments" | "document_checks";
type Act = (fn: () => Promise<unknown>, ok?: string) => Promise<void>;

// Which land a row applies to: the only fact rule conditions are decided on at intake is the tenure.
const APPLIES: [string, string][] = [["", "All land"], ["govt_lease", "Leasehold land only"], ["occupancy_class_2", "Class-II land only"]];
const SEVERITY: [string, string][] = [["problem", "Problem — blocks the case"], ["warning", "Warning — needs a look"], ["info", "Info"]];

function tenureOf(cond: unknown): string | null {
  const c = (cond ?? {}) as Record<string, unknown>;
  const keys = Object.keys(c);
  if (!keys.length) return "";
  return keys.length === 1 && typeof c.tenure === "string" && APPLIES.some(([k]) => k === c.tenure) ? c.tenure : null;
}

// A code for a new row, from its text: the stacking of rule sets matches rows by code.
function codeFrom(text: string, taken: Set<string>): string {
  const base = text.toUpperCase().replace(/[^A-Z0-9]+/g, " ").trim().split(" ").slice(0, 6).join("_").slice(0, 56) || "RULE";
  let code = base;
  for (let i = 2; taken.has(code); i++) code = `${base}_${i}`;
  return code;
}

export default function RuleSetEditor({ id }: { id: string }) {
  const [rs, setRs] = useState<RuleSet | null>(null);
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => setRs(await get<RuleSet>(`/admin/rule-sets/${id}`)), [id]);
  useEffect(() => { load().catch((e) => setMsg({ kind: "error", text: e.message })); }, [load]);

  const act: Act = async (fn, ok) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      await load();
      if (ok) setMsg({ kind: "success", text: ok });
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  if (!rs) {
    return (
      <main className="page">
        {msg ? <div className={msg.kind}><Icon name="alert" /> <span>{msg.text}</span></div> : <span className="muted"><span className="spinner" /> Loading…</span>}
      </main>
    );
  }

  const patchSet = (body: Record<string, unknown>, ok?: string) => act(() => send("PATCH", `/admin/rule-sets/${id}`, body), ok);
  const patchRow = (t: Table, r: Row, body: Record<string, unknown>, ok?: string) => act(() => send("PATCH", `/admin/rules/${t}/${r.id}`, body), ok);
  const addRow = (t: Table, body: Record<string, unknown>) => act(() => send("POST", `/admin/rule-sets/${id}/${t}`, body), "Rule added (to_verify)");
  const upload = (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return act(() => send("POST", `/admin/rule-sets/${id}/source`, fd), `Uploaded ${file.name}`);
  };
  const docName = (code: unknown) => rs.catalog.find((c) => c.code === code);
  const rowProps = { busy, patchRow, act };
  const isVerified = rs.status === "verified";

  return (
    <main className="page" style={{ maxWidth: 1200 }}>
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href="/admin/rules">Rules</a><span className="sep">/</span><span>{rs.code}</span>
      </nav>
      <div className="card hero">
        <div style={{ minWidth: 0 }}>
          <h1 style={{ margin: 0 }}>{rs.name}</h1>
          <div className="hero-meta">
            <span className="mono">{rs.code}</span>
            <span><Icon name="clock" size={14} /> In force {rs.effective_from} → {rs.effective_to ?? "open"}</span>
            <span className="pill status" style={{ color: STATUS_COLOUR[rs.status] }}>{rs.status}</span>
          </div>
        </div>
        <button className={isVerified ? "" : "ok"} disabled={busy}
          onClick={() => patchSet({ status: isVerified ? "to_verify" : "verified" },
            isVerified ? "Rule set set back to to_verify" : "Rule set verified")}>
          <Icon name={isVerified ? "refresh" : "checkCircle"} /> {isVerified ? "Mark to_verify" : "Mark verified"}
        </button>
      </div>
      {msg && <div className={msg.kind} style={{ marginTop: 12 }}><Icon name={msg.kind === "error" ? "alert" : "check"} /> <span>{msg.text}</span></div>}

      <div className="callout">
        <Icon name="info" />
        <span>
          These rules are applied to every case filed while the rule set is in force: they decide which documents the case needs and
          what the AI checks inside each document. A rule still <b>to_verify</b> is applied, but what it finds is at most a warning.
          To verify a rule, upload the notification below and fill in the rule&apos;s clause (source ref).
        </span>
      </div>

      <details className="card">
        <summary>
          <Icon name="fileText" style={{ color: "var(--muted)" }} />
          <b>Notification</b>
          {rs.has_source
            ? <a href={`/api/rci/admin/rule-sets/${id}/source`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{rs.source_filename}</a>
            : <span className="pill status lvl-warning">not uploaded — needed before anything here can be verified</span>}
        </summary>
        <div className="grid2" style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--line)" }}>
          <div className="stack">
            <Field label="Name" value={rs.name} onSave={(v) => patchSet({ name: v })} wide />
            <Field label="Notification ref" value={rs.notification_ref ?? ""} onSave={(v) => patchSet({ notification_ref: v || null })} wide />
            <Field label="Notes" value={rs.notes ?? ""} onSave={(v) => patchSet({ notes: v || null })} wide />
            <label style={{ display: "grid", gridTemplateColumns: "130px 1fr", alignItems: "center" }}>Upload notification
              <input type="file" accept=".pdf,.png,.jpg,.jpeg,.txt" disabled={busy} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            </label>
          </div>
          <div className="stack">
            <div className="row" style={{ gap: 12 }}>
              <Field label="Notified" type="date" value={rs.notification_date ?? ""} onSave={(v) => patchSet({ notification_date: v || null })} />
              <Field label="In force from" type="date" value={rs.effective_from} onSave={(v) => patchSet({ effective_from: v })} />
              <Field label="to" type="date" value={rs.effective_to ?? ""} onSave={(v) => patchSet({ effective_to: v || null })} />
            </div>
            <div className="row" style={{ gap: 12 }}>
              <label className="inline">Kind
                <select value={rs.kind} disabled={busy} onChange={(e) => patchSet({ kind: e.target.value })}>
                  {["base", "amendment", "circular", "amnesty"].map((k) => <option key={k}>{k}</option>)}
                </select>
              </label>
              <Field label="Replaces rule set" value={rs.supersedes_code ?? ""} onSave={(v) => patchSet({ supersedes_code: v.toUpperCase() || null })} />
            </div>
          </div>
        </div>
      </details>

      {/* ------------------------------------------------------------ required documents */}
      <section>
        <div className="section-head">
          <h2>Documents the case must include <Count rows={rs.requirements} /></h2>
          <p>A required document that is missing blocks the case. Optional ones are recognised when filed (and checked below) but not asked for.</p>
        </div>
        <div className="card table-card">
          <div className="scroll">
            <table className="data">
              <thead><tr><th>Document</th><th>Required</th><th>Applies to</th><th>Source ref</th><th>Status</th><th /></tr></thead>
              <tbody>
                {[...rs.requirements].sort((a, b) => Number(Boolean(b.mandatory)) - Number(Boolean(a.mandatory))).map((r) => {
                  const d = docName(r.requirement_code);
                  return (
                    <tr key={r.id}>
                      <td><b style={{ fontWeight: 550 }}>{d?.name_en ?? String(r.requirement_code)}</b>{d?.name_mr && <div className="muted" style={{ fontSize: 12 }}>{d.name_mr}</div>}</td>
                      <td><input type="checkbox" checked={Boolean(r.mandatory)} disabled={busy} aria-label="Required" onChange={(e) => patchRow("requirements", r, { mandatory: e.target.checked })} /></td>
                      <td><AppliesTo row={r} disabled={busy} onSave={(c) => patchRow("requirements", r, { condition: c })} /></td>
                      <td><Text value={r.source_ref} disabled={busy} width={140} placeholder="clause" onSave={(v) => patchRow("requirements", r, { source_ref: v })} /></td>
                      <RowStatus table="requirements" row={r} {...rowProps} />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        <AddRequirement rs={rs} busy={busy} onAdd={(b) => addRow("requirements", b)} />
      </section>

      {/* ------------------------------------------------------------ document checks, by document */}
      <section>
        <div className="section-head">
          <h2>What the AI checks in each document <Count rows={rs.document_checks} /></h2>
          <p>
            Write each rule as one plain sentence saying what a correct document shows: the case report prints &quot;Not met: …&quot; with it
            when the document falls short. For additional documents, start with &quot;If this is …&quot; so other kinds of document are skipped.
          </p>
        </div>
        {rs.slots.map((s) => {
          const rows = rs.document_checks.filter((r) => r.slot === s.key);
          return (
            <div key={s.key} className="card slot-card">
              <div className="slot-card-head">
                <span className="doc-icon" style={{ width: 32, height: 32, borderRadius: 8 }}><Icon name="fileText" size={15} /></span>
                <h3>{s.label}</h3>
                <span className="muted" style={{ fontSize: 13 }}>{s.label_mr}</span>
                <span className="count-pill" style={{ marginLeft: "auto" }}>{rows.length} check{rows.length === 1 ? "" : "s"}</span>
              </div>
              <div className="slot-card-body">
                {rows.map((r) => (
                  <div key={r.id} className="check-row">
                    <div className="stack" style={{ flex: 1, gap: 6, minWidth: 0 }}>
                      <Text value={r.text_en} disabled={busy} multiline onSave={(v) => v && patchRow("document_checks", r, { text_en: v })} />
                      <Text value={r.text_mr} disabled={busy} placeholder="मराठी (optional)" onSave={(v) => patchRow("document_checks", r, { text_mr: v })} />
                      <div className="check-controls">
                        <select value={String(r.severity)} disabled={busy} aria-label="Severity" onChange={(e) => patchRow("document_checks", r, { severity: e.target.value })}>
                          {SEVERITY.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                        </select>
                        <AppliesTo row={r} disabled={busy} onSave={(c) => patchRow("document_checks", r, { condition: c })} />
                        <Text value={r.source_ref} disabled={busy} width={160} placeholder="source ref (clause)" onSave={(v) => patchRow("document_checks", r, { source_ref: v })} />
                      </div>
                    </div>
                    <div className="row-status"><RowStatus table="document_checks" row={r} cell={false} {...rowProps} /></div>
                  </div>
                ))}
                {!rows.length && <p className="muted" style={{ margin: "12px 0 0", fontSize: 13 }}>No checks for this document yet.</p>}
                <AddCheck slot={s.key} taken={new Set(rs.document_checks.map((r) => String(r.code)))} busy={busy}
                  onAdd={(b) => addRow("document_checks", b)} />
              </div>
            </div>
          );
        })}
      </section>

      {/* ------------------------------------------------------------ obligations */}
      <section>
        <div className="section-head">
          <h2>What the society must do before the order <Count rows={rs.obligations} /></h2>
        </div>
        <div className="card table-card">
          <div className="scroll">
            <table className="data">
              <thead><tr><th>Obligation</th><th>Applies to</th><th>Source ref</th><th>Status</th><th /></tr></thead>
              <tbody>
                {rs.obligations.map((r) => (
                  <tr key={r.id}>
                    <td style={{ minWidth: 380 }}>
                      <div className="stack" style={{ gap: 6 }}>
                        <Text value={r.description_en} disabled={busy} multiline onSave={(v) => patchRow("obligations", r, { description_en: v })} />
                        <Text value={r.description_mr} disabled={busy} placeholder="मराठी" onSave={(v) => patchRow("obligations", r, { description_mr: v })} />
                      </div>
                    </td>
                    <td><AppliesTo row={r} disabled={busy} onSave={(c) => patchRow("obligations", r, { condition: c })} /></td>
                    <td><Text value={r.source_ref} disabled={busy} width={140} placeholder="clause" onSave={(v) => patchRow("obligations", r, { source_ref: v })} /></td>
                    <RowStatus table="obligations" row={r} {...rowProps} />
                  </tr>
                ))}
                {!rs.obligations.length && <tr><td colSpan={5} className="muted">None yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <AddObligation taken={new Set(rs.obligations.map((r) => String(r.code)))} busy={busy} onAdd={(b) => addRow("obligations", b)} />
      </section>

      {/* ------------------------------------------------------------ fees (not used at intake) */}
      <details className="card" style={{ marginTop: 36 }}>
        <summary><Icon name="layers" style={{ color: "var(--muted)" }} /> <b>Fees</b> <span className="muted">— premium slabs and adjustments, used only by the fee calculation</span></summary>
        <FeeTable title="Premium slabs" table="slabs" rows={rs.slabs} {...rowProps} onAdd={() => addRow("slabs", {
          applicant_category: "CHS", holding_basis: "govt_lease", land_use: "residential", rate_basis: "asr_land_value" })}
          cols={[["holding_basis", "Holding"], ["land_use", "Land use"], ["rate_pct", "Rate %"], ["rate_basis", "Basis"],
            ["window_from", "From", "date"], ["window_to", "To", "date"]]} />
        <FeeTable title="Adjustments" table="adjustments" rows={rs.adjustments} {...rowProps} onAdd={() => addRow("adjustments", {
          code: codeFrom("adjustment", new Set(rs.adjustments.map((r) => String(r.code)))), kind: "discount" })}
          cols={[["code", "Code"], ["kind", "Kind"], ["rate_pct", "Rate %"], ["amount", "Amount"],
            ["applies_from", "From", "date"], ["applies_to", "To", "date"]]} />
      </details>

      <p className="footnote">
        Editing a verified rule sets it back to to_verify. Rules that came with the system can be switched off only by editing them
        (e.g. untick Required); rules you added can be deleted.
      </p>
    </main>
  );
}

// ------------------------------------------------------------------------------------------------
function Count({ rows }: { rows: Row[] }) {
  const n = rows.filter((r) => r.status === "verified").length;
  return <span className={`count-pill${rows.length && n === rows.length ? " all" : ""}`}>{n}/{rows.length} verified</span>;
}

// Status pill, verify toggle and delete (rules added here only); as two table cells unless `cell` is false.
function RowStatus({ table, row, busy, patchRow, act, cell = true }: {
  table: Table; row: Row; busy: boolean; act: Act; cell?: boolean;
  patchRow: (t: Table, r: Row, body: Record<string, unknown>, ok?: string) => Promise<void>;
}) {
  const on = row.status === "verified";
  const status = (
    <>
      <span className="pill status" style={{ color: STATUS_COLOUR[row.status] }}>{row.status}</span>
      <button className={`small${on ? "" : " ok"}`} disabled={busy}
        onClick={() => patchRow(table, row, { status: on ? "to_verify" : "verified" }, on ? "Set back to to_verify" : "Verified")}>
        {on ? "unverify" : "verify"}
      </button>
    </>
  );
  const del = !row.seed_key && (
    <button className="small icon danger" disabled={busy} title="Delete" aria-label="Delete rule"
      onClick={() => confirm("Delete this rule?") && act(() => send("DELETE", `/admin/rules/${table}/${row.id}`), "Rule deleted")}><Icon name="trash" size={13} /></button>
  );
  if (!cell) return <>{status}{del}</>;
  return <><td><span className="row-status">{status}</span></td><td>{del}</td></>;
}

function AppliesTo({ row, onSave, disabled }: { row: Row; onSave: (c: Record<string, unknown>) => void; disabled: boolean }) {
  const t = tenureOf(row.condition);
  if (t === null) return <span className="mono" style={{ fontSize: 11 }} title="Custom condition">{JSON.stringify(row.condition)}</span>;
  return (
    <select value={t} disabled={disabled} onChange={(e) => onSave(e.target.value ? { tenure: e.target.value } : {})}>
      {APPLIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  );
}

function AddRequirement({ rs, busy, onAdd }: { rs: RuleSet; busy: boolean; onAdd: (b: Record<string, unknown>) => Promise<void> }) {
  const have = new Set(rs.requirements.map((r) => r.requirement_code));
  const left = rs.catalog.filter((c) => c.category === "applicant_document" && !have.has(c.code));
  const [code, setCode] = useState("");
  const [applies, setApplies] = useState("");
  if (!left.length) return null;
  return (
    <div className="add-row">
      <select value={code} onChange={(e) => setCode(e.target.value)}>
        <option value="">Add a document…</option>
        {left.map((c) => <option key={c.code} value={c.code}>{c.name_en}</option>)}
      </select>
      <select value={applies} onChange={(e) => setApplies(e.target.value)}>
        {APPLIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      <button className="small" disabled={busy || !code}
        onClick={() => onAdd({ requirement_code: code, mandatory: true, condition: applies ? { tenure: applies } : {} }).then(() => setCode(""))}>
        <Icon name="plus" size={14} /> Add as required
      </button>
    </div>
  );
}

function AddCheck({ slot, taken, busy, onAdd }: { slot: string; taken: Set<string>; busy: boolean; onAdd: (b: Record<string, unknown>) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ text_en: "", text_mr: "", severity: "warning", applies: "" });
  if (!open) return <button className="small" style={{ marginTop: 12 }} disabled={busy} onClick={() => setOpen(true)}><Icon name="plus" size={14} /> Add a check</button>;
  const add = () => onAdd({
    code: codeFrom(f.text_en, taken), slot, text_en: f.text_en.trim(), text_mr: f.text_mr.trim() || null, severity: f.severity,
    condition: f.applies ? { tenure: f.applies } : {},
  }).then(() => { setOpen(false); setF({ text_en: "", text_mr: "", severity: "warning", applies: "" }); });
  return (
    <div className="stack add-panel" style={{ gap: 8 }}>
      <textarea rows={2} autoFocus placeholder={slot === "other" ? "If this is …, it shows …" : "The document shows …"}
        value={f.text_en} onChange={(e) => setF({ ...f, text_en: e.target.value })} />
      <input placeholder="मराठी (optional)" value={f.text_mr} onChange={(e) => setF({ ...f, text_mr: e.target.value })} />
      <div className="row" style={{ gap: 8 }}>
        <select value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>
          {SEVERITY.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <select value={f.applies} onChange={(e) => setF({ ...f, applies: e.target.value })}>
          {APPLIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <button className="primary small" disabled={busy || !f.text_en.trim()} onClick={add}>Add check</button>
        <button className="small" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}

function AddObligation({ taken, busy, onAdd }: { taken: Set<string>; busy: boolean; onAdd: (b: Record<string, unknown>) => Promise<void> }) {
  const [en, setEn] = useState("");
  const [mr, setMr] = useState("");
  return (
    <div className="add-row">
      <input placeholder="Add an obligation, e.g. Pay the premium before …" value={en} onChange={(e) => setEn(e.target.value)} style={{ flex: 2 }} />
      <input placeholder="मराठी" value={mr} onChange={(e) => setMr(e.target.value)} style={{ flex: 1 }} />
      <button className="small" disabled={busy || !en.trim()}
        onClick={() => onAdd({ code: codeFrom(en, taken), description_en: en.trim(), description_mr: mr.trim() || null })
          .then(() => { setEn(""); setMr(""); })}><Icon name="plus" size={14} /> Add</button>
    </div>
  );
}

function FeeTable({ title, table, rows, cols, onAdd, ...rowProps }: {
  title: string; table: Table; rows: Row[]; cols: [string, string, string?][]; onAdd: () => void; busy: boolean; act: Act;
  patchRow: (t: Table, r: Row, body: Record<string, unknown>, ok?: string) => Promise<void>;
}) {
  const { busy, patchRow } = rowProps;
  return (
    <div style={{ marginTop: 18 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>{title}</h3>
        <button className="small" disabled={busy} onClick={onAdd}><Icon name="plus" size={14} /> Row</button>
      </div>
      <div style={{ overflowX: "auto", marginTop: 8, border: "1px solid var(--line)", borderRadius: "var(--radius)" }}>
        <table className="data">
          <thead><tr>{cols.map(([k, l]) => <th key={k}>{l}</th>)}<th>Source ref</th><th>Status</th><th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                {cols.map(([k, , type]) => (
                  <td key={k}><Text value={r[k]} type={type} disabled={busy} width={type === "date" ? 140 : 110}
                    placeholder={k === "rate_pct" ? "fill in" : ""} onSave={(v) => patchRow(table, r, { [k]: v })} /></td>
                ))}
                <td><Text value={r.source_ref} disabled={busy} width={120} onSave={(v) => patchRow(table, r, { source_ref: v })} /></td>
                <RowStatus table={table} row={r} {...rowProps} />
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={cols.length + 3} className="muted">None yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Field({ label, value, onSave, type, wide }: { label: string; value: string; onSave: (v: string) => void; type?: string; wide?: boolean }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <label className="inline" style={wide ? { display: "grid", gridTemplateColumns: "130px 1fr", alignItems: "center" } : undefined}>
      {label}
      <input type={type ?? "text"} value={v} onChange={(e) => setV(e.target.value)}
        onBlur={() => v !== value && onSave(v)} style={wide ? { width: "100%" } : undefined} />
    </label>
  );
}

// Saves on blur (or Enter, for one line); an emptied field saves null.
function Text({ value, onSave, disabled, multiline, width, placeholder, type }: {
  value: unknown; onSave: (v: string | null) => void; disabled: boolean; multiline?: boolean; width?: number; placeholder?: string; type?: string;
}) {
  const shown = value === null || value === undefined ? "" : String(value);
  const [v, setV] = useState(shown);
  useEffect(() => setV(shown), [shown]);
  const commit = () => v.trim() !== shown && onSave(v.trim() || null);
  const style = { width: width ?? "100%", fontSize: 13 };
  if (multiline) {
    return <textarea rows={2} value={v} disabled={disabled} placeholder={placeholder} onChange={(e) => setV(e.target.value)} onBlur={commit} style={style} />;
  }
  return (
    <input type={type ?? "text"} value={v} disabled={disabled} placeholder={placeholder} onChange={(e) => setV(e.target.value)} onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} style={style} />
  );
}
