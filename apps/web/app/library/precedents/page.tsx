import Icon from "@/components/Icon";
import { fmtDate, GR_STATUS, type PrecedentView } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

const OUTCOME: Record<string, string> = { approved: "Approved", rejected: "Rejected", partly: "Partly approved", proposed: "Proposed" };

// The department's accepted cases, read from its case notes: what was decided, what was charged and under which GRs.
// Case analysis matches new proposals against them.
export default async function PrecedentsPage() {
  const rows = await apiGet<PrecedentView[]>("/library/precedents");
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb"><a href="/library/search">GR search</a><span className="sep">/</span><a href="/library">Library</a><span className="sep">/</span>Accepted cases</nav>
          <h1>Accepted cases</h1>
          <div className="sub">
            Cases the department decided, read from its case notes. Each value links to the page of the note it was read
            from; the GRs it applied show whether they are still in force. Case analysis shows the ones similar to a new
            proposal. The notes are not part of GR search.
          </div>
        </div>
      </div>
      {rows.map((p) => (
        <section key={p.id} className="card lib-prec-card">
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <a href={`/library/${p.document_id}?tab=details`}><b>{p.title ?? p.key}</b></a>
            <span className={`pill status ${p.outcome === "approved" ? "lvl-ok" : "lvl-info"}`}>{OUTCOME[p.outcome ?? "proposed"] ?? p.outcome}</span>
            {p.decided_on && <span className="muted">decided {fmtDate(p.decided_on)}</span>}
            {p.status === "verified" ? <span className="pill status lvl-ok">Confirmed</span> : <span className="pill status lvl-unchecked">To verify</span>}
          </div>
          <p className="lib-statement">{p.statement.en}</p>
          <div className="fp-chips">
            {p.applied_grs.map((g) => (
              <span key={g.link_id} className={`pill status ${GR_STATUS[g.status].lvl}`} title={GR_STATUS[g.status].text}>{g.label}</span>
            ))}
          </div>
        </section>
      ))}
      {!rows.length && (
        <div className="card">
          <div className="empty-state">
            <span className="empty-icon"><Icon name="inbox" size={22} /></span>
            <b>No accepted cases loaded</b>
            <span>The three case notes are listed in <code>config/library/case_notes.yaml</code>; read and load them with
              <code> make library-notes-ocr</code> then <code>make library-notes-load</code>.</span>
          </div>
        </div>
      )}
    </main>
  );
}
