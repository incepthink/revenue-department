import Icon, { type IconName } from "@/components/Icon";
import NewCase from "@/components/NewCase";
import type { CaseSummary } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

type Stage = { label: string; tone: "ok" | "info" | "warning" | "problem" | "unchecked" };

const NOTING_STAGE: Record<string, Stage> = {
  uploaded: { label: "Examining pages", tone: "unchecked" }, segmenting: { label: "Examining pages", tone: "unchecked" },
  awaiting_range: { label: "Confirm report pages", tone: "warning" }, reading: { label: "Reading the letter", tone: "unchecked" },
  extracting: { label: "Reading the facts", tone: "unchecked" }, awaiting_review: { label: "Review the facts", tone: "warning" },
  generating: { label: "Writing the Noting", tone: "unchecked" }, done: { label: "Noting ready", tone: "ok" },
  failed: { label: "Noting failed", tone: "problem" },
};

// One status per case, folding pipeline state, card status and review progress together.
function stageOf(c: CaseSummary): Stage {
  if (c.kind === "noting") return NOTING_STAGE[c.noting_stage ?? "uploaded"] ?? { label: "Noting", tone: "info" };
  if (c.card_status === "approved" || c.status === "approved") return { label: "Approved", tone: "ok" };
  if (c.status === "failed") return { label: "Processing failed", tone: "problem" };
  if (c.review?.length) return { label: "In review", tone: "info" };
  if (c.card_version) return { label: "Ready for review", tone: "warning" };
  return { label: "Processing", tone: "unchecked" };
}

function fmtDate(d: string | null): string {
  if (!d) return "—";
  const t = new Date(d);
  return isNaN(t.getTime()) ? d : t.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export default async function CasesPage() {
  let cases: CaseSummary[] = [];
  let error: string | null = null;
  try {
    // SYN-* cases are the synthetic backtest corpus; keep them off the operator's case list.
    cases = (await apiGet<CaseSummary[]>("/cases")).filter((c) => !c.case_ref.startsWith("SYN"));
  } catch (e) {
    error = String(e);
  }
  const stages = cases.map(stageOf);
  const notings = cases.filter((c) => c.kind === "noting").length;
  const approved = stages.filter((s) => s.label === "Approved").length;
  const inReview = stages.filter((s) => s.label === "In review").length;
  const open = cases.length - notings - approved;
  const stats: { label: string; value: number; icon: IconName; tone?: string }[] = [
    { label: "Total cases", value: cases.length - notings, icon: "folder" },
    { label: "Open", value: open, icon: "clock", tone: "warn" },
    { label: "In review", value: inReview, icon: "eye", tone: "muted" },
    { label: "Approved", value: approved, icon: "checkCircle", tone: "ok" },
    { label: "Notings", value: notings, icon: "edit", tone: "muted" },
  ];
  return (
    <main className="page">
      <NewCase>
        <div>
          <h1>Cases</h1>
          <div className="sub">Upload a case&apos;s documents to check them against the rules in force, then review the extracted text.</div>
        </div>
      </NewCase>
      {error && (
        <p className="error"><Icon name="alert" /> <span>API unavailable ({error}). Start it with <code>make api</code>.</span></p>
      )}
      <div className="stats">
        {stats.map((s) => (
          <div key={s.label} className="card stat">
            <span className={`stat-icon${s.tone ? ` ${s.tone}` : ""}`}><Icon name={s.icon} size={19} /></span>
            <div>
              <div className="stat-value">{s.value}</div>
              <div className="stat-label">{s.label}</div>
            </div>
          </div>
        ))}
      </div>
      <div className="card table-card cases-table">
        <div className="scroll">
          <table className="data">
            <thead>
              <tr><th>Case</th><th>Status</th><th>Filed</th><th>Decided</th><th className="actions-col"><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {cases.map((c, i) => {
                const s = stages[i];
                if (c.kind === "noting") {
                  return (
                    <tr key={c.id}>
                      <td>
                        <div className="case-ref">
                          <span className="case-avatar"><Icon name="edit" size={16} /></span>
                          <a href={`/notings/${c.id}`}>{c.title ?? c.case_ref}</a>
                          <span className="pill">Noting</span>
                        </div>
                      </td>
                      <td><span className={`pill status lvl-${s.tone}`}>{s.label}</span></td>
                      <td className="date">{fmtDate(c.filed_on)}</td>
                      <td className="date">—</td>
                      <td className="actions-col">
                        <a className="btn small" href={`/notings/${c.id}`}><Icon name="edit" size={14} /> Noting</a>
                        <a className="btn small" href={`/cases/${c.id}/documents`}><Icon name="eye" size={14} /> Pages</a>
                      </td>
                    </tr>
                  );
                }
                return (
                  <tr key={c.id}>
                    <td>
                      <div className="case-ref">
                        <span className="case-avatar"><Icon name="folder" size={16} /></span>
                        <a href={`/cases/${c.id}/intake`}>{c.case_ref}</a>
                      </div>
                    </td>
                    <td><span className={`pill status lvl-${s.tone}`}>{s.label}</span></td>
                    <td className="date">{fmtDate(c.filed_on)}</td>
                    <td className="date">{fmtDate(c.decided_on)}</td>
                    <td className="actions-col">
                      <a className="btn small" href={`/cases/${c.id}/intake`}><Icon name="clipboard" size={14} /> Document check</a>
                      <a className="btn small" href={`/cases/${c.id}/documents`}><Icon name="eye" size={14} /> Documents</a>
                    </td>
                  </tr>
                );
              })}
              {!cases.length && !error && (
                <tr>
                  <td colSpan={5} className="empty">
                    <div className="empty-state">
                      <span className="empty-icon"><Icon name="inbox" size={22} /></span>
                      <b>No cases yet</b>
                      <span>Create a new case to upload its documents and run the document check.</span>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}
