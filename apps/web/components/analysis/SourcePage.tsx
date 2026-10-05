"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { pageImageUrl } from "@/lib/client";
import Icon from "../Icon";

export type PageInfo = {
  seq: number; page_no: number; predicted_type: string | null; text_redacted: string | null; ocr_engine: string | null; has_image: boolean;
};

const norm = (s: string) => s.replace(/\s+/g, " ").trim();

// Lines of the redacted page text that fall inside (or contain) the cited snippet.
function hits(lines: string[], q: string | null): Set<number> {
  const out = new Set<number>();
  if (!q) return out;
  const nq = norm(q);
  lines.forEach((l, i) => {
    const nl = norm(l);
    if (nl.length >= 4 && (nq.includes(nl) || nl.includes(nq) || (nq.length > 24 && nl.includes(nq.slice(4, 24))))) out.add(i);
  });
  return out;
}

export default function SourcePage({ caseId, caseRef, society, page, q }: {
  caseId: string; caseRef: string; society: string | null; page: PageInfo; q: string | null;
}) {
  const [img, setImg] = useState(page.has_image);
  const lines = useMemo(() => (page.text_redacted ?? "").split("\n"), [page.text_redacted]);
  const hit = useMemo(() => hits(lines, q), [lines, q]);
  const firstHit = hit.size ? Math.min(...Array.from(hit)) : -1;
  const first = useRef<HTMLDivElement>(null);
  useEffect(() => first.current?.scrollIntoView({ block: "center" }), []);
  return (
    <main className="page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <a href="/">Cases</a><span className="sep">/</span><a href={`/cases/${caseId}/intake`}>{caseRef}</a><span className="sep">/</span><span>Page {page.seq}</span>
      </nav>
      <div className="page-head">
        <div>
          <h1>Page {page.seq} {society && <small className="muted" style={{ fontSize: 15, fontWeight: 500 }}>{society}</small>}</h1>
          <div className="sub row" style={{ gap: 8 }}>
            <span className="badge">{page.predicted_type ?? "page"}</span>
            <span className="badge"><Icon name="text" size={12} /> {page.ocr_engine ?? "no text"}</span>
            <span className="badge"><Icon name="shield" size={12} /> PII redacted / masked</span>
          </div>
        </div>
        <div className="actions-bar">
          <div className="seg">
            {page.seq > 1 && <a className="btn ghost small" href={`/cases/${caseId}/source/${page.seq - 1}`}><Icon name="chevronLeft" size={14} /> p.{page.seq - 1}</a>}
            <a className="btn ghost small" href={`/cases/${caseId}/source/${page.seq + 1}`}>p.{page.seq + 1} <Icon name="chevronRight" size={14} /></a>
          </div>
          <a className="btn" href={`/cases/${caseId}/documents`}><Icon name="eye" /> View documents</a>
        </div>
      </div>
      {q && (
        <div className="notice">
          <Icon name="info" />
          <span>Cited: “{q}”{hit.size ? "" : " — not matched line by line on this page (the snippet may span lines)."}</span>
        </div>
      )}
      <div className="source-grid" style={{ marginTop: 16 }}>
        {img && (
          <div className="card page-image">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={pageImageUrl(caseId, page.seq, true, 1200)} alt={`page ${page.seq} (PII masked)`} onError={() => setImg(false)} />
          </div>
        )}
        <div className="card ocr-text" style={{ maxWidth: "none", boxShadow: "var(--shadow)" }}>
          {!img && <p className="muted" style={{ marginTop: 0 }}>No page image (text-only page).</p>}
          {lines.map((l, i) => (
            <div key={i} ref={i === firstHit ? first : undefined} className={`ln${hit.has(i) ? " cited" : ""}`}>
              <span className="n">{i + 1}</span><span>{l || " "}</span>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
