"use client";
import type { CaseDetail } from "@/lib/api";
import Icon from "../Icon";
import { confClass } from "./shared";

type Doc = CaseDetail["documents"][number];

function avgConfidence(d: Doc): number | null {
  const c = d.page_list.map((p) => p.ocr_confidence).filter((x): x is number => x != null);
  return c.length ? c.reduce((a, b) => a + b, 0) / c.length : null;
}

export default function DocumentRail({ documents, selected, onSelect }: {
  documents: Doc[]; selected: string | null; onSelect: (id: string) => void;
}) {
  const pages = documents.reduce((n, d) => n + d.page_list.length, 0);
  return (
    <>
      <div className="pane-head" style={{ justifyContent: "space-between" }}>
        <b>Documents</b>
        <span className="count-pill">{documents.length} · {pages} pages</span>
      </div>
      <div className="rail-list">
        {documents.map((d) => {
          const conf = avgConfidence(d);
          const ready = d.page_list.length > 0;
          const on = d.id === selected;
          return (
            <div key={d.id} className={`rail-item${on ? " active" : ""}${ready ? "" : " disabled"}`}
              role="button" tabIndex={ready ? 0 : -1} aria-pressed={on} aria-disabled={!ready}
              onClick={() => ready && onSelect(d.id)}
              onKeyDown={(e) => ready && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect(d.id))}>
              <span className="rail-icon"><Icon name="fileText" size={16} /></span>
              <div className="rail-body">
                <div className="t" style={{ wordBreak: "break-word" }}>{d.seq}. {d.filename}</div>
                <div className="meta">
                  <span>{d.page_list.length || d.pages || 0} page(s)</span>
                  {conf != null && <span className={`conf ${confClass(conf)}`} title="average OCR confidence">{conf.toFixed(2)}</span>}
                  {!ready && <span className="pill" style={{ fontSize: 11, padding: "0 8px" }}>{d.status}</span>}
                </div>
                {d.error && <div className="meta" style={{ color: "var(--bad)" }}>{d.error}</div>}
              </div>
            </div>
          );
        })}
      </div>
      {!documents.length && <p className="muted" style={{ padding: 14 }}>No documents uploaded.</p>}
    </>
  );
}
