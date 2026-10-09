"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { LibraryDocDetail, LibraryLine, LibraryPageLines } from "@/lib/api";
import { get, send } from "@/lib/client";
import Icon from "../Icon";
import type { LineMark, Notify } from "./LibraryDocApp";

const STATUS: Record<LibraryLine["status"], { text: string; cls: string; title: string }> = {
  agreed: { text: "Agreed", cls: "lvl-ok", title: "Both readers gave the same text" },
  adjudicated: { text: "Settled", cls: "lvl-info", title: "The readers differed; settled by comparing both with a close-up of the line" },
  disputed: { text: "Disputed", cls: "lvl-warning", title: "The readers differed and it could not be settled" },
  single: { text: "One reader", cls: "lvl-unchecked", title: "Only one reader produced text" },
  unboxed: { text: "No box", cls: "lvl-unchecked", title: "Text found outside every line box" },
  added: { text: "Added", cls: "lvl-info", title: "Added by hand" },
};

// The editable OCR of one page: every line with how it was settled, both readers' texts where they differed, and
// the review state. Correct a line, accept a reader's text or a digit suggestion, mark it reviewed, add a line the
// reader missed or delete a stray one. The lines waiting for review are boxed on the page.
export default function LinesPanel({ doc, page, setPage, selected, onSelect, setMarks, onChanged, notify }: {
  doc: LibraryDocDetail; page: number; setPage: (n: number) => void; selected: string | null;
  onSelect: (id: string, page: number) => void; setMarks: (m: LineMark[]) => void; onChanged: () => void; notify: Notify;
}) {
  const [data, setData] = useState<LibraryPageLines | null>(null);
  const [onlyReview, setOnlyReview] = useState(doc.lines_to_review > 0);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const rows = useRef(new Map<string, HTMLLIElement>());

  const load = useCallback(() => get<LibraryPageLines>(`/library/documents/${doc.id}/pages/${page}/lines`).then(setData)
    .catch((e) => notify("error", (e as Error).message)), [doc.id, page, notify]);
  useEffect(() => { setData(null); load(); }, [load]);

  useEffect(() => {
    if (!data) return;
    setMarks(data.lines.filter((l) => l.bbox && (l.needs_review || l.id === selected)).map((l) => ({
      id: l.id, bbox: l.bbox!, level: l.id === selected ? "line" : "review", label: String(l.line_no), page,
    })));
  }, [data, selected, page, setMarks]);
  useEffect(() => () => setMarks([]), [setMarks]);
  useEffect(() => { if (selected) rows.current.get(selected)?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [selected]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); await load(); onChanged(); notify("success", ok); }
    catch (e) { notify("error", (e as Error).message); }
    finally { setBusy(false); }
  };
  const patch = (l: LibraryLine, body: Record<string, unknown>, ok: string) => act(() => send("PATCH", `/library/lines/${l.id}`, body), ok);
  const useDigit = (l: LibraryLine, num: string, alt: string) => {
    const re = new RegExp(`(?<![०-९])${num}(?![०-९])`);
    if (!re.test(l.text)) return notify("error", `“${num}” is no longer in the line.`);
    patch(l, { text: l.text.replace(re, alt) }, `Changed ${num} to ${alt}.`);
  };

  const pageInfo = doc.pages.find((p) => p.page_no === page);
  const nextReview = doc.pages.find((p) => p.page_no > page && p.lines_to_review) ?? doc.pages.find((p) => p.lines_to_review && p.page_no !== page);
  const lines = data ? data.lines.filter((l) => !onlyReview || l.needs_review || l.id === selected || l.id === editing) : [];

  return (
    <div className="lib-lines">
      <div className="pane-head fp-filters">
        <select value={page} onChange={(e) => setPage(Number(e.target.value))} aria-label="Page">
          {doc.pages.map((p) => (
            <option key={p.page_no} value={p.page_no}>
              Page {p.page_no}{p.section_page ? ` (§${doc.section} p.${p.section_page})` : ""}{p.lines_to_review ? ` — ${p.lines_to_review} to review` : ""}
            </option>
          ))}
        </select>
        {nextReview && <button className="small" onClick={() => setPage(nextReview.page_no)}>Next to review <Icon name="arrowRight" size={12} /></button>}
      </div>
      <div className="lib-lines-bar">
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={onlyReview} onChange={(e) => setOnlyReview(e.target.checked)} /> Only lines to review
        </label>
        <span className="spacer" />
        {data && (
          <button className="ghost small" title="Copy the page text" onClick={() => {
            navigator.clipboard.writeText(data.lines.map((l) => l.text).join("\n")).then(() => notify("success", "Page text copied."));
          }}><Icon name="copy" size={13} /> Copy page</button>
        )}
      </div>
      {pageInfo?.stats && (
        <div className="muted lib-stats">
          {pageInfo.stats.lines} lines · {pageInfo.stats.agreed} agreed · {pageInfo.stats.adjudicated} settled · {pageInfo.stats.uncertain} uncertain
        </div>
      )}
      {!data ? <div className="muted" style={{ padding: 14 }}><span className="spinner" /> Loading lines…</div> : (
        <ol className="lib-line-list">
          {lines.map((l) => {
            const st = STATUS[l.status];
            // the readers' texts only where they mattered (a settled or open dispute), and only when they differ beyond
            // the normalisation the stored text went through (dashes, spaces)
            const norm = (t: string) => t.replace(/[‐-―−]/g, "-").replace(/\s+/g, " ").trim();
            const readers = ["adjudicated", "disputed", "single"].includes(l.status)
              ? Object.entries(l.reads).filter(([, t]) => t && norm(t) !== norm(l.text)) : [];
            return (
              <li key={l.id} ref={(el) => { if (el) rows.current.set(l.id, el); else rows.current.delete(l.id); }}
                className={`lib-line${l.id === selected ? " on" : ""}${l.needs_review ? " needs-review" : ""}`}>
                <button className="lib-line-head ghost" onClick={() => onSelect(l.id, page)} disabled={!l.bbox} title={l.bbox ? "Show on the page" : "No box on the page"}>
                  <span className="lib-line-no">{l.line_no}</span>
                  <span className={`pill status ${st.cls}`} title={st.title}>{st.text}</span>
                  {l.uncertain && !l.reviewed && <span className="pill status lvl-warning">Check</span>}
                  {l.reviewed && <span className="pill status lvl-ok">Reviewed</span>}
                  {l.edited && <span className="pill">Edited</span>}
                </button>
                {editing === l.id ? (
                  <div className="lib-edit">
                    <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} lang="mr" autoFocus />
                    <div className="row" style={{ gap: 6 }}>
                      <button className="primary small" disabled={busy || !draft.trim()} onClick={() => { setEditing(null); patch(l, { text: draft }, "Line corrected."); }}>Save</button>
                      <button className="small" onClick={() => setEditing(null)}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  <div className="lib-line-text" lang="mr">{l.text}</div>
                )}
                {l.edited && l.machine_text && l.machine_text !== l.text && <div className="muted lib-read">OCR read: {l.machine_text}</div>}
                {readers.map(([m, t]) => (
                  <div key={m} className="lib-read">
                    <span className="muted">{m}:</span> <span lang="mr">{t}</span>
                    <button className="ghost small" disabled={busy} onClick={() => patch(l, { text: t }, `Used ${m}'s reading.`)}>Use</button>
                  </div>
                ))}
                {l.adjudication?.note && <div className="muted lib-read">Note: {l.adjudication.note}</div>}
                {(l.hints.digit_suspects ?? []).map((s) => (
                  <div key={s.num + s.alt} className="lib-hint">
                    <Icon name="alert" size={12} /> <span>The digit check reads <b>{s.alt}</b> where the line has <b>{s.num}</b> (१ and ९ look alike in this print).</span>
                    <button className="small" disabled={busy} onClick={() => useDigit(l, s.num, s.alt)}>Use {s.alt}</button>
                  </div>
                ))}
                <div className="lib-line-actions">
                  {editing !== l.id && <button className="ghost small" onClick={() => { setEditing(l.id); setDraft(l.text); }}><Icon name="edit" size={12} /> Correct</button>}
                  {l.reviewed
                    ? <button className="ghost small" disabled={busy} onClick={() => patch(l, { reviewed: false }, "Marked for review again.")}>Unmark</button>
                    : <button className="ghost small" disabled={busy} onClick={() => patch(l, { reviewed: true }, "Marked reviewed.")}><Icon name="check" size={12} /> Text is right</button>}
                  <button className="ghost small" onClick={() => { setAdding(l.line_no); setDraft(""); }}><Icon name="plus" size={12} /> Line after</button>
                  <button className="ghost small" disabled={busy} title="Delete a stray line (a stamp, a duplicate)" onClick={() => {
                    if (window.confirm(`Delete line ${l.line_no}?\n\n${l.text}`)) act(() => send("DELETE", `/library/lines/${l.id}`), "Line deleted.");
                  }}><Icon name="trash" size={12} /></button>
                </div>
                {adding === l.line_no && (
                  <div className="lib-edit">
                    <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={2} lang="mr" placeholder="Text of the missing line" autoFocus />
                    <div className="row" style={{ gap: 6 }}>
                      <button className="primary small" disabled={busy || !draft.trim()} onClick={() => {
                        setAdding(null);
                        act(() => send("POST", `/library/pages/${data.page_id}/lines`, { after_line_no: l.line_no, text: draft }), "Line added.");
                      }}>Add</button>
                      <button className="small" onClick={() => setAdding(null)}>Cancel</button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
          {!lines.length && (
            <li className="empty-state">
              <Icon name="checkCircle" size={20} />
              <b>{onlyReview ? "Nothing to review on this page" : "No lines"}</b>
              {onlyReview && <button className="small" onClick={() => setOnlyReview(false)}>Show all lines</button>}
            </li>
          )}
        </ol>
      )}
    </div>
  );
}
