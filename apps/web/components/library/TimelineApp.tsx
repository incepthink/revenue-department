"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fmtDate, type LibraryTaxonomy, type TimelineView } from "@/lib/api";
import { get } from "@/lib/client";
import Icon from "../Icon";
import LinkRow from "./LinkRow";

type Toast = { kind: "error" | "success"; text: string } | null;

// Rules in time, per section of the compendium: its GRs by date, grouped into chains (documents that amend, supersede,
// correct or clarify one another), each with the references the AI read in it. Confirming a link is what makes search
// and case analysis treat the older provision as amended or no longer in force.
export default function TimelineApp({ initialSection }: { initialSection: string }) {
  const [section, setSection] = useState(initialSection);
  const [tx, setTx] = useState<LibraryTaxonomy | null>(null);
  const [view, setView] = useState<TimelineView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [toast, setToast] = useState<Toast>(null);

  const notify = useCallback((kind: "error" | "success", text: string) => {
    setToast({ kind, text });
    window.setTimeout(() => setToast((t) => (t?.text === text ? null : t)), kind === "error" ? 8000 : 3000);
  }, []);
  const load = useCallback(() => {
    get<TimelineView>(`/library/timeline?section=${encodeURIComponent(section)}`).then(setView).catch((e) => setError((e as Error).message));
  }, [section]);
  useEffect(() => { get<LibraryTaxonomy>("/library/taxonomy").then(setTx).catch(() => setTx(null)); }, []);
  useEffect(() => {
    load();
    window.history.replaceState(null, "", `/library/timeline?section=${section}`);
  }, [load, section]);

  const byId = useMemo(() => new Map((view?.documents ?? []).map((d) => [d.id, d])), [view]);
  const linksFrom = useMemo(() => {
    const m = new Map<string, TimelineView["links"]>();
    for (const l of view?.links ?? []) {
      if (!l.from) continue;
      if (onlyOpen && l.status !== "proposed") continue;
      m.set(l.from.document_id, [...(m.get(l.from.document_id) ?? []), l]);
    }
    return m;
  }, [view, onlyOpen]);

  return (
    <main className="page lib-timeline">
      <div className="page-head">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb"><a href="/library/search">GR search</a><span className="sep">/</span><a href="/library">Library</a><span className="sep">/</span>Timeline</nav>
          <h1>Rules in time</h1>
          <div className="sub">
            The GRs of a section by date. Under each, the references the AI read in it: what it amends, supersedes, corrects,
            clarifies or cites, with the words that say so. Confirm the ones that are right: only confirmed links change which
            provisions search and case analysis treat as in force.
          </div>
        </div>
        <div className="actions-bar">
          {view && <span className="pill status lvl-warning">{view.counts.proposed} to confirm</span>}
          <a className="btn" href="/library/missing"><Icon name="alert" /> Missing GRs</a>
        </div>
      </div>
      {tx && (
        <div className="lib-filters">
          {Object.entries(tx.sections).map(([letter, s]) => (
            <button key={letter} className={`btn small${section === letter ? " on" : ""}`} onClick={() => setSection(letter)} title={s.mr}>
              {letter} · {s.en}
            </button>
          ))}
          <span className="lib-break" />
          <label className="muted"><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> Only links to confirm</label>
        </div>
      )}
      {error && <div className="error"><Icon name="alert" /> <span>{error}</span></div>}
      {view && !view.documents.length && <div className="card"><div className="empty-state"><b>No GRs of this section are loaded</b></div></div>}
      {view?.chains.map((chain, ci) => (
        <section key={ci} className={`card lib-chain${chain.length > 1 ? " linked" : ""}`}>
          {chain.length > 1 && <div className="card-title"><Icon name="layers" /> Chain of {chain.length} documents</div>}
          <ol className="lib-chain-list">
            {chain.map((id) => {
              const d = byId.get(id);
              if (!d) return null;
              const out = linksFrom.get(id) ?? [];
              return (
                <li key={id}>
                  <div className="lib-chain-doc">
                    <span className="lib-chain-date">{fmtDate(d.doc_date) || "undated"}</span>
                    <a href={`/library/${d.id}?tab=details`}><b>{d.key}</b> {d.type_label}</a>
                    <span className="lib-chain-subject" lang="mr">{d.subject_mr}</span>
                  </div>
                  {out.length > 0 && <div className="lib-chain-links">{out.map((l) => <LinkRow key={l.id} link={l} onChanged={load} notify={notify} />)}</div>}
                </li>
              );
            })}
          </ol>
        </section>
      ))}
      {toast && <div className={`toast ${toast.kind}`} role="status"><Icon name={toast.kind === "success" ? "check" : "alert"} /> <span>{toast.text}</span></div>}
    </main>
  );
}
