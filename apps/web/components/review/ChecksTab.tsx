"use client";
import { STATUS_COLOUR, fmtValue } from "@/lib/api";
import { useReview } from "./shared";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

export default function ChecksTab() {
  const { view } = useReview();
  const d = view.disagreements;
  return (
    <>
      {(view.blockers ?? []).length > 0 && (
        <div className="notice" style={{ marginBottom: 10 }}>
          <b>Before approval:</b>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{view.blockers!.map((b) => <li key={b}>{b}</li>)}</ul>
        </div>
      )}
      <h3 style={{ marginTop: 0 }}>Validators (on the reviewed values)</h3>
      {(view.validations ?? []).map((v) => (
        <div key={v.code} className="qitem" style={{ borderLeft: `4px solid ${STATUS_COLOUR[v.status] ?? "var(--line)"}` }}>
          <b style={{ color: STATUS_COLOUR[v.status] }}>{v.status}</b> <b>{v.code}</b>
          <div style={{ fontSize: 13 }}>{v.message}</div>
          {v.depends_on_unverified && (
            <div className="muted" style={{ fontSize: 12 }}>
              depends on unverified parameters: {(v.parameters ?? []).filter((p) => p.status !== "verified").map((p) => `${p.key}=${fmtValue(p.value)}`).join(", ")}
            </div>
          )}
        </div>
      ))}

      <h3>Reviewers</h3>
      <ul style={{ paddingLeft: 18, margin: 0 }}>
        {view.assignments.map((a) => (
          <li key={a.id}>{a.reviewer.display_name}{a.mine ? " (you)" : ""} — {a.mode}, {a.status}{a.submitted_at ? `, submitted ${String(a.submitted_at).slice(0, 16).replace("T", " ")}` : ""}</li>
        ))}
      </ul>

      {view.assignment?.mode === "double" && (
        <>
          <h3>Double-review disagreements</h3>
          {!d ? (
            <p className="muted">Shown once both reviewers have submitted.</p>
          ) : (
            <>
              <div className="row" style={{ gap: 6, marginBottom: 8 }}>
                <span className="badge">segmentation {pct(d.segmentation)}</span>
                <span className="badge">fields {pct(d.fields)}</span>
                <span className="badge">requirements {pct(d.requirements)}</span>
                <span className="badge">query mapping {pct(d.query_mapping)}</span>
                <span className="badge">events {pct(d.events)}</span>
              </div>
              {d.disagreements.length === 0 && <p className="success">Full agreement.</p>}
              <table className="data">
                <thead><tr><th>Kind</th><th>Where</th><th>You</th><th>Other reviewer</th></tr></thead>
                <tbody>
                  {d.disagreements.map((x, i) => (
                    <tr key={i}>
                      <td>{x.kind}</td>
                      <td style={{ fontSize: 12 }}>{x.exhibit ?? x.code ?? x.item ?? x.event}{x.field ? ` · ${x.field}` : ""}</td>
                      <td>{x.in ? (x.in === "a" ? "✓" : "—") : fmtValue(x.a)}</td>
                      <td>{x.in ? (x.in === "b" ? "✓" : "—") : fmtValue(x.b)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: 12 }}>Resolve by editing your own review, then approve. The agreement report keeps both submitted versions.</p>
            </>
          )}
        </>
      )}
    </>
  );
}
