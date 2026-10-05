"use client";
import { useState } from "react";
import { confClass, range, useReview } from "./shared";

const STAGE_COLOUR: Record<string, string> = {
  filing: "var(--accent)", query_response: "#7a3fb0", internal: "var(--muted)", final: "var(--ok)",
};

export default function ExhibitRail({ selected, onSelect }: { selected: string; onSelect: (id: string) => void }) {
  const { view, run, readOnly, busy } = useReview();
  const exhibits = view.exhibits!;
  const cat = view.catalogs!;
  const [splitAt, setSplitAt] = useState<Record<string, number>>({});

  return (
    <>
      <div className="pane-head">
        <b>Exhibits</b>
        <span className="muted" style={{ fontSize: 12 }}>{exhibits.length} · {view.pages?.length} pages</span>
      </div>
      {exhibits.map((e, i) => {
        const on = e.exhibit_id === selected;
        const cc = confClass(e.confidence);
        const splitOptions = range(e.start + 1, e.end);
        const at = splitAt[e.exhibit_id] ?? splitOptions[0];
        return (
          <div key={e.exhibit_id} className={`rail-item${on ? " active" : ""}`} onClick={() => onSelect(e.exhibit_id)}>
            <div className="t">
              {e.seq}. {e.type}
              {e.undecided > 0 && <span className="count" style={{ marginLeft: 6, fontSize: 10.5, padding: "0 5px", borderRadius: 8, background: "var(--warn-soft)", color: "var(--warn)" }}>{e.undecided}</span>}
            </div>
            <div className="meta">
              <span>{e.type_name_mr}</span>
              <span>p.{e.start === e.end ? e.start : `${e.start}–${e.end}`}</span>
              <span className="pill" style={{ color: STAGE_COLOUR[e.stage] ?? "var(--muted)", fontSize: 10.5 }}>{e.stage}</span>
              <span className={`conf ${cc}`} title={`segment confidence (${e.method})`}>{e.confidence.toFixed(2)}</span>
              {e.origin === "manual" && <span className="badge">edited</span>}
            </div>
            {on && !readOnly && (
              <div className="rail-tools" onClick={(ev) => ev.stopPropagation()}>
                <span>Type</span>
                <select value={e.type} disabled={busy} aria-label="Exhibit type"
                  onChange={(ev) => run([{ op: "exhibit.retype", exhibit_id: e.exhibit_id, type: ev.target.value }])}>
                  {cat.exhibit_types.map((t) => <option key={t.code} value={t.code}>{t.code} — {t.name_mr}</option>)}
                </select>
                <span>Stage</span>
                <select value={e.stage} disabled={busy} aria-label="Stage"
                  onChange={(ev) => run([{ op: "exhibit.stage", exhibit_id: e.exhibit_id, stage: ev.target.value }])}>
                  {cat.stages.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
                <span>Split</span>
                {splitOptions.length ? (
                  <div className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
                    <select value={at} aria-label="Split before page"
                      onChange={(ev) => setSplitAt({ ...splitAt, [e.exhibit_id]: Number(ev.target.value) })}>
                      {splitOptions.map((p) => <option key={p} value={p}>before p.{p}</option>)}
                    </select>
                    <button className="small" disabled={busy}
                      onClick={() => run([{ op: "exhibit.split", exhibit_id: e.exhibit_id, at_page_seq: at }])}>Split</button>
                  </div>
                ) : <span className="muted">single page</span>}
                <span>Merge</span>
                <button className="small" disabled={busy || i === exhibits.length - 1}
                  title={i < exhibits.length - 1 ? `Merge with ${exhibits[i + 1].seq}. ${exhibits[i + 1].type}` : "last exhibit"}
                  onClick={() => run([{ op: "exhibit.merge", exhibit_id: e.exhibit_id }])}>
                  with next exhibit
                </button>
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
