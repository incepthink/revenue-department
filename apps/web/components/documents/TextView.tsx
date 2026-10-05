"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CaseDetail } from "@/lib/api";
import Icon from "../Icon";
import DocumentRail from "../review/DocumentRail";
import PageViewer, { type ViewerPage } from "../review/PageViewer";

type Notify = (kind: "error" | "success" | "notice", text: string) => void;

// The Text tab of the documents screen: pick an uploaded document on the left, read its extracted (redacted) text or
// page images, and copy lines, pages or the whole document. `seq` opens a page; `lines` (OCR line indices) or `q`
// (a cited snippet) mark the cited lines on it.
export default function TextView({ caseId, detail, seq, lines, q, notify }: {
  caseId: string; detail: CaseDetail; seq?: number | null; lines?: number[] | null; q?: string | null; notify: Notify;
}) {
  const documents = detail.documents;
  const [selDoc, setSelDoc] = useState<string | null>(null);
  const [page, setPageState] = useState<number | null>(null);
  const [cite, setCite] = useState<{ seq: number; lines?: number[] | null; q?: string | null } | null>(null);

  const pages: ViewerPage[] = useMemo(
    () => documents.flatMap((d) => d.page_list.map((p) => ({ seq: p.seq, document: d.filename, page_no: p.page_no }))),
    [documents],
  );

  // open the requested page (with its cited lines), else the first document that has pages
  const opened = useRef(false);
  useEffect(() => {
    const target = seq ? documents.find((d) => d.page_list.some((p) => p.seq === seq)) : null;
    const first = target ?? documents.find((d) => d.page_list.length);
    if (!first || (opened.current && !target)) return;
    opened.current = true;
    setSelDoc(first.id);
    setPageState(target && seq ? seq : first.page_list[0].seq);
    setCite(target && seq ? { seq, lines, q } : null);
  }, [documents, seq, lines, q]);

  const setPage = useCallback((s: number) => setPageState(s), []);
  const doc = documents.find((d) => d.id === selDoc) ?? null;
  const docPages = doc?.page_list.map((p) => p.seq) ?? [];
  const here = cite && cite.seq === page ? cite : null;

  return (
    <div className="review-grid extract docs-text">
      <div className="pane">
        <DocumentRail documents={documents} selected={selDoc} onSelect={(id) => {
          const d = documents.find((x) => x.id === id);
          setSelDoc(id);
          if (d?.page_list.length) setPageState(d.page_list[0].seq);
        }} />
      </div>
      <div className="pane viewer">
        {doc && page !== null ? (
          <PageViewer caseId={caseId} pages={pages} docPages={docPages} page={page} setPage={setPage}
            canUnmask notify={notify} highlight={here?.lines} q={here?.q} />
        ) : (
          <div className="viewer-empty">
            <span className="empty-icon"><Icon name="fileText" size={22} /></span>
            <b style={{ color: "var(--text)" }}>{documents.length ? "No pages extracted yet" : "No documents"}</b>
            <span>{documents.length ? "Run the document check first to extract the pages." : "No documents were uploaded for this case."}</span>
          </div>
        )}
      </div>
    </div>
  );
}
