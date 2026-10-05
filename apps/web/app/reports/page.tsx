import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

type M = { n: number; accept: number; edit: number; not_present: number; added: number; precision: number | null; recall: number | null; accuracy: number | null };
type Accuracy = {
  cases: number; overall: M; by_field: Record<string, M>; by_exhibit_type: Record<string, M>; by_draft_method: Record<string, M>;
  segmentation_corrections: number; stage_corrections: number;
};
type Agreement = {
  first_n: number; cases: number; segmentation: number | null; fields: number | null; fields_compared: number;
  requirements_agreement: number | null; requirements_kappa: number | null; query_mapping: number | null;
  per_case: { case_ref: string; reviewers: string[]; segmentation: number | null; stage: number | null; fields: number | null; requirements: number | null; query_mapping: number | null; events: number | null; disagreement_count: number }[];
};

type FeeGolden = {
  precedents: number; checked: number; matches: number; unexplained: number; by_class: Record<string, number>;
  cases: { case_id: string; case_ref: string; observed: string | null; observed_source: string | null; computed: string | null; status: string | null; class: string; detail: string | null }[];
};
type TuneM = { precision: number; recall: number; f1: number; ndcg5: number | null };
type Tuning = {
  created_at: string; n_queries: number; data_class: string | null;
  default: { weights: Record<string, number>; support_threshold: number; metrics: TuneM };
  tuned: { weights: Record<string, number>; support_threshold: number; metrics: TuneM };
};

const p = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

async function optional<T>(path: string): Promise<T | null> {
  try {
    return await apiGet<T>(path);
  } catch {
    return null; // 404 = not run yet
  }
}

function MetricsTable({ rows, first }: { rows: Record<string, M>; first: string }) {
  return (
    <table className="data">
      <thead><tr><th>{first}</th><th>n</th><th>accept</th><th>edit</th><th>not present</th><th>added</th><th>precision</th><th>recall</th></tr></thead>
      <tbody>
        {Object.entries(rows).map(([k, m]) => (
          <tr key={k}>
            <td className="mono">{k}</td><td>{m.n}</td><td>{m.accept}</td><td>{m.edit}</td><td>{m.not_present}</td><td>{m.added}</td>
            <td style={{ color: (m.precision ?? 1) < 0.9 ? "var(--bad)" : undefined }}>{p(m.precision)}</td>
            <td style={{ color: (m.recall ?? 1) < 0.9 ? "var(--bad)" : undefined }}>{p(m.recall)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default async function ReportsPage() {
  const [acc, agr, fee, tune] = await Promise.all([
    apiGet<Accuracy>("/reports/extraction-accuracy"), apiGet<Agreement>("/reports/agreement"),
    optional<FeeGolden>("/reports/fee-golden"), optional<Tuning>("/reports/tuning"),
  ]);
  return (
    <main className="page">
      <h1>Reports</h1>
      <section className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Field-level extraction accuracy</h2>
          <a className="btn" href="/api/rci/reports/extraction_accuracy.md" target="_blank" rel="noreferrer">Markdown</a>
        </div>
        <p className="muted" style={{ fontSize: 12.5 }}>
          Machine draft vs approved card, from the correction log written at every first-round approval. precision = draft value
          correct / draft had a value; recall = draft value correct / approved card has a value.
        </p>
        {acc.overall.n === 0 ? <p className="notice">No approved cards yet.</p> : (
          <>
            <div className="row" style={{ gap: 6, marginBottom: 10 }}>
              <span className="badge">{acc.cases} case(s)</span><span className="badge">{acc.overall.n} fields compared</span>
              <span className="badge">precision {p(acc.overall.precision)}</span><span className="badge">recall {p(acc.overall.recall)}</span>
              <span className="badge">accuracy {p(acc.overall.accuracy)}</span>
              <span className="badge">{acc.segmentation_corrections} segmentation fixes</span><span className="badge">{acc.stage_corrections} stage fixes</span>
            </div>
            <h3>By draft method</h3>
            <MetricsTable rows={acc.by_draft_method} first="method" />
            <h3>By exhibit type</h3>
            <MetricsTable rows={acc.by_exhibit_type} first="exhibit type" />
            <h3>By field</h3>
            <MetricsTable rows={acc.by_field} first="field" />
          </>
        )}
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Double-review agreement (first {agr.first_n} cases)</h2>
          <a className="btn" href="/api/rci/reports/agreement.md" target="_blank" rel="noreferrer">Markdown</a>
        </div>
        {agr.cases === 0 ? <p className="notice" style={{ marginTop: 10 }}>No case has two submitted reviews yet.</p> : (
          <>
            <div className="row" style={{ gap: 6, margin: "10px 0" }}>
              <span className="badge">{agr.cases} case(s)</span><span className="badge">segmentation {p(agr.segmentation)}</span>
              <span className="badge">fields {p(agr.fields)} of {agr.fields_compared}</span>
              <span className="badge">requirements {p(agr.requirements_agreement)} (κ {agr.requirements_kappa ?? "—"})</span>
              <span className="badge">query mapping {p(agr.query_mapping)}</span>
            </div>
            <table className="data">
              <thead><tr><th>Case</th><th>Reviewers</th><th>Segmentation</th><th>Stage</th><th>Fields</th><th>Requirements</th><th>Query map</th><th>Events</th><th>Disagreements</th></tr></thead>
              <tbody>
                {agr.per_case.map((c) => (
                  <tr key={c.case_ref}>
                    <td>{c.case_ref}</td><td style={{ fontSize: 12 }}>{c.reviewers.join(", ")}</td><td>{p(c.segmentation)}</td><td>{p(c.stage)}</td>
                    <td>{p(c.fields)}</td><td>{p(c.requirements)}</td><td>{p(c.query_mapping)}</td><td>{p(c.events)}</td><td>{c.disagreement_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Fee golden check (precedent challans)</h2>
          <a className="btn" href="/api/rci/reports/fee-golden.md" target="_blank" rel="noreferrer">Markdown</a>
        </div>
        <p className="muted" style={{ fontSize: 12.5 }}>
          Every approved precedent with a premium challan (else the order / demand amount) is recomputed from the rules layer and the
          ASR table. Each difference is classified; &quot;unexplained&quot; must stay at 0.
        </p>
        {!fee ? <p className="notice">Report unavailable.</p> : (
          <>
            <div className="row" style={{ gap: 6, marginBottom: 10 }}>
              <span className="badge">{fee.checked} of {fee.precedents} precedents with an amount</span>
              <span className="badge">exact matches {fee.matches}</span>
              <span className="badge" style={{ color: fee.unexplained ? "var(--bad)" : "var(--ok)" }}>unexplained {fee.unexplained}</span>
              {Object.entries(fee.by_class).map(([k, v]) => <span key={k} className="badge">{k}: {v}</span>)}
            </div>
            <details>
              <summary className="muted">Per case</summary>
              <table className="data">
                <thead><tr><th>Case</th><th>Observed</th><th>Computed</th><th>Status</th><th>Class</th><th>Detail</th></tr></thead>
                <tbody>
                  {fee.cases.map((c) => (
                    <tr key={c.case_id}>
                      <td><a href={`/cases/${c.case_id}/analysis`}>{c.case_ref}</a></td><td>{c.observed ?? "—"} <span className="muted">({c.observed_source ?? "—"})</span></td>
                      <td>{c.computed ?? "—"}</td><td>{c.status ?? "—"}</td><td className="mono">{c.class}</td><td style={{ fontSize: 12 }}>{c.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </>
        )}
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <h2 style={{ margin: 0 }}>Leave-one-out tuning</h2>
        {!tune ? <p className="notice" style={{ marginTop: 10 }}>No tuning run saved yet — run <code>rci tune --save</code>.</p> : (
          <>
            <p className="muted" style={{ fontSize: 12.5 }}>
              {tune.n_queries} precedents, each masked to its filing stage and scored against the requirements the office actually queried
              (never holdouts). Saved {tune.created_at.slice(0, 16).replace("T", " ")}{tune.data_class ? ` · ${tune.data_class} only` : ""}.
            </p>
            <table className="data">
              <thead><tr><th>Setting</th><th>Support threshold</th><th>Precision</th><th>Recall</th><th>F1</th><th>nDCG@5</th></tr></thead>
              <tbody>
                {(["default", "tuned"] as const).map((k) => (
                  <tr key={k}>
                    <td>{k}</td><td>{tune[k].support_threshold}</td><td>{p(tune[k].metrics.precision)}</td><td>{p(tune[k].metrics.recall)}</td>
                    <td><b>{p(tune[k].metrics.f1)}</b></td><td>{p(tune[k].metrics.ndcg5)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3>Rerank weights</h3>
            <div className="row" style={{ gap: 6 }}>
              {Object.keys(tune.tuned.weights).map((f) => (
                <span key={f} className="badge">{f}: {tune.default.weights[f]} → <b>{tune.tuned.weights[f]}</b></span>
              ))}
            </div>
          </>
        )}
      </section>
    </main>
  );
}
