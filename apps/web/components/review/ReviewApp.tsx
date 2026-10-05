"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { CaseDetail } from "@/lib/api";
import { get } from "@/lib/client";
import Icon from "../Icon";
import DocumentRail from "./DocumentRail";
import PageViewer, { type ViewerPage } from "./PageViewer";

type Toast = { kind: "error" | "success" | "notice"; text: string } | null;

// Review = text extraction: pick an uploaded document on the left, read its extracted (redacted) text
// or page images in the viewer, and copy lines, pages or the whole document.
export default function ReviewApp({ caseId }: { caseId: string }) {
  const [c, setCase] = useState<CaseDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast>(null);
  const [selDoc, setSelDoc] = useState<string | null>(null);
  const [page, setPage] = useState<number | null>(null);

  const notify = useCallback((kind: "error" | "success" | "notice", text: string) => {
    setToast({ kind, text });
    window.setTimeout(() => setToast((t) => (t?.text === text ? null : t)), kind === "error" ? 8000 : 3500);
  }, []);

  useEffect(() => {
    get<CaseDetail>(`/cases/${caseId}`).then(setCase).catch((e) => setLoadError((e as Error).message));
  }, [caseId]);

  const documents = c?.documents ?? [];
  const pages: ViewerPage[] = useMemo(
    () => documents.flatMap((d) => d.page_list.map((p) => ({ seq: p.seq, document: d.filename, page_no: p.page_no }))),
    [documents],
  );

  // default to the first document that has pages
  useEffect(() => {
    if (selDoc || !documents.length) return;
    const first = documents.find((d) => d.page_list.length);
    if (first) {
      setSelDoc(first.id);
      setPage(first.page_list[0].seq);
    }
  }, [documents, selDoc]);

  if (loadError) return <main className="page"><div className="error"><Icon name="alert" /> <span>{loadError}</span></div></main>;
  if (!c) return <main className="page muted"><span className="spinner" /> Loading documents…</main>;

  const doc = documents.find((d) => d.id === selDoc) ?? null;
  const docPages = doc?.page_list.map((p) => p.seq) ?? [];

  return (
    <div className="review">
      <div className="review-bar">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb" style={{ marginBottom: 2 }}>
            <a href="/">Cases</a><span className="sep">/</span><a href={`/cases/${caseId}/intake`}>{c.case_ref}</a>
          </nav>
          <div className="row" style={{ gap: 10 }}>
            <span className="title">View documents</span>
            {c.society_name && <span className="muted">{c.society_name}</span>}
            <span className="badge"><Icon name="text" size={12} /> text extraction</span>
          </div>
        </div>
        <span className="spacer" />
        <a className="btn" href={`/cases/${caseId}/intake`}><Icon name="clipboard" /> Document check</a>
      </div>

      <div className="review-grid extract">
        <div className="pane">
          <DocumentRail documents={documents} selected={selDoc} onSelect={(id) => {
            const d = documents.find((x) => x.id === id);
            setSelDoc(id);
            if (d?.page_list.length) setPage(d.page_list[0].seq);
          }} />
        </div>
        <div className="pane viewer">
          {doc && page !== null ? (
            <PageViewer caseId={caseId} pages={pages} docPages={docPages} page={page} setPage={setPage}
              canUnmask notify={notify} />
          ) : (
            <div className="viewer-empty">
              <span className="empty-icon"><Icon name="fileText" size={22} /></span>
              <b style={{ color: "var(--text)" }}>{documents.length ? "No pages extracted yet" : "No documents"}</b>
              <span>{documents.length ? "Run the document check first to extract the pages." : "No documents were uploaded for this case."}</span>
            </div>
          )}
        </div>
      </div>
      {toast && (
        <div className={`toast ${toast.kind}`} role="status">
          <Icon name={toast.kind === "success" ? "check" : toast.kind === "error" ? "alert" : "info"} /> <span>{toast.text}</span>
        </div>
      )}
    </div>
  );
}
