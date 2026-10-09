import Icon from "@/components/Icon";
import { fmtDate, sourceHref, type MissingGr } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

// The GRs the department's GRs and accepted case notes refer to that are not in the compendium: the list to send to the
// department, each with every place it is cited (opens the page with the citing words marked).
export default async function MissingGrsPage() {
  const { missing } = await apiGet<{ missing: MissingGr[]; count: number }>("/library/missing");
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb"><a href="/library/search">GR search</a><span className="sep">/</span><a href="/library">Library</a><span className="sep">/</span>Missing GRs</nav>
          <h1>GRs cited but not in the library</h1>
          <div className="sub">
            GRs, notifications, circulars and letters that the compendium&apos;s GRs and the accepted case notes refer to, but
            that the department has not provided. Answers and rules that depend on them cannot be checked until they are added.
          </div>
        </div>
        <div className="actions-bar">
          <a className="btn" href="/api/rci/library/missing?format=md" download="missing_grs.md"><Icon name="copy" /> Download the list</a>
          <a className="btn" href="/library/timeline"><Icon name="layers" /> Timeline</a>
        </div>
      </div>
      <section className="card table-card">
        <div className="scroll">
          <table className="data">
            <thead><tr><th>#</th><th>Date</th><th>Number</th><th>Type</th><th>Cited by</th></tr></thead>
            <tbody>
              {missing.map((m, i) => (
                <tr key={`${m.date}-${m.number}-${i}`}>
                  <td>{i + 1}</td>
                  <td style={{ whiteSpace: "nowrap" }}><b>{m.date ? fmtDate(m.date) : m.date_printed ?? "—"}</b></td>
                  <td lang="mr">{m.number ?? <span className="muted">—</span>}</td>
                  <td>{m.kinds.join(", ")}</td>
                  <td>
                    {m.cited_by.map((c) => {
                      const href = sourceHref({ document_id: c.document_id, page_no: c.page_no, quote: c.quote });
                      return (
                        <div key={c.link_id} className="lib-cited-by">
                          {href ? <a className="cite-chip" href={href} title={c.quote ?? undefined}>{c.key}{c.label ? ` ${c.label}` : ""}{c.page_no ? ` · p.${c.page_no}` : ""}</a> : c.key}
                          {c.collection === "case_note" && <span className="badge">accepted case</span>}
                          {c.status === "confirmed" && <span className="badge">confirmed</span>}
                        </div>
                      );
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!missing.length && (
          <div className="empty-state">
            <b>No missing GRs found yet</b>
            <span>The list fills when the references are read: <code>make library-links</code>.</span>
          </div>
        )}
      </section>
    </main>
  );
}
