"use client";
import { STATUS_COLOUR, type ReqRow } from "@/lib/api";
import { useReview } from "./shared";

const STATUSES = ["present", "missing", "invalid", "queried", "waived", "not_applicable"];

// Requirements matrix: every catalog requirement with its derived status (from exhibits, validators and the
// query mapping) and the reviewer's override.
export default function RequirementsTab() {
  const { view, run, readOnly, busy } = useReview();
  const reqs = view.requirements ?? [];
  const exhibits = view.exhibits!;
  const exLabel = (id: string | null) => {
    const e = exhibits.find((x) => x.exhibit_id === id);
    return e ? `#${e.seq} ${e.type}` : "—";
  };
  const set = (r: ReqRow, patch: Partial<{ status: string | null; satisfied_by_exhibit_id: string | null; notes: string | null }>) => {
    const base = r.override ?? { status: null, satisfied_by_exhibit_id: null, notes: null };
    return run([{ op: "requirement.set", code: r.code, ...base, ...patch }]);
  };
  const counts = STATUSES.map((s) => [s, reqs.filter((r) => r.status === s).length] as const).filter(([, n]) => n);
  return (
    <>
      <div className="row" style={{ marginBottom: 8, gap: 6 }}>
        {counts.map(([s, n]) => <span key={s} className="pill" style={{ color: STATUS_COLOUR[s] }}>{n} {s}</span>)}
      </div>
      <table className="data">
        <thead><tr><th>Requirement</th><th>Status</th><th>Satisfied by / queried in</th></tr></thead>
        <tbody>
          {reqs.map((r) => (
            <tr key={r.code}>
              <td style={{ width: "42%" }}>
                <b style={{ fontSize: 12.5 }}>{r.code}</b>{r.catalog_status === "to_verify" && <span className="muted" title="catalog row to_verify">*</span>}
                <div className="muted" style={{ fontSize: 12 }}>{r.name_mr}</div>
                {r.origin === "review" && <span className="badge">override</span>}
              </td>
              <td>
                {readOnly ? (
                  <b style={{ color: STATUS_COLOUR[r.status] }}>{r.status}</b>
                ) : (
                  <select value={r.override?.status ?? ""} disabled={busy} aria-label={`${r.code} status`}
                    style={{ color: STATUS_COLOUR[r.status], fontWeight: 600 }}
                    onChange={(e) => (e.target.value === "" && !r.override?.notes && !r.override?.satisfied_by_exhibit_id
                      ? run([{ op: "requirement.set", code: r.code, reset: true }])
                      : set(r, { status: e.target.value || null }))}>
                    <option value="">auto: {r.derived_status}</option>
                    {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                )}
                {!readOnly && (
                  <input defaultValue={r.override?.notes ?? ""} placeholder="note" key={`${r.code}:${r.override?.notes ?? ""}`}
                    style={{ width: "100%", marginTop: 4, fontSize: 12 }}
                    onBlur={(e) => { if ((e.target.value || null) !== (r.override?.notes ?? null)) set(r, { notes: e.target.value || null }); }} />
                )}
                {readOnly && r.notes && <div className="muted" style={{ fontSize: 12 }}>{r.notes}</div>}
              </td>
              <td style={{ fontSize: 12 }}>
                {readOnly ? exLabel(r.satisfied_by_exhibit_id) : (
                  <select value={r.override?.satisfied_by_exhibit_id ?? ""} disabled={busy} aria-label={`${r.code} satisfied by`}
                    onChange={(e) => set(r, { satisfied_by_exhibit_id: e.target.value || null })} style={{ maxWidth: 170 }}>
                    <option value="">auto: {exLabel(r.satisfied_by_exhibit_id)}</option>
                    {exhibits.map((e) => <option key={e.exhibit_id} value={e.exhibit_id}>#{e.seq} {e.type}</option>)}
                  </select>
                )}
                {r.was_queried_in && <div className="muted">queried in {exLabel(r.was_queried_in)}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 12 }}>* catalog entry not yet verified against the rules.</p>
    </>
  );
}
