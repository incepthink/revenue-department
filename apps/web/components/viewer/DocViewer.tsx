"use client";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type ReactNode } from "react";
import Icon from "../Icon";

// Generic document viewer: every page of every document in one continuous scroll, with marks drawn on the pages
// (translucent boxes + numbered pins, or a ribbon for page-level marks), thumbnails on the left and a sidebar on the
// right. It knows nothing about what the marks mean — callers build docs and marks from their own data.

export type Box = [number, number, number, number]; // 0..1 of the page (x0, y0, x1, y1)
export type ViewerPage = { key: string; docId: string; pageNo: number; width: number; height: number; src: (w: number) => string };
export type ViewerDoc = { id: string; name: string; pages: ViewerPage[]; banner?: ReactNode };
export type ViewerMark = {
  id: string; itemId: string; number: number | string; level: string; pageKey: string;
  kind: "highlight" | "page"; boxes: Box[]; label?: string;
};
export type DocViewerHandle = { scrollToMark(id: string): void; scrollToPage(key: string): void };

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];
const TIERS = [800, 1600, 2400];
const FIT_MAX = 960; // px: the "fit width" page width on wide screens
const FLASH_MS = 1400;

type Props = {
  docs: ViewerDoc[];
  marks: ViewerMark[];
  selectedItem?: string | null;
  onSelectMark: (m: ViewerMark) => void;
  sidebar: ReactNode;
  toolbar?: ReactNode;
};

const DocViewer = forwardRef<DocViewerHandle, Props>(function DocViewer({ docs, marks, selectedItem, onSelectMark, sidebar, toolbar }, ref) {
  const scroller = useRef<HTMLDivElement>(null);
  const thumbs = useRef<HTMLDivElement>(null);
  const pageEls = useRef(new Map<string, HTMLDivElement>());
  const [zoom, setZoom] = useState(1);
  const [fit, setFit] = useState(FIT_MAX);
  const [near, setNear] = useState<Set<string>>(new Set());
  const [current, setCurrent] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const pages = useMemo(() => docs.flatMap((d) => d.pages), [docs]);
  const byPage = useMemo(() => {
    const m = new Map<string, ViewerMark[]>();
    for (const mk of marks) m.set(mk.pageKey, [...(m.get(mk.pageKey) ?? []), mk]);
    return m;
  }, [marks]);

  // fit width follows the scroll area (minus padding), capped
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setFit(Math.max(240, Math.min(FIT_MAX, el.clientWidth - 48))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // render images only for pages near the viewport
  useEffect(() => {
    const root = scroller.current;
    if (!root) return;
    const io = new IntersectionObserver((entries) => {
      setNear((prev) => {
        const next = new Set(prev);
        for (const e of entries) {
          const k = (e.target as HTMLElement).dataset.key!;
          if (e.isIntersecting) next.add(k);
          else next.delete(k);
        }
        return next;
      });
    }, { root, rootMargin: "1200px 0px" });
    pageEls.current.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [pages]);

  // the current page: the last one whose top is above a third of the viewport
  const onScroll = useCallback(() => {
    const root = scroller.current;
    if (!root) return;
    const line = root.scrollTop + root.clientHeight / 3;
    let cur: string | null = pages[0]?.key ?? null;
    for (const p of pages) {
      const el = pageEls.current.get(p.key);
      if (el && el.offsetTop <= line) cur = p.key;
    }
    setCurrent(cur);
  }, [pages]);
  useEffect(onScroll, [onScroll, zoom, fit]);

  // keep the current page's thumbnail in view
  useEffect(() => {
    if (!current) return;
    thumbs.current?.querySelector<HTMLElement>(`[data-thumb="${CSS.escape(current)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const scrollToPage = useCallback((key: string, offset = 0) => {
    const root = scroller.current;
    const el = pageEls.current.get(key);
    if (!root || !el) return;
    root.scrollTo({ top: Math.max(0, el.offsetTop - 16 + offset), behavior: "smooth" });
  }, []);

  const scrollToMark = useCallback((id: string) => {
    const mk = marks.find((m) => m.id === id);
    const root = scroller.current;
    const el = mk && pageEls.current.get(mk.pageKey);
    if (!mk || !root || !el) return;
    if (mk.kind === "highlight" && mk.boxes.length) {
      const y = el.offsetTop + mk.boxes[0][1] * el.offsetHeight;
      root.scrollTo({ top: Math.max(0, y - root.clientHeight / 3), behavior: "smooth" });
    } else scrollToPage(mk.pageKey);
    setFlash(id);
    window.setTimeout(() => setFlash((f) => (f === id ? null : f)), FLASH_MS);
  }, [marks, scrollToPage]);

  useImperativeHandle(ref, () => ({ scrollToMark, scrollToPage: (k) => scrollToPage(k) }), [scrollToMark, scrollToPage]);

  const index = current ? pages.findIndex((p) => p.key === current) : -1;
  const step = useCallback((d: number) => {
    const p = pages[Math.max(0, Math.min(pages.length - 1, index + d))];
    if (p) scrollToPage(p.key);
  }, [pages, index, scrollToPage]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName)) return;
      if (e.key === "PageDown") { e.preventDefault(); step(1); }
      if (e.key === "PageUp") { e.preventDefault(); step(-1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  const width = Math.round(fit * zoom);
  const dpr = typeof window === "undefined" ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const tier = TIERS.find((t) => t >= width * dpr) ?? TIERS[TIERS.length - 1];
  const zi = ZOOMS.indexOf(zoom);

  return (
    <div className="dv">
      <div className="dv-thumbs" ref={thumbs} aria-label="Pages">
        {docs.map((d) => (
          <div key={d.id} className="dv-thumb-doc">
            <div className="dv-thumb-name" title={d.name}>{d.name}</div>
            {d.pages.map((p) => {
              const n = byPage.get(p.key)?.length ?? 0;
              return (
                <button key={p.key} data-thumb={p.key} className={`dv-thumb${p.key === current ? " on" : ""}`} onClick={() => scrollToPage(p.key)}
                  aria-label={`${d.name}, page ${p.pageNo}${n ? `, ${n} mark${n === 1 ? "" : "s"}` : ""}`}>
                  <span className="dv-thumb-img" style={{ aspectRatio: `${p.width} / ${p.height}` }}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={p.src(180)} alt="" loading="lazy" />
                    {n > 0 && <span className="dv-thumb-count">{n}</span>}
                  </span>
                  <span className="dv-thumb-no">p.{p.pageNo}</span>
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div className="dv-main">
        <div className="pane-head dv-toolbar">
          <button className="icon small" onClick={() => step(-1)} disabled={index <= 0} aria-label="Previous page"><Icon name="chevronLeft" size={14} /></button>
          <span className="dv-pageno">{index >= 0 ? index + 1 : "–"} / {pages.length}</span>
          <button className="icon small" onClick={() => step(1)} disabled={index >= pages.length - 1} aria-label="Next page"><Icon name="chevronRight" size={14} /></button>
          <span className="toolbar-sep" />
          <div className="seg">
            <button onClick={() => setZoom(ZOOMS[Math.max(0, zi - 1)])} disabled={zi <= 0} aria-label="Zoom out"><Icon name="zoomOut" size={13} /></button>
            <button onClick={() => setZoom(1)} title="Fit width" style={{ minWidth: 48, fontVariantNumeric: "tabular-nums" }}>{Math.round(zoom * 100)}%</button>
            <button onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, zi + 1)])} disabled={zi >= ZOOMS.length - 1} aria-label="Zoom in"><Icon name="zoomIn" size={13} /></button>
          </div>
          <span style={{ flex: 1 }} />
          {toolbar}
        </div>
        <div className="dv-scroll" ref={scroller} onScroll={onScroll}>
          {docs.map((d) => (
            <div key={d.id} className="dv-doc" style={{ width }}>
              <div className="dv-doc-name"><Icon name="fileText" size={13} /> {d.name}</div>
              {d.banner}
              {d.pages.map((p) => {
                const pm = byPage.get(p.key) ?? [];
                const ribbons = pm.filter((m) => m.kind === "page");
                const boxes = pm.filter((m) => m.kind === "highlight" && m.boxes.length);
                return (
                  <div key={p.key} data-key={p.key} className="dv-page" style={{ aspectRatio: `${p.width} / ${p.height}` }}
                    ref={(el) => { if (el) pageEls.current.set(p.key, el); else pageEls.current.delete(p.key); }}>
                    {near.has(p.key) && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={p.src(tier)} alt={`${d.name}, page ${p.pageNo}`} draggable={false} />
                    )}
                    <svg viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden>
                      {boxes.flatMap((m) => m.boxes.map((b, i) => (
                        <rect key={`${m.id}:${i}`} x={b[0]} y={b[1]} width={b[2] - b[0]} height={b[3] - b[1]}
                          className={`mk mk-${m.level}${m.itemId === selectedItem ? " sel" : ""}${flash === m.id ? " flash" : ""}`}
                          onClick={() => onSelectMark(m)} />
                      )))}
                    </svg>
                    {boxes.map((m) => (
                      <button key={m.id} className={`pin mk-${m.level}${m.itemId === selectedItem ? " sel" : ""}`}
                        style={{ left: `${m.boxes[0][0] * 100}%`, top: `${m.boxes[0][1] * 100}%` }}
                        onClick={() => onSelectMark(m)} title={m.label} aria-label={`Mark ${m.number}${m.label ? `: ${m.label}` : ""}`}>
                        {m.number}
                      </button>
                    ))}
                    {ribbons.length > 0 && (
                      <div className="ribbons">
                        {ribbons.map((m) => (
                          <button key={m.id} className={`ribbon mk-${m.level}${m.itemId === selectedItem ? " sel" : ""}${flash === m.id ? " flash" : ""}`}
                            onClick={() => onSelectMark(m)}>
                            <span className={`pin static mk-${m.level}`}>{m.number}</span> <span className="ribbon-text">{m.label ?? "On this page"}</span>
                          </button>
                        ))}
                      </div>
                    )}
                    <span className="dv-page-no">p.{p.pageNo}</span>
                  </div>
                );
              })}
            </div>
          ))}
          {!pages.length && <div className="viewer-empty"><span className="empty-icon"><Icon name="fileText" size={22} /></span><b>No pages to show</b></div>}
        </div>
      </div>

      <aside className="dv-side">{sidebar}</aside>
    </div>
  );
});

export default DocViewer;
