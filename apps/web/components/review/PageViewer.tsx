"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { OcrLine, PageOcr } from "@/lib/api";
import { get, pageImageUrl } from "@/lib/client";
import Icon from "../Icon";

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];

export type ViewerPage = { seq: number; document: string; page_no: number };

const norm = (s: string) => s.replace(/\s+/g, " ").trim();

// OCR lines that fall inside (or contain) a cited snippet — used when no anchor gives the lines.
function textHits(lines: OcrLine[], q: string | null | undefined): number[] {
  if (!q) return [];
  const nq = norm(q);
  return lines.filter((l) => {
    const nl = norm(l.text ?? "");
    return nl.length >= 4 && (nq.includes(nl) || nl.includes(nq) || (nq.length > 24 && nl.includes(nq.slice(4, 24))));
  }).map((l) => l.index);
}

const pageText = (o: PageOcr) => o.text_redacted ?? o.lines.map((l) => l.text ?? "").join("\n");

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

// Page viewer for text extraction: masked page image with OCR bounding boxes, or the redacted OCR text;
// zoom; page navigation within the selected document. Clicking a line copies its text. `highlight` (OCR line indices)
// or `q` (a cited snippet, matched line by line) mark the cited lines of `page`.
export default function PageViewer({ caseId, pages, docPages, page, setPage, canUnmask, notify, highlight, q }: {
  caseId: string;
  pages: ViewerPage[];
  docPages: number[];
  page: number;
  setPage: (seq: number) => void;
  canUnmask: boolean;
  notify: (kind: "error" | "success" | "notice", text: string) => void;
  highlight?: number[] | null;
  q?: string | null;
}) {
  const [mode, setMode] = useState<"image" | "text">("text");
  const [boxes, setBoxes] = useState(true);
  const [masked, setMasked] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [ocr, setOcr] = useState<Record<number, PageOcr>>({});
  const [imgError, setImgError] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  const loadOcr = useCallback(
    async (seq: number) => {
      if (ocr[seq]) return ocr[seq];
      const o = await get<PageOcr>(`/cases/${caseId}/pages/${seq}/ocr`);
      setOcr((m) => ({ ...m, [seq]: o }));
      return o;
    },
    [caseId, ocr],
  );

  useEffect(() => {
    setImgError(false);
    setCopied(null);
    loadOcr(page).catch(() => undefined);
    scroller.current?.scrollTo({ top: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, page]);

  const idx = docPages.indexOf(page);
  const prev = idx > 0 ? docPages[idx - 1] : null;
  const next = idx >= 0 && idx < docPages.length - 1 ? docPages[idx + 1] : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName)) return;
      if ((e.key === "ArrowRight" || e.key === "PageDown") && next !== null) setPage(next);
      if ((e.key === "ArrowLeft" || e.key === "PageUp") && prev !== null) setPage(prev);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prev, next, setPage]);

  const o = ocr[page];
  const meta = pages.find((p) => p.seq === page);
  const cited = useMemo(
    () => new Set(highlight?.length ? highlight : o ? textHits(o.lines, q) : []),
    [highlight, q, o],
  );
  const firstCited = useRef<HTMLDivElement>(null);
  useEffect(() => {
    firstCited.current?.scrollIntoView({ block: "center" });
  }, [cited, mode]);

  const copyLine = async (l: OcrLine) => {
    if (!l.text) {
      notify("notice", "This line's text is not available (masked or OCR not aligned).");
      return;
    }
    if (await copy(l.text)) {
      setCopied(l.index);
      notify("success", `Copied: “${l.text.slice(0, 60)}${l.text.length > 60 ? "…" : ""}”`);
    } else notify("error", "Could not copy to the clipboard.");
  };

  const copyText = async (text: string, what: string) => {
    if (await copy(text)) notify("success", `Copied ${what}.`);
    else notify("error", "Could not copy to the clipboard.");
  };

  const copyPage = () => o && copyText(pageText(o), `the text of page ${meta?.page_no ?? page}`);

  const copyDocument = async () => {
    try {
      const all = await Promise.all(docPages.map((s) => loadOcr(s)));
      const text = all.map((x, i) => `--- page ${pages.find((p) => p.seq === docPages[i])?.page_no ?? i + 1} ---\n${pageText(x)}`).join("\n\n");
      await copyText(text, `the text of all ${docPages.length} page(s)`);
    } catch (e) {
      notify("error", (e as Error).message);
    }
  };

  const cls = (l: OcrLine) => {
    const c = l.confidence ?? 1;
    const parts = ["line", c < 0.7 ? "low" : c < 0.9 ? "mid" : ""];
    if (copied === l.index || cited.has(l.index)) parts.push("cited");
    return parts.join(" ");
  };

  return (
    <>
      <div className="pane-head">
        <button className="icon small" disabled={prev === null} onClick={() => prev !== null && setPage(prev)} aria-label="Previous page"><Icon name="chevronLeft" size={14} /></button>
        <div className="page-chips" title="Pages of the selected document">
          {docPages.map((s) => (
            <button key={s} className={s === page ? "on" : ""} onClick={() => setPage(s)}>
              {pages.find((p) => p.seq === s)?.page_no ?? s}
            </button>
          ))}
        </div>
        <button className="icon small" disabled={next === null} onClick={() => next !== null && setPage(next)} aria-label="Next page"><Icon name="chevronRight" size={14} /></button>
        <span style={{ flex: 1 }} />
        <div className="seg" role="group" aria-label="View">
          <button className={mode === "text" ? "on" : ""} aria-pressed={mode === "text"} onClick={() => setMode("text")}><Icon name="text" size={13} /> Extracted text</button>
          <button className={mode === "image" ? "on" : ""} aria-pressed={mode === "image"} onClick={() => setMode("image")}><Icon name="image" size={13} /> Image</button>
        </div>
        {mode === "image" && (
          <>
            <span className="toolbar-sep" />
            <label className="inline"><input type="checkbox" checked={boxes} onChange={(e) => setBoxes(e.target.checked)} /> boxes</label>
            {canUnmask && (
              <label className="inline" title="Unmasked images show personal data; every view is audited">
                <input type="checkbox" checked={!masked} onChange={(e) => setMasked(!e.target.checked)} /> unmask PII
              </label>
            )}
            <div className="seg">
              <button onClick={() => setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(zoom) - 1)])} aria-label="Zoom out"><Icon name="zoomOut" size={13} /></button>
              <button onClick={() => setZoom(1)} title="Fit width" style={{ minWidth: 48, fontVariantNumeric: "tabular-nums" }}>{Math.round(zoom * 100)}%</button>
              <button onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + 1)])} aria-label="Zoom in"><Icon name="zoomIn" size={13} /></button>
            </div>
          </>
        )}
        <span className="toolbar-sep" />
        <button className="small" disabled={!o} onClick={copyPage}><Icon name="copy" size={13} /> Copy page</button>
        <button className="small" onClick={copyDocument}><Icon name="copy" size={13} /> Copy document</button>
      </div>
      <div className="viewer-meta">
        <Icon name="fileText" size={13} />
        <b style={{ color: "var(--text-2)", fontWeight: 600 }}>{meta?.document}</b>
        <span className="sep">·</span> page {meta?.page_no}
        <span className="sep">·</span>
        {o ? `${o.ocr_engine ?? "no OCR"}${o.ocr_confidence != null ? ` ${(o.ocr_confidence * 100).toFixed(0)}%` : ""} · ${o.lines.length} lines${o.aligned ? "" : " · OCR/redaction not aligned: all lines masked"}` : "loading OCR…"}
        <span style={{ marginLeft: "auto" }}>Click a line to copy it</span>
      </div>
      <div className="viewer-canvas" ref={scroller}>
        {mode === "image" ? (
          <div className="page-wrap" style={{ width: `${zoom * 100}%`, maxWidth: zoom <= 1 ? 1000 : undefined }}>
            {imgError ? (
              <div className="error" style={{ margin: 20 }}>Page image unavailable.</div>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={pageImageUrl(caseId, page, masked, zoom > 1.5 ? 2400 : 1600)} alt={`page ${page}`} onError={() => setImgError(true)} />
            )}
            {boxes && o && (
              <svg viewBox="0 0 1 1" preserveAspectRatio="none">
                {o.lines.filter((l) => l.bbox).map((l) => {
                  const [x0, y0, x1, y1] = l.bbox!;
                  return (
                    <rect key={l.index} className={cls(l)} x={x0} y={y0} width={x1 - x0} height={y1 - y0} onClick={() => copyLine(l)}>
                      <title>{l.text ?? "(text unavailable)"}{l.confidence != null ? ` · conf ${l.confidence.toFixed(2)}` : ""}</title>
                    </rect>
                  );
                })}
              </svg>
            )}
          </div>
        ) : (
          <div className="ocr-text">
            {!o && <span className="muted">Loading…</span>}
            {o && !o.aligned && <p className="notice">Line text could not be aligned with the redacted page; showing the redacted page text.</p>}
            {o && o.aligned && !o.lines.length && <span className="muted">No text was extracted from this page.</span>}
            {o && o.aligned && o.lines.map((l) => (
              <div key={l.index} className={`ln${copied === l.index || cited.has(l.index) ? " cited" : ""}`} onClick={() => copyLine(l)}
                ref={cited.size && l.index === Math.min(...cited) ? firstCited : undefined}
                title={l.confidence != null ? `OCR confidence ${l.confidence.toFixed(2)}` : undefined}>
                <span className="n">{l.index + 1}</span>
                <span>{l.text ?? <span className="muted">(masked)</span>}</span>
              </div>
            ))}
            {o && !o.aligned && <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit" }}>{o.text_redacted}</pre>}
          </div>
        )}
      </div>
    </>
  );
}
