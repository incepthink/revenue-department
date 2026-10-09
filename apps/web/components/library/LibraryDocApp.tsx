"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtDate, libraryImageUrl, type Anchor, type LibraryDocDetail, type LibraryTaxonomy, type Provision } from "@/lib/api";
import { get, send } from "@/lib/client";
import Icon from "../Icon";
import DocViewer, { type DocViewerHandle, type ViewerDoc, type ViewerMark } from "../viewer/DocViewer";
import LinesPanel from "./LinesPanel";
import LinkRow from "./LinkRow";
import MetaPanel from "./MetaPanel";
import PrecedentPanel from "./PrecedentPanel";

const PROV_STATUS: Record<string, { text: string; lvl: string }> = {
  superseded: { text: "Superseded", lvl: "lvl-problem" }, amended: { text: "Amended", lvl: "lvl-info" },
  not_yet: { text: "Not yet in force", lvl: "lvl-unchecked" },
};

export type LibraryDocParams = { prov?: string; page?: string; q?: string; tab?: string };
type Tab = "outline" | "lines" | "details";
type Toast = { kind: "error" | "success" | "notice"; text: string } | null;
export type Notify = (kind: "error" | "success" | "notice", text: string) => void;
export type LineMark = { id: string; bbox: [number, number, number, number]; level: "review" | "line"; label: string; page: number };

const A4 = { w: 1654, h: 2339 };

function provTitle(t: Provision["timeline"]): string | undefined {
  if (!t) return undefined;
  const rows = [...(t.superseded_by ?? []).map((e) => `superseded by ${e.doc_key} ${e.label ?? ""} from ${e.date ?? "?"}`),
                ...(t.amended_by ?? []).map((e) => `amended by ${e.doc_key} ${e.label ?? ""} (${e.date ?? "?"})`),
                ...(t.corrected_by ?? []).map((e) => `corrected by ${e.doc_key} (${e.date ?? "?"})`),
                ...(t.clarified_by ?? []).map((e) => `clarified by ${e.doc_key} (${e.date ?? "?"})`)];
  return rows.length ? rows.join("; ") : undefined;
}

export default function LibraryDocApp({ docId, params }: { docId: string; params: LibraryDocParams }) {
  const [doc, setDoc] = useState<LibraryDocDetail | null>(null);
  const [tx, setTx] = useState<LibraryTaxonomy | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(params.tab === "lines" ? "lines" : params.tab === "details" ? "details" : "outline");
  const [prov, setProv] = useState<string | null>(params.prov ?? null);
  const [cite, setCite] = useState<Anchor | null>(null);
  const [linePage, setLinePage] = useState<number>(Number(params.page) || 1);
  const [lineMarks, setLineMarks] = useState<LineMark[]>([]);
  const [selLine, setSelLine] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast>(null);
  const viewer = useRef<DocViewerHandle>(null);
  const initial = useRef(params);

  const notify: Notify = useCallback((kind, text) => {
    setToast({ kind, text });
    window.setTimeout(() => setToast((t) => (t?.text === text ? null : t)), kind === "error" ? 8000 : 3500);
  }, []);

  const reload = useCallback(() => get<LibraryDocDetail>(`/library/documents/${docId}`).then(setDoc).catch((e) => setLoadError((e as Error).message)), [docId]);
  useEffect(() => { reload(); get<LibraryTaxonomy>("/library/taxonomy").then(setTx).catch(() => setTx(null)); }, [reload]);

  // a cited quote (?q=&page=): placed on its page by the API
  useEffect(() => {
    const p = initial.current;
    if (!p.q || !p.page) return;
    get<Anchor>(`/library/documents/${docId}/locate?page=${p.page}&q=${encodeURIComponent(p.q)}`)
      .then(setCite).catch(() => notify("notice", "The quoted words could not be placed on the page; the passage is marked instead."));
  }, [docId, notify]);

  const docs: ViewerDoc[] = useMemo(() => doc ? [{
    id: doc.id, name: `${doc.key} · ${doc.type_label}${doc.doc_date ? ` ${fmtDate(doc.doc_date)}` : ""}`,
    pages: doc.pages.map((p) => ({
      key: String(p.page_no), docId: doc.id, pageNo: p.page_no, width: p.width_px ?? A4.w, height: p.height_px ?? A4.h,
      src: (w: number) => libraryImageUrl(doc.id, p.page_no, w),
    })),
  }] : [], [doc]);

  const provision = doc?.provisions.find((p) => p.id === prov) ?? null;
  const marks: ViewerMark[] = useMemo(() => {
    const out: ViewerMark[] = [];
    provision?.anchors.forEach((a, i) => {
      if (!a.page_no || a.kind === "missing") return;
      out.push({ id: `prov:${i}`, itemId: "prov", number: provision.label || "§", level: "hit", pageKey: String(a.page_no),
                 kind: a.kind === "highlight" && a.boxes.length ? "highlight" : "page", boxes: a.boxes, label: provision.label });
    });
    if (cite?.page_no) {
      out.push({ id: "cite", itemId: "cite", number: "❝", level: "cite", pageKey: String(cite.page_no),
                 kind: cite.kind === "highlight" && cite.boxes.length ? "highlight" : "page", boxes: cite.boxes, label: cite.quote ?? "Quoted" });
    }
    if (tab === "lines") {
      for (const m of lineMarks) {
        out.push({ id: `line:${m.id}`, itemId: `line:${m.id}`, number: m.label, level: m.level, pageKey: String(m.page),
                   kind: "highlight", boxes: [m.bbox], label: m.label });
      }
    }
    return out;
  }, [provision, cite, tab, lineMarks]);

  // deep link: scroll to the cite, else the provision, else the page, once the pages are laid out
  useEffect(() => {
    if (!doc) return;
    const p = initial.current;
    const t = window.setTimeout(() => {
      if (cite) viewer.current?.scrollToMark("cite");
      else if (p.prov && provision) viewer.current?.scrollToMark("prov:0");
      else if (p.page) viewer.current?.scrollToPage(p.page);
    }, 80);
    return () => window.clearTimeout(t);
  }, [doc, cite, provision]);

  const selectProv = (id: string) => {
    setProv(id);
    window.history.replaceState(null, "", `/library/${docId}?prov=${id}`);
    window.setTimeout(() => viewer.current?.scrollToMark("prov:0"), 40);
  };

  const switchTab = (t: Tab) => {
    setTab(t);
    const sp = new URLSearchParams(window.location.search);
    if (t === "outline") sp.delete("tab"); else sp.set("tab", t);
    window.history.replaceState(null, "", `/library/${docId}${sp.toString() ? `?${sp}` : ""}`);
  };

  if (loadError) return <main className="page"><div className="error"><Icon name="alert" /> <span>{loadError}</span></div></main>;
  if (!doc) return <main className="page muted"><span className="spinner" /> Loading the GR…</main>;
  const isNote = doc.collection === "case_note";

  const sidebar = (
    <div className="lib-side">
      <div className="pane-head lib-tabs">
        <div className="seg" role="tablist" aria-label="Sidebar">
          <button role="tab" className={tab === "outline" ? "on" : ""} aria-selected={tab === "outline"} onClick={() => switchTab("outline")}>Outline</button>
          <button role="tab" className={tab === "lines" ? "on" : ""} aria-selected={tab === "lines"} onClick={() => switchTab("lines")}>
            OCR lines{doc.lines_to_review ? <span className="lib-count">{doc.lines_to_review}</span> : null}
          </button>
          <button role="tab" className={tab === "details" ? "on" : ""} aria-selected={tab === "details"} onClick={() => switchTab("details")}>Details</button>
        </div>
      </div>
      {doc.needs_reindex && (
        <div className="notice lib-reindex">
          <Icon name="info" />
          <span>Lines were corrected since this GR was indexed: search still uses the old text.</span>
          <button className="small" onClick={async () => {
            try { await send("POST", `/library/documents/${doc.id}/reindex`); await reload(); notify("success", "Re-indexed."); }
            catch (e) { notify("error", (e as Error).message); }
          }}><Icon name="refresh" size={13} /> Re-index now</button>
        </div>
      )}
      {tab === "outline" && (
        <ol className="lib-outline">
          {doc.provisions.map((p) => (
            <li key={p.id}>
              <button className={`lib-prov${p.id === prov ? " on" : ""}`} onClick={() => selectProv(p.id)}>
                <span className="lib-prov-label">{p.label || p.kind}</span>
                <span className="lib-prov-text" lang="mr">
                  {p.status && PROV_STATUS[p.status] && (
                    <span className={`pill status ${PROV_STATUS[p.status].lvl}`} title={provTitle(p.timeline)}>{PROV_STATUS[p.status].text}</span>
                  )}{" "}
                  {p.snippet}
                </span>
                <span className="muted lib-prov-page">p.{p.page_from}{p.page_to !== p.page_from ? `–${p.page_to}` : ""}</span>
              </button>
            </li>
          ))}
          {!doc.provisions.length && <li className="muted" style={{ padding: 14 }}>No provisions: the GR has not been indexed.</li>}
        </ol>
      )}
      {tab === "lines" && (
        <LinesPanel doc={doc} page={linePage} setPage={(n) => { setLinePage(n); viewer.current?.scrollToPage(String(n)); }}
          selected={selLine} onSelect={(id, n) => { setSelLine(id); window.setTimeout(() => viewer.current?.scrollToMark(`line:${id}`), 40); void n; }}
          setMarks={setLineMarks} onChanged={reload} notify={notify} />
      )}
      {tab === "details" && (
        <div className="lib-details">
          {doc.precedent && <PrecedentPanel p={doc.precedent} onSaved={reload} notify={notify} />}
          <section className="lib-links">
            <h4>{isNote ? "GRs this note cites" : "What this document says about other GRs"} <span className="muted">({doc.links_out?.length ?? 0})</span></h4>
            {(doc.links_out ?? []).map((l) => <LinkRow key={l.id} link={l} onChanged={reload} notify={notify} />)}
            {!doc.links_out?.length && <p className="muted">None found yet (<code>rci library links</code> proposes them).</p>}
            {!isNote && (
              <>
                <h4>What other documents say about it <span className="muted">({doc.links_in?.length ?? 0})</span></h4>
                {(doc.links_in ?? []).map((l) => <LinkRow key={l.id} link={l} onChanged={reload} notify={notify} />)}
                {!doc.links_in?.length && <p className="muted">No other document refers to it.</p>}
              </>
            )}
          </section>
          {tx && <MetaPanel doc={doc} tx={tx} onSaved={reload} notify={notify} />}
        </div>
      )}
    </div>
  );

  return (
    <div className="review docs lib-doc">
      <div className="review-bar">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb" style={{ marginBottom: 2 }}>
            <a href="/library/search">GR search</a><span className="sep">/</span>
            {isNote ? <a href="/library/precedents">Accepted cases</a> : <a href="/library">Library</a>}<span className="sep">/</span>{doc.key}
          </nav>
          <div className="row" style={{ gap: 10 }}>
            <span className="title">{doc.type_label}{doc.doc_date ? ` ${fmtDate(doc.doc_date)}` : ""}</span>
            {doc.doc_number && <span className="muted">{doc.doc_number}</span>}
            {doc.verification === "verified"
              ? <span className="pill status lvl-ok">Confirmed</span>
              : <span className="pill status lvl-unchecked">Metadata to verify</span>}
          </div>
          <div className="muted lib-bar-subject" lang="mr">{doc.subject_mr}</div>
        </div>
        <span className="spacer" />
        <a className="btn" href="/library/search"><Icon name="search" /> Search</a>
      </div>
      <DocViewer ref={viewer} docs={docs} marks={marks} selectedItem={tab === "lines" && selLine ? `line:${selLine}` : prov ? "prov" : null}
        onSelectMark={(m) => {
          if (m.itemId.startsWith("line:")) { setSelLine(m.itemId.slice(5)); if (tab !== "lines") switchTab("lines"); }
        }}
        sidebar={sidebar}
        toolbar={<span className="muted dv-hint">{isNote ? "Accepted case note (not searched as a GR)" : `PDF pages ${doc.source_pages[0]}–${doc.source_pages[1]} of the compendium`}</span>} />
      {toast && (
        <div className={`toast ${toast.kind}`} role="status">
          <Icon name={toast.kind === "success" ? "check" : toast.kind === "error" ? "alert" : "info"} /> <span>{toast.text}</span>
        </div>
      )}
    </div>
  );
}
