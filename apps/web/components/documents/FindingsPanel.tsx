"use client";
import { useEffect, useRef } from "react";
import type { Anchor, IntakeFinding, IntakeLevel } from "@/lib/api";
import { LEVEL, LEVEL_ORDER } from "../intake/levels";
import Icon from "../Icon";

export type PanelItem = { id: string; number: number; f: IntakeFinding; anchors: Anchor[]; placed: boolean };
export type PanelFilters = { levels: IntakeLevel[]; doc: string };

// Findings of the document check beside the pages: numbered like the pins, grouped by level, filtered by level and
// document. A selected row expands to its detail and one location chip per anchor (the other side of a cross-document
// finding is one click away).
export default function FindingsPanel({ items, selected, onSelect, onOpenText, filters, setFilters, documents, docName }: {
  items: PanelItem[];
  selected: string | null;
  onSelect: (id: string, anchor?: number) => void;
  onOpenText: (a: Anchor) => void;
  filters: PanelFilters;
  setFilters: (f: PanelFilters) => void;
  documents: { id: string; name: string }[];
  docName: (id: string | null | undefined) => string;
}) {
  const rows = useRef(new Map<string, HTMLLIElement>());
  useEffect(() => {
    if (selected) rows.current.get(selected)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  const inDoc = (it: PanelItem) => !filters.doc || it.anchors.some((a) => a.document_id === filters.doc)
    || it.f.evidence.some((e) => e.document_id === filters.doc);
  const counts = Object.fromEntries(LEVEL_ORDER.map((l) => [l, items.filter((it) => it.f.level === l && inDoc(it)).length])) as Record<IntakeLevel, number>;
  const shown = items.filter((it) => filters.levels.includes(it.f.level) && inDoc(it));
  const unplaced = shown.filter((it) => !it.placed);
  const toggle = (l: IntakeLevel) => setFilters({
    ...filters, levels: filters.levels.includes(l) ? filters.levels.filter((x) => x !== l) : [...filters.levels, l],
  });

  const row = (it: PanelItem) => {
    const f = it.f;
    const on = it.id === selected;
    return (
      <li key={it.id} ref={(el) => { if (el) rows.current.set(it.id, el); else rows.current.delete(it.id); }}
        className={`fp-row${on ? " on" : ""}`}>
        <button className="fp-row-head" onClick={() => onSelect(it.id)} aria-expanded={on}>
          <span className={`pin static mk-${f.level}`}>{it.number}</span>
          <span className="fp-title">{f.title}</span>
          {!it.placed && <span className="fp-tag" title="Not tied to a page">no page</span>}
        </button>
        {on && (
          <div className="fp-body">
            {f.detail && <p>{f.detail}</p>}
            {f.action && <p className="fp-action"><Icon name="clipboard" size={13} /> {f.action}</p>}
            <div className="row" style={{ gap: 6 }}>
              {f.source === "ai" && <span className="badge" title="Observed by the AI reader — confirm on the cited page">AI observation</span>}
              {f.source === "rule" && <span className="badge" title="Judged by the AI reader against the rule — confirm on the cited page">AI rule check</span>}
              {f.rule && <span className="badge" title={`${f.rule.code} — ${f.rule.rule_set_name}`}>Rule {f.rule.rule_set_code}{f.rule.source_ref ? `, ${f.rule.source_ref}` : ""}</span>}
              {f.rule?.label && <span className="badge warn">{f.rule.label}</span>}
              {f.depends_on_unverified && <span className="badge warn">rule to be confirmed</span>}
            </div>
            {it.anchors.length > 0 && (
              <div className="fp-locs">
                {it.anchors.map((a, i) => {
                  const where = `${docName(a.document_id) || a.filename || "Document"}${a.page_no ? ` · p.${a.page_no}` : ""}`;
                  return (
                    <div key={i} className="fp-loc">
                      {a.kind === "missing"
                        ? <span className="ev-chip" title="Not on a page of this document">{where}</span>
                        : <button className="ev-chip" onClick={() => onSelect(it.id, i)} title={a.quote ?? undefined}>{where}</button>}
                      {a.quote && <q className="fp-quote">{a.quote}</q>}
                      {a.reason === "no_geometry" && (
                        <button className="small ghost" onClick={() => onOpenText(a)}>Couldn&apos;t place on the page · <Icon name="text" size={12} /> Show in text</button>
                      )}
                      {a.reason === "not_found" && <span className="muted fp-note">The quoted text was not found on the page.</span>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </li>
    );
  };

  return (
    <div className="fp">
      <div className="pane-head fp-filters">
        <div className="fp-chips" role="group" aria-label="Levels">
          {LEVEL_ORDER.map((l) => (
            <button key={l} className={`fp-chip lvl-${l}${filters.levels.includes(l) ? " on" : ""}`} aria-pressed={filters.levels.includes(l)}
              onClick={() => toggle(l)} title={LEVEL[l].label}>
              <span className={`lvl-icon lvl-${l}`}>{LEVEL[l].icon}</span> {counts[l]}
            </button>
          ))}
        </div>
        <select value={filters.doc} onChange={(e) => setFilters({ ...filters, doc: e.target.value })} aria-label="Document">
          <option value="">All documents</option>
          {documents.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </div>
      <div className="fp-list">
        {unplaced.length > 0 && (
          <section className="fp-group fp-unplaced">
            <div className="fp-group-head"><Icon name="inbox" size={14} /> Not on any page <span className="count-pill">{unplaced.length}</span></div>
            <ul>{unplaced.map(row)}</ul>
          </section>
        )}
        {LEVEL_ORDER.map((l) => {
          const group = shown.filter((it) => it.placed && it.f.level === l);
          if (!group.length) return null;
          return (
            <section key={l} className="fp-group">
              <div className="fp-group-head"><span className={`lvl-icon lvl-${l}`}>{LEVEL[l].icon}</span> {LEVEL[l].label} <span className="count-pill">{group.length}</span></div>
              <ul>{group.map(row)}</ul>
            </section>
          );
        })}
        {!shown.length && <p className="muted" style={{ padding: 14 }}>No findings match the filters.</p>}
      </div>
    </div>
  );
}
