import Icon from "@/components/Icon";
import NewRuleSet from "@/components/admin/NewRuleSet";
import { STATUS_COLOUR } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

type RuleSetRow = {
  id: string; code: string; name: string; effective_from: string; effective_to: string | null; status: string; has_source: boolean;
  counts: Record<string, { total: number; verified: number }>;
};

function Verified({ c }: { c: { total: number; verified: number } }) {
  const pct = c.total ? Math.round((c.verified / c.total) * 100) : 0;
  return (
    <div className="verify-meter" title={`${c.verified} of ${c.total} verified`}>
      <span className="num">{c.total}</span>
      <span className="bar"><span style={{ width: `${pct}%` }} /></span>
      <span className="muted">{c.verified} verified</span>
    </div>
  );
}

export default async function RulesPage() {
  const sets = await apiGet<RuleSetRow[]>("/admin/rule-sets");
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Rules</h1>
          <div className="sub">
            The rule sets in force on a case&apos;s application date decide which documents it must include and what the AI checks
            inside each one. Add a rule set when a new notification or circular changes them; its rules add to (or, with the same
            code, replace) those of earlier sets.
          </div>
        </div>
        <NewRuleSet />
      </div>
      <div className="card table-card">
        <div className="scroll">
          <table className="data">
            <thead>
              <tr><th>Rule set</th><th>In force</th><th>Notification</th><th>Documents</th><th>Document checks</th><th>Obligations</th><th>Status</th></tr>
            </thead>
            <tbody>
              {sets.map((r) => (
                <tr key={r.id}>
                  <td>
                    <div className="rule-code">
                      <span className="case-avatar"><Icon name="book" size={16} /></span>
                      <div>
                        <a href={`/admin/rules/${r.id}`}>{r.code}</a>
                        <div className="muted">{r.name}</div>
                      </div>
                    </div>
                  </td>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>{r.effective_from} → {r.effective_to ?? "open"}</td>
                  <td>
                    {r.has_source
                      ? <span className="pill status lvl-ok">Uploaded</span>
                      : <span className="pill status lvl-warning">Missing</span>}
                  </td>
                  {["requirements", "document_checks", "obligations"].map((k) => <td key={k}><Verified c={r.counts[k]} /></td>)}
                  <td><span className="pill status" style={{ color: STATUS_COLOUR[r.status] }}>{r.status}</span></td>
                </tr>
              ))}
              {!sets.length && (
                <tr>
                  <td colSpan={7}>
                    <div className="empty-state">
                      <span className="empty-icon"><Icon name="book" size={22} /></span>
                      <b>No rule sets</b>
                      <span>Add a rule set for the notification or circular that sets the rules.</span>
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
