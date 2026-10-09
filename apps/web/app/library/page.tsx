import Icon from "@/components/Icon";
import { fmtDate, type LibraryDoc, type LibraryTaxonomy } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

// The GR library: every GR of the department's compendium, grouped by its section (A–L), newest first, with the OCR
// lines still waiting for review and whether the metadata has been confirmed.
export default async function LibraryPage({ searchParams }: { searchParams: Promise<{ section?: string; type?: string }> }) {
  const sp = await searchParams;
  const qs = new URLSearchParams();
  if (sp.section) qs.set("section", sp.section);
  if (sp.type) qs.set("type", sp.type);
  const [docs, tx] = await Promise.all([
    apiGet<LibraryDoc[]>(`/library/documents${qs.toString() ? `?${qs}` : ""}`),
    apiGet<LibraryTaxonomy>("/library/taxonomy"),
  ]);
  const sections = Object.entries(tx.sections);
  const groups = sections
    .map(([letter, s]) => ({
      letter, s,
      docs: docs.filter((d) => d.section === letter).sort((a, b) => (b.doc_date ?? "").localeCompare(a.doc_date ?? "")),
    }))
    .filter((g) => g.docs.length);
  const review = docs.reduce((n, d) => n + d.lines_to_review, 0);
  const href = (section?: string, type?: string) => {
    const q = new URLSearchParams();
    if (section) q.set("section", section);
    if (type) q.set("type", type);
    return `/library${q.toString() ? `?${q}` : ""}`;
  };

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb"><a href="/library/search">GR search</a><span className="sep">/</span>Library</nav>
          <h1>GR library</h1>
          <div className="sub">
            The GRs, notifications and circulars of the department&apos;s compendium ({docs.length} documents), by section. Metadata
            comes from the compendium&apos;s index and the AI; confirm it on each document. Lines the OCR could not settle wait
            for review.
          </div>
        </div>
        <div className="actions-bar">
          {review > 0 && <span className="pill status lvl-warning">{review} lines to review</span>}
          <a className="btn" href="/library/timeline"><Icon name="layers" /> Timeline</a>
          <a className="btn" href="/library/missing"><Icon name="alert" /> Missing GRs</a>
          <a className="btn" href="/library/precedents"><Icon name="clipboard" /> Accepted cases</a>
          <a className="btn primary" href="/library/search"><Icon name="search" /> Search the GRs</a>
        </div>
      </div>

      <div className="lib-filters">
        <a className={`btn small${!sp.section ? " on" : ""}`} href={href(undefined, sp.type)}>All sections</a>
        {sections.map(([letter, s]) => (
          <a key={letter} className={`btn small${sp.section === letter ? " on" : ""}`} href={href(letter, sp.type)} title={s.mr}>
            {letter} · {s.en}
          </a>
        ))}
        <span className="lib-break" />
        <span className="muted">Type:</span>
        <a className={`btn small${!sp.type ? " on" : ""}`} href={href(sp.section)}>All</a>
        {Object.entries(tx.types).map(([k, t]) => (
          <a key={k} className={`btn small${sp.type === k ? " on" : ""}`} href={href(sp.section, k)}>{t.en}</a>
        ))}
      </div>

      {groups.map((g) => (
        <section key={g.letter} className="card table-card lib-section">
          <div className="lib-section-head">
            <span className="lib-letter">{g.letter}</span>
            <div><b>{g.s.en}</b><div className="muted lib-mr">{g.s.mr}</div></div>
          </div>
          <div className="scroll">
            <table className="data">
              <thead>
                <tr><th>Document</th><th>Date</th><th>Subject</th><th>Pages</th><th>OCR review</th><th>Metadata</th></tr>
              </thead>
              <tbody>
                {g.docs.map((d) => (
                  <tr key={d.id}>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <a href={`/library/${d.id}`}><b>{d.key}</b></a>
                      <div className="muted">{d.type_label}{d.doc_number ? ` · ${d.doc_number}` : ""}</div>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>{fmtDate(d.doc_date) || <span className="muted">—</span>}</td>
                    <td className="lib-subject">
                      <a href={`/library/${d.id}`} lang="mr">{d.subject_mr ?? "—"}</a>
                      {d.subject_en && <div className="muted">{d.subject_en}</div>}
                    </td>
                    <td className="muted" style={{ whiteSpace: "nowrap" }}>
                      {d.page_count ?? "—"} <span title="Pages of the compendium PDF">(PDF {d.source_pages[0]}–{d.source_pages[1]})</span>
                    </td>
                    <td>
                      {d.lines_to_review
                        ? <a className="pill status lvl-warning" href={`/library/${d.id}?tab=lines`}>{d.lines_to_review} to review</a>
                        : <span className="pill status lvl-ok">Done</span>}
                    </td>
                    <td>
                      {d.verification === "verified"
                        ? <span className="pill status lvl-ok">Confirmed</span>
                        : <span className="pill status lvl-unchecked">To verify</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      {!groups.length && (
        <div className="card">
          <div className="empty-state">
            <span className="empty-icon"><Icon name="book" size={22} /></span>
            <b>No GRs loaded</b>
            <span>Read and load the compendium: <code>make library-ocr</code> then <code>make library-load</code>.</span>
          </div>
        </div>
      )}
    </main>
  );
}
