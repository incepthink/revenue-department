"use client";
import { useState } from "react";
import { fmtValue, type Evidence, type FieldRow, type ReviewExhibit } from "@/lib/api";
import { confClass, range, useReview } from "./shared";

export default function FieldsTab({ exhibit }: { exhibit: ReviewExhibit }) {
  const { run, readOnly, busy } = useReview();
  const undecided = exhibit.fields.filter((f) => !f.decision).length;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <div>
          <b>{exhibit.seq}. {exhibit.type}</b> <span className="muted">{exhibit.type_name_mr} · p.{exhibit.start}–{exhibit.end}</span>
          <div className="muted" style={{ fontSize: 12 }}>{exhibit.fields.length - undecided}/{exhibit.fields.length} fields decided</div>
        </div>
        {!readOnly && undecided > 0 && (
          <div className="row" style={{ gap: 4 }}>
            <button className="small" disabled={busy}
              onClick={() => run([{ op: "field.accept_all", exhibit_id: exhibit.exhibit_id, min_confidence: 0.9, include_empty: false }])}>
              Accept ≥ 0.9
            </button>
            <button className="small" disabled={busy} title="Accept every remaining value; empty fields become 'not present'"
              onClick={() => run([{ op: "field.accept_all", exhibit_id: exhibit.exhibit_id }])}>
              Accept remaining
            </button>
          </div>
        )}
      </div>
      {!exhibit.fields.length && <p className="muted">No extraction fields for this exhibit type — review the boundaries, type and stage in the rail.</p>}
      {exhibit.fields.map((f) => <FieldCard key={`${exhibit.exhibit_id}:${f.name}`} exhibit={exhibit} f={f} />)}
    </>
  );
}

function asList(v: Evidence | Evidence[] | null): Evidence[] {
  return Array.isArray(v) ? v : v ? [v] : [];
}

function FieldCard({ exhibit, f }: { exhibit: ReviewExhibit; f: FieldRow }) {
  const { run, readOnly, busy, jump, page, cite } = useReview();
  const [editing, setEditing] = useState(false);
  const d = f.decision;
  const isList = f.kind === "list";
  const current = f.current;
  const cc = d?.action === "edit" ? "high" : confClass(f.confidence);
  const pages = range(exhibit.start, exhibit.end);

  const decide = async (action: string, extra: Record<string, unknown> = {}) => {
    const ok = await run([{ op: "field.decide", exhibit_id: exhibit.exhibit_id, field: f.name, action, ...extra }]);
    if (ok) setEditing(false);
  };

  const status = d ? { accept: "accepted", edit: "edited", not_present: "not present" }[d.action] : "undecided";
  const shown = asList(current);
  return (
    <div className={`field conf-${d?.action === "not_present" ? "none" : cc}${d ? " decided" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="fname" title={f.description}>{f.name} <span className="muted" style={{ fontWeight: 400 }}>· {f.kind}</span></span>
        <span className="row" style={{ gap: 4 }}>
          {f.confidence != null && <span className={`conf ${confClass(f.confidence)}`} title="draft confidence">{f.confidence.toFixed(2)}</span>}
          <span className="pill" style={{ color: d ? (d.action === "edit" ? "var(--accent)" : "var(--ok)") : "var(--warn)", fontSize: 10.5 }}>{status}</span>
        </span>
      </div>

      {isList ? (
        shown.length ? (
          <ol style={{ margin: "4px 0", paddingLeft: 20 }}>
            {shown.map((e, i) => (
              <li key={i} className="fval" style={{ fontSize: 13 }}>
                {fmtValue(e.value)} <a className="muted" style={{ fontSize: 11.5, cursor: "pointer" }} onClick={() => jump(e.page_seq, e.raw)}>p.{e.page_seq}</a>
              </li>
            ))}
          </ol>
        ) : <div className="fval absent">none</div>
      ) : shown.length ? (
        <div className={`fval${d?.action === "edit" ? " edited" : ""}`}>{fmtValue(shown[0].value)}</div>
      ) : (
        <div className="fval absent">{d?.action === "not_present" ? "not present" : "not found by the extractor"}</div>
      )}

      {!isList && shown[0] && (
        <div className="ev">
          <a onClick={() => jump(shown[0].page_seq, shown[0].raw)}>p.{shown[0].page_seq}</a> · {shown[0].method}
          {shown[0].located === false && " · raw text not found on page"} · “{shown[0].snippet}”
        </div>
      )}
      {d?.action === "edit" && f.draft && !isList && (
        <div className="ev">draft: {fmtValue((f.draft as Evidence).value)} ({(f.draft as Evidence).method})</div>
      )}
      {!isList && !Array.isArray(f.draft) && (f.draft?.alternatives?.length ?? 0) > 0 && (
        <div className="ev">alternatives: {f.draft!.alternatives!.map((a) => `${fmtValue(a.value)} (${a.method})`).join(", ")}</div>
      )}

      {!readOnly && !editing && (
        <div className="actions">
          <button className={`small${d?.action === "accept" ? " on" : ""}`} disabled={busy || (!shown.length && !f.draft)} onClick={() => decide("accept")}
            title={f.draft ? "The draft value is correct" : "Nothing to accept"}>✓ Accept</button>
          <button className={`small${d?.action === "edit" ? " on" : ""}`} disabled={busy} onClick={() => setEditing(true)}>✎ Edit</button>
          <button className={`small${d?.action === "not_present" ? " on" : ""}`} disabled={busy} onClick={() => decide("not_present")}>∅ Not present</button>
          {d && <button className="small" disabled={busy} onClick={() => decide("reset")} title="Back to undecided">↺</button>}
        </div>
      )}
      {editing && (isList
        ? <ListEditor f={f} pages={pages} initial={shown} onCancel={() => setEditing(false)} onSave={(items) => decide("edit", { items })} />
        : <ValueEditor f={f} pages={pages} defaultPage={cite && pages.includes(cite.page_seq) ? cite.page_seq : pages.includes(page) ? page : exhibit.start}
            initial={shown[0] ?? null} cite={cite} onCancel={() => setEditing(false)}
            onSave={(value, page_seq, raw) => decide("edit", { value, page_seq, raw })} />)}
    </div>
  );
}

function ValueEditor({ f, pages, defaultPage, initial, cite, onSave, onCancel }: {
  f: FieldRow; pages: number[]; defaultPage: number; initial: Evidence | null; cite: { page_seq: number; text: string } | null;
  onSave: (value: unknown, page: number, raw: string) => void; onCancel: () => void;
}) {
  const [value, setValue] = useState<string>(initial?.value == null ? "" : String(initial.value));
  const [pg, setPg] = useState<number>(defaultPage);
  const citeHere = cite && cite.page_seq === pg ? cite.text : null;
  const [raw, setRaw] = useState<string>(citeHere ?? initial?.raw ?? "");
  const input = (() => {
    if (f.kind === "enum") {
      return (
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">— choose —</option>
          {f.enum.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    }
    if (f.kind === "bool") {
      return (
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">— choose —</option><option value="true">yes</option><option value="false">no</option>
        </select>
      );
    }
    if (f.kind === "date") return <input type="date" value={value} onChange={(e) => setValue(e.target.value)} />;
    if (f.kind === "int") return <input type="number" min={0} step={1} value={value} onChange={(e) => setValue(e.target.value)} />;
    return <input value={value} inputMode={f.kind === "amount" || f.kind === "area" ? "decimal" : undefined}
      onChange={(e) => setValue(e.target.value)} style={{ width: "100%" }} />;
  })();
  return (
    <div className="editor">
      <div className="grid">
        <label>Value</label>{input}
        <label>Page *</label>
        <select value={pg} onChange={(e) => setPg(Number(e.target.value))} aria-label="Evidence page">
          {pages.map((p) => <option key={p} value={p}>p.{p}</option>)}
        </select>
        <label>As written</label>
        <div className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
          <input value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="text on the page (click a box in the viewer to cite)" style={{ flex: 1 }} />
          {citeHere && citeHere !== raw && <button type="button" className="small" onClick={() => setRaw(citeHere)} title={citeHere}>use cited</button>}
        </div>
      </div>
      <div className="muted" style={{ fontSize: 11.5 }}>
        {f.kind === "amount" || f.kind === "area" ? "Number only (e.g. 805.50). " : ""}A page reference is required for every edit.
      </div>
      <div className="row" style={{ gap: 4 }}>
        <button className="small primary" disabled={value === ""} onClick={() => onSave(value, pg, raw)}>Save</button>
        <button className="small" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function ListEditor({ f, pages, initial, onSave, onCancel }: {
  f: FieldRow; pages: number[]; initial: Evidence[]; onSave: (items: { raw: string; page_seq: number }[]) => void; onCancel: () => void;
}) {
  const { cite } = useReview();
  const [items, setItems] = useState(initial.map((e) => ({ raw: String(e.value ?? e.raw), page_seq: e.page_seq })));
  const set = (i: number, k: "raw" | "page_seq", v: string | number) => setItems(items.map((it, j) => (j === i ? { ...it, [k]: v } : it)));
  return (
    <div className="editor">
      <div className="muted" style={{ fontSize: 12 }}>{f.description}</div>
      {items.map((it, i) => (
        <div key={i} className="row" style={{ gap: 4, flexWrap: "nowrap", alignItems: "flex-start" }}>
          <textarea rows={2} value={it.raw} onChange={(e) => set(i, "raw", e.target.value)} style={{ flex: 1 }} />
          <select value={it.page_seq} onChange={(e) => set(i, "page_seq", Number(e.target.value))} aria-label="page">
            {pages.map((p) => <option key={p} value={p}>p.{p}</option>)}
          </select>
          <button className="small icon" onClick={() => setItems(items.filter((_, j) => j !== i))} aria-label="Remove item">✕</button>
        </div>
      ))}
      <div className="row" style={{ gap: 4 }}>
        <button className="small" onClick={() => setItems([...items, { raw: "", page_seq: pages[0] }])}>+ item</button>
        {cite && pages.includes(cite.page_seq) && (
          <button className="small" onClick={() => setItems([...items, { raw: cite.text, page_seq: cite.page_seq }])}>+ cited line</button>
        )}
      </div>
      <div className="row" style={{ gap: 4 }}>
        <button className="small primary" disabled={!items.length || items.some((it) => !it.raw.trim())} onClick={() => onSave(items)}>Save list</button>
        <button className="small" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
