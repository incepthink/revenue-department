"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { viewerHref, type Anchor, type CaseDetail, type IntakeFinding, type IntakeLevel, type IntakeReport, type IntakeView } from "@/lib/api";
import { get, pageImageUrl } from "@/lib/client";
import Icon from "../Icon";
import { LEVEL_ORDER, VERDICT } from "../intake/levels";
import DocViewer, { type DocViewerHandle, type ViewerDoc, type ViewerMark } from "../viewer/DocViewer";
import FindingsPanel, { type PanelFilters, type PanelItem } from "./FindingsPanel";
import TextView from "./TextView";

export type DocumentsParams = { f?: string; a?: string; fact?: string; seq?: string; tab?: string };
type Toast = { kind: "error" | "success" | "notice"; text: string } | null;
type TextAt = { seq: number; lines?: number[] | null; q?: string | null } | null;

const DEFAULT_LEVELS: IntakeLevel[] = ["problem", "warning", "info", "unchecked"]; // "Passed" is opt-in
const A4 = { w: 1240, h: 1754 }; // layout fallback for pages stored without their size

// Anchors as stored, or — a report that could not be anchored — the evidence's pages without boxes.
function anchorsOf(f: IntakeFinding): Anchor[] {
  if (f.anchors) return f.anchors;
  return f.evidence.map((e) => ({
    document_id: e.document_id, filename: e.filename, page_seq: e.page_seq, page_id: null, page_no: null,
    kind: e.page_seq ? "page" : "missing", boxes: [], lines: [], precision: null, quote: e.raw ?? null,
    reason: e.page_seq ? (e.raw ? "not_found" : "no_quote") : null,
  }));
}

// The check's verdict and what the applicant has to do, above the findings.
function Summary({ report }: { report: IntakeReport }) {
  const v = VERDICT[report.verdict];
  return (
    <div className={`fp-summary ${report.verdict}`}>
      <div className="fp-verdict"><span className="fp-verdict-icon" aria-hidden>{v.icon}</span> <b>{v.label}</b></div>
      <div className="fp-headline">{report.headline}</div>
      {report.limited && <div className="fp-headline muted">Read with pattern rules only — treat this result as partial.</div>}
      {report.actions.length > 0 ? (
        <details open className="fp-todo">
          <summary><Icon name="clipboard" size={13} /> What the applicant needs to do <span className="count-pill">{report.actions.length}</span></summary>
          <ol>{report.actions.map((a) => <li key={a}>{a}</li>)}</ol>
        </details>
      ) : <div className="fp-headline"><Icon name="checkCircle" size={13} /> Nothing to replace or add.</div>}
    </div>
  );
}

// The documents screen: the pages with the document check's findings drawn on them (Pages tab), or the extracted
// text (Text tab). Deep links: ?f=<finding>&a=<anchor>, ?fact=<i>, ?seq=<page>, ?tab=text.
export default function DocumentsApp({ caseId, params }: { caseId: string; params: DocumentsParams }) {
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [intake, setIntake] = useState<IntakeView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast>(null);
  const [tab, setTab] = useState<"pages" | "text">(params.tab === "text" ? "text" : "pages");
  const [selected, setSelected] = useState<string | null>(params.f ?? null);
  const [fact, setFact] = useState<number | null>(params.fact !== undefined ? Number(params.fact) : null);
  const [filters, setFilters] = useState<PanelFilters>({ levels: DEFAULT_LEVELS, doc: "" });
  const [textAt, setTextAt] = useState<TextAt>(params.seq ? { seq: Number(params.seq) } : null);
  const viewer = useRef<DocViewerHandle>(null);
  const initial = useRef(params);

  const notify = useCallback((kind: "error" | "success" | "notice", text: string) => {
    setToast({ kind, text });
    window.setTimeout(() => setToast((t) => (t?.text === text ? null : t)), kind === "error" ? 8000 : 3500);
  }, []);

  useEffect(() => {
    Promise.all([get<CaseDetail>(`/cases/${caseId}`), get<IntakeView>(`/cases/${caseId}/intake`)])
      .then(([d, i]) => { setDetail(d); setIntake(i); })
      .catch((e) => setLoadError((e as Error).message));
  }, [caseId]);

  const report = intake?.report ?? null;
  const slotOf = useMemo(() => new Map((intake?.documents ?? []).map((d) => [d.id, d.slot])), [intake]);
  const slotLabel = useCallback((key: string | null | undefined) => intake?.slots.find((s) => s.key === key)?.label ?? null, [intake]);
  const docName = useCallback((id: string | null | undefined) => {
    const d = detail?.documents.find((x) => x.id === id);
    if (!d) return "";
    const label = slotLabel(slotOf.get(d.id));
    return label && label !== "Additional document" ? label : d.filename;
  }, [detail, slotLabel, slotOf]);

  // findings numbered in panel order (the report is sorted by level already; keep it stable)
  const items: PanelItem[] = useMemo(() => {
    const fs = report?.findings ?? [];
    const sorted = LEVEL_ORDER.flatMap((l) => fs.map((f, i) => ({ f, i })).filter((x) => x.f.level === l));
    return sorted.map(({ f, i }, n) => {
      const anchors = anchorsOf(f);
      return { id: f.id ?? `f${i}`, number: n + 1, f, anchors, placed: anchors.some((a) => a.kind !== "missing") };
    });
  }, [report]);

  const visible = useMemo(() => new Set(items.filter((it) => filters.levels.includes(it.f.level)).map((it) => it.id)), [items, filters.levels]);

  const factMark: ViewerMark | null = useMemo(() => {
    const row = fact !== null ? report?.facts[fact] : undefined;
    const a = row?.anchor;
    if (!row || !a || a.kind === "missing" || !a.page_seq) return null;
    return { id: `fact:${fact}`, itemId: `fact:${fact}`, number: "★", level: "fact", pageKey: String(a.page_seq),
             kind: a.kind === "highlight" ? "highlight" : "page", boxes: a.boxes, label: `${row.fact}: ${row.raw ?? ""}` };
  }, [fact, report]);

  const marks: ViewerMark[] = useMemo(() => {
    const out: ViewerMark[] = [];
    for (const it of items) {
      if (!visible.has(it.id) && it.id !== selected) continue;
      it.anchors.forEach((a, i) => {
        if (a.kind === "missing" || !a.page_seq) return;
        out.push({
          id: `${it.id}:${i}`, itemId: it.id, number: it.number, level: it.f.level, pageKey: String(a.page_seq),
          kind: a.kind === "highlight" && a.boxes.length ? "highlight" : "page", boxes: a.boxes,
          label: a.kind === "highlight" ? it.f.title
            : a.reason === "no_geometry" ? `Couldn't place on the page — ${it.f.title}` : it.f.title,
        });
      });
    }
    if (factMark) out.push(factMark);
    return out;
  }, [items, visible, selected, factMark]);

  const setUrl = useCallback((at: Parameters<typeof viewerHref>[1]) => {
    window.history.replaceState(null, "", viewerHref(caseId, at));
  }, [caseId]);

  const select = useCallback((id: string, a?: number) => {
    const it = items.find((x) => x.id === id);
    setSelected(id);
    setFact(null);
    if (!it) return;
    if (!filters.levels.includes(it.f.level)) setFilters((f) => ({ ...f, levels: [...f.levels, it.f.level] }));
    const target = a !== undefined && it.anchors[a]?.kind !== "missing" ? a : it.anchors.findIndex((x) => x.kind !== "missing");
    setUrl({ f: id, a: target >= 0 ? target : undefined, tab });
    if (tab === "text") {
      const an = it.anchors[target];
      if (an?.page_seq) setTextAt({ seq: an.page_seq, lines: an.lines, q: an.quote });
      return;
    }
    if (target >= 0) window.setTimeout(() => viewer.current?.scrollToMark(`${id}:${target}`), 30);
    else {
      const docId = it.anchors.find((x) => x.document_id)?.document_id;
      const first = detail?.documents.find((d) => d.id === docId)?.page_list[0];
      if (first) viewer.current?.scrollToPage(String(first.seq));
    }
  }, [items, filters.levels, setUrl, tab, detail]);

  // documents and their pages; findings about a document but no page of it show as a banner above its first page
  const docs: ViewerDoc[] = useMemo(() => (detail?.documents ?? []).filter((d) => d.page_list.length).map((d) => {
    const about = items.filter((it) => visible.has(it.id) && it.anchors.some((a) => a.kind === "missing" && a.document_id === d.id));
    return {
      id: d.id,
      name: docName(d.id) === d.filename ? d.filename : `${docName(d.id)} · ${d.filename}`,
      pages: d.page_list.map((p) => ({
        key: String(p.seq), docId: d.id, pageNo: p.page_no, width: p.width_px || A4.w, height: p.height_px || A4.h,
        src: (w: number) => pageImageUrl(caseId, p.seq, false, w),
      })),
      banner: about.length ? (
        <div className="dv-banner">
          {about.map((it) => (
            <button key={it.id} className={`dv-banner-row${it.id === selected ? " sel" : ""}`} onClick={() => select(it.id)}>
              <span className={`pin static mk-${it.f.level}`}>{it.number}</span> {it.f.title}
            </button>
          ))}
        </div>
      ) : undefined,
    };
  }), [detail, items, visible, selected, docName, caseId, select]);

  const openText = useCallback((a: Anchor) => {
    if (!a.page_seq) return;
    setTextAt({ seq: a.page_seq, lines: a.lines, q: a.quote });
    setTab("text");
    setUrl({ f: selected ?? undefined, seq: a.page_seq, tab: "text" });
  }, [selected, setUrl]);

  const switchTab = (t: "pages" | "text") => {
    setTab(t);
    setUrl({ f: selected ?? undefined, seq: t === "text" ? textAt?.seq : undefined, tab: t });
  };

  // deep link, once the pages are laid out
  useEffect(() => {
    if (!detail || !intake) return;
    const p = initial.current;
    if (!p) return;
    initial.current = {} as DocumentsParams;
    const go = () => {
      if (p.f) {
        const it = items.find((x) => x.id === p.f);
        const a = p.a !== undefined ? Number(p.a) : undefined;
        if (it && p.tab === "text") {
          const an = it.anchors[a ?? it.anchors.findIndex((x) => x.page_seq)];
          if (an?.page_seq && !p.seq) setTextAt({ seq: an.page_seq, lines: an.lines, q: an.quote });
        } else if (it) select(it.id, a);
      } else if (factMark) viewer.current?.scrollToMark(factMark.id);
      else if (p.seq && p.tab !== "text") viewer.current?.scrollToPage(p.seq);
    };
    window.setTimeout(go, 60);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail, intake]);

  if (loadError) return <main className="page"><div className="error"><Icon name="alert" /> <span>{loadError}</span></div></main>;
  if (!detail || !intake) return <main className="page muted"><span className="spinner" /> Loading documents…</main>;

  const factRow = fact !== null ? report?.facts[fact] : undefined;
  const sidebar = report ? (
    <>
      <Summary report={report} />
      {factRow && (
        <div className="fp-fact">
          <span className="pin static mk-fact">★</span>
          <span><b>{factRow.fact}</b> — {factRow.raw ?? String(factRow.value ?? "")} <span className="muted">({docName(factRow.document_id)})</span></span>
          <button className="icon small ghost" aria-label="Close" onClick={() => { setFact(null); setUrl({ tab }); }}><Icon name="x" size={13} /></button>
        </div>
      )}
      <FindingsPanel items={items} selected={selected} onSelect={select} onOpenText={openText} filters={filters} setFilters={setFilters}
        documents={detail.documents.filter((d) => d.page_list.length).map((d) => ({ id: d.id, name: docName(d.id) }))} docName={docName} />
    </>
  ) : (
    <div className="notice" style={{ margin: 12 }}>
      <Icon name="info" />
      <span>{intake.state === "processing" ? "The document check is still running." : "There is no document check for this case yet."} <a href={`/cases/${caseId}/intake`}>Document check</a></span>
    </div>
  );

  return (
    <div className="review docs">
      <div className="review-bar">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb" style={{ marginBottom: 2 }}>
            <a href="/">Cases</a><span className="sep">/</span><a href={`/cases/${caseId}/intake`}>{detail.case_ref}</a>
          </nav>
          <div className="row" style={{ gap: 10 }}>
            <span className="title">Documents</span>
            {detail.society_name && <span className="muted">{detail.society_name}</span>}
          </div>
        </div>
        <div className="seg" role="tablist" aria-label="View">
          <button role="tab" className={tab === "pages" ? "on" : ""} aria-selected={tab === "pages"} onClick={() => switchTab("pages")}><Icon name="image" size={13} /> Pages</button>
          <button role="tab" className={tab === "text" ? "on" : ""} aria-selected={tab === "text"} onClick={() => switchTab("text")}><Icon name="text" size={13} /> Text</button>
        </div>
        <span className="spacer" />
        <a className="btn" href={`/cases/${caseId}/intake`}><Icon name="clipboard" /> Full report</a>
      </div>
      {tab === "pages" ? (
        <DocViewer ref={viewer} docs={docs} marks={marks} selectedItem={selected ?? factMark?.itemId}
          onSelectMark={(m) => { if (m.itemId.startsWith("fact:")) return; setSelected(m.itemId); setUrl({ f: m.itemId, a: Number(m.id.split(":")[1]), tab }); }}
          sidebar={sidebar} />
      ) : (
        <TextView caseId={caseId} detail={detail} seq={textAt?.seq} lines={textAt?.lines} q={textAt?.q} notify={notify} />
      )}
      {toast && (
        <div className={`toast ${toast.kind}`} role="status">
          <Icon name={toast.kind === "success" ? "check" : toast.kind === "error" ? "alert" : "info"} /> <span>{toast.text}</span>
        </div>
      )}
    </div>
  );
}
