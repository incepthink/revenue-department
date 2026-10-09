"use client";
import { useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import type { DocLayout, DocPara, DocRun } from "@/lib/api";
import { get } from "@/lib/client";

const TAB_PT = 36;  // Word's default tab stop (1.27 cm)
const LINE = 1.32;  // a "single" line of the Devanagari fonts, as a multiple of the font size
const TOKEN = /(\[[A-Z_]+_[0-9a-f]{8}\])/;

// A stored .docx render as a page: the layout the API reads from the file itself (alignment, indents, spacing, bold /
// underline), so what shows here is what Word prints. PII stays tokenised, as stored; the download fills it in.
export default function DocPreview({ draftId }: { draftId: string }) {
  const [doc, setDoc] = useState<DocLayout | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    setDoc(null);
    setErr(null);
    get<DocLayout>(`/drafts/${draftId}/layout`).then((d) => !stop && setDoc(d)).catch((e) => !stop && setErr((e as Error).message));
    return () => { stop = true; };
  }, [draftId]);
  if (err) return <div className="error" role="alert">{err}</div>;
  if (!doc) return <p className="muted"><span className="spinner" /> Loading the preview…</p>;
  const m = doc.page.margin;
  const text = doc.page.width - m.left - m.right;  // indents scale with the sheet: as in Word on A4, narrower on a phone
  const sheet: CSSProperties = {
    maxWidth: `${doc.page.width}pt`, minHeight: `min(${doc.page.height}pt, 70vh)`,
    padding: `min(${m.top}pt, 7vw) min(${m.right}pt, 6vw) min(${m.bottom}pt, 7vw) min(${m.left}pt, 6vw)`,
  };
  return (
    <div className="doc-desk">
      <article className="doc-sheet" lang="mr" style={sheet} aria-label="Preview of the Noting">
        {doc.watermark && (
          <div className="doc-watermark" aria-hidden="true">{[0, 1, 2].map((i) => <span key={i}>{doc.watermark}</span>)}</div>
        )}
        {doc.header.map((h, i) => <div key={i} className="doc-header">{h}</div>)}
        {doc.blocks.map((b, i) => b.kind === "p" ? <Para key={i} p={b} text={text} /> : (
          <table key={i} className="doc-table">
            <tbody>
              {b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}>{c.map((p, n) => <Para key={n} p={p} text={text} />)}</td>)}</tr>)}
            </tbody>
          </table>
        ))}
      </article>
    </div>
  );
}

function Para({ p, text }: { p: DocPara; text: number }) {
  const size = p.runs[0]?.size ?? 12;
  const pct = (pt: number, of = text) => `${((100 * pt) / of).toFixed(3)}%`;
  const style: CSSProperties = {
    textAlign: p.align, marginLeft: pct(p.left), marginRight: pct(p.right), textIndent: pct(p.first),
    marginTop: `${p.before}pt`, marginBottom: `${p.after}pt`, lineHeight: p.line * LINE, fontSize: `${size}pt`,
  };
  if (!p.runs.length) return <p className="doc-p" style={style}>{" "}</p>;
  // "3.1<tab>text", "1.<tab>text": the label sits in a box as wide as the tab it is followed by — the hanging indent
  // for a condition, the next default tab stop otherwise — so the text lines up as it does in Word
  const at = p.runs.findIndex((r) => r.text.includes("\t"));
  if (at < 0) return <p className="doc-p" style={style}>{p.runs.map((r, i) => <Run key={i} r={r} />)}</p>;
  const cut = p.runs[at].text.indexOf("\t");
  const label = [...p.runs.slice(0, at), { ...p.runs[at], text: p.runs[at].text.slice(0, cut) }].filter((r) => r.text);
  const rest = [{ ...p.runs[at], text: p.runs[at].text.slice(cut + 1) }, ...p.runs.slice(at + 1)].filter((r) => r.text);
  // a hanging label is as wide as the hang (a share of the paragraph's own width); otherwise up to the next tab stop
  const width = p.first < 0 ? pct(-p.first, text - p.left - p.right) : `${TAB_PT - (p.first % TAB_PT)}pt`;
  return (
    <p className="doc-p" style={style}>
      <span className="doc-label" style={{ minWidth: width }}>{label.map((r, i) => <Run key={i} r={r} />)}</span>
      {rest.map((r, i) => <Run key={i} r={r} />)}
    </p>
  );
}

function Run({ r }: { r: DocRun }) {
  const style: CSSProperties = {
    fontSize: `${r.size}pt`, fontWeight: r.bold ? 700 : undefined, fontStyle: r.italic ? "italic" : undefined,
    textDecoration: r.underline ? "underline" : undefined, color: r.color ?? undefined,
  };
  const parts: ReactNode[] = r.text.split(TOKEN).map((t, i) => (i % 2 ? <mark key={i} className="doc-token" title="Filled in on download">{t}</mark> : t));
  return <span style={style}>{parts}</span>;
}
