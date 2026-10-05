import Icon, { type IconName } from "@/components/Icon";
import NewCase from "@/components/NewCase";
import type { CaseSummary } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

type Stage = { label: string; tone: "ok" | "info" | "warning" | "problem" | "unchecked" };

// One status per case, folding pipeline state, card status and review progress together.
function stageOf(c: CaseSummary): Stage {
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
  const approved = stages.filter((s) => s.label === "Approved").length;
  const inReview = stages.filter((s) => s.label === "In review").length;
  const open = cases.length - approved;
  const stats: { label: string; value: number; icon: IconName; tone?: string }[] = [
    { label: "Total cases", value: cases.length, icon: "folder" },
    { label: "Open", value: open, icon: "clock", tone: "warn" },
    { label: "In review", value: inReview, icon: "eye", tone: "muted" },
    { label: "Approved", value: approved, icon: "checkCircle", tone: "ok" },
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
