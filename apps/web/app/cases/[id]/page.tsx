import { STATUS_COLOUR, fmtValue, type CaseDetail, type Fact } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

function isFact(v: unknown): v is NonNullable<Fact> {
  return typeof v === "object" && v !== null && "page_seq" in v && "value" in v;
}

export default async function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const c = await apiGet<CaseDetail>(`/cases/${id}`);
  const card = c.card?.card;
  const groups = card ? (Object.entries(card.facts).filter(([, v]) => !Array.isArray(v)) as [string, Record<string, Fact>][]) : [];
  const approved = c.card?.status === "approved";
  return (
    <main className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1 style={{ marginBottom: 2 }}>{c.case_ref} <small className="muted">{c.society_name}</small></h1>
          <span className="muted">
            Pipeline: {c.status} · {c.documents.length} document(s) · card v{c.card?.version ?? "—"}{" "}
            <span className="pill" style={{ color: STATUS_COLOUR[c.card?.status ?? ""] ?? "var(--muted)" }}>{c.card?.status ?? "none"}</span>
            {c.card?.locked && " · locked"}
          </span>
        </div>
        <div className="row">
          <a className="btn" href={`/cases/${id}/intake`}>Document check</a>
          <a className="btn primary" href={`/cases/${id}/documents`}>View Documents</a>
        </div>
      </div>

      {card?.summary && (
        <section className="card" style={{ marginTop: 14 }}>
          <h3 style={{ marginTop: 0 }}>Facts summary (approved)</h3>
          <p style={{ margin: "4px 0" }}>{card.summary.mr}</p>
          <p className="muted" style={{ margin: "4px 0" }}>{card.summary.en}</p>
          {card.approval && (
            <small>Approved on {card.approval.approved_at.slice(0, 10)} (from draft v{card.approval.base_card_version})</small>
          )}
        </section>
      )}

      <div className="grid2" style={{ marginTop: 14 }}>
        <section className="card">
          <h3 style={{ marginTop: 0 }}>Exhibits</h3>
          <table className="data">
            <thead><tr><th>#</th><th>Type</th><th>Stage</th><th>Pages</th><th>Date</th><th>Conf.</th></tr></thead>
            <tbody>
              {card?.exhibits.map((e) => (
                <tr key={e.exhibit_id}>
                  <td>{e.seq}</td><td>{e.type}</td><td>{e.stage}</td><td>{e.page_range}</td><td>{e.doc_date ?? "—"}</td>
                  <td>{e.confidence.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card">
          <h3 style={{ marginTop: 0 }}>Validations</h3>
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            {card?.validations.map((v) => (
              <li key={v.code}>
                <b style={{ color: STATUS_COLOUR[v.status] ?? "inherit" }}>{v.status}</b> {v.code} — {v.message}
                {v.depends_on_unverified && <em style={{ color: "var(--warn)" }}> (parameter to_verify)</em>}
              </li>
            ))}
          </ul>
          <h3>Card versions</h3>
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            {c.card_versions.map((v) => (
              <li key={v.version}>v{v.version} <span style={{ color: STATUS_COLOUR[v.status] }}>{v.status}</span> · {String(v.created_at).slice(0, 16).replace("T", " ")}</li>
            ))}
          </ul>
        </section>
      </div>

      <h2>Facts</h2>
      <div className="grid2">
        {groups.map(([group, facts]) => (
          <section key={group} className="card">
            <h3 style={{ marginTop: 0 }}>{group}</h3>
            <table className="data">
              <tbody>
                {Object.entries(facts).map(([name, f]) => (
                  <tr key={name}>
                    <td style={{ width: 150 }}>{name}</td>
                    <td><b>{isFact(f) ? fmtValue(f.value) : "—"}</b></td>
                    <td className="muted" style={{ fontSize: 12 }}>{isFact(f) ? `p.${f.page_seq} · ${f.method}` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ))}
      </div>

      <div className="grid2" style={{ marginTop: 14 }}>
        <section className="card">
          <h3 style={{ marginTop: 0 }}>Requirements {approved ? "" : "(draft)"}</h3>
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            {card?.requirements.map((r) => (
              <li key={r.code}>
                <b style={{ color: STATUS_COLOUR[r.status] ?? "inherit" }}>{r.status}</b> {r.code}
                {r.was_queried_in ? " · queried" : ""}{r.origin === "review" ? " · set in review" : ""}
              </li>
            ))}
          </ul>
        </section>
        <section className="card">
          <h3 style={{ marginTop: 0 }}>Events {approved ? "" : "(draft)"}</h3>
          <ol style={{ paddingLeft: 22, margin: 0 }}>{card?.events.map((e) => <li key={e.seq}>{e.date ?? "—"} {e.code}</li>)}</ol>
        </section>
      </div>
    </main>
  );
}
