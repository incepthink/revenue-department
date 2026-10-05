"use client";
import { useCallback, useEffect, useState } from "react";
import { STATUS_COLOUR } from "@/lib/api";
import { get, send } from "@/lib/client";

type Issue = { row: number; column: string | null; message: string };
type Report = {
  ok: boolean; dry_run: boolean; rows: number; would_insert: number; would_replace: number; inserted: number; replaced: number;
  batch_id: string | null; errors: Issue[]; warnings: Issue[]; error_count: number; warning_count: number;
};
type Listing = { total: number; years: { year: number; rows: number; verified: number }[]; rows: Record<string, string | null>[] };

export default function AsrAdmin({ canImport }: { canImport: boolean }) {
  const [file, setFile] = useState<File | null>(null);
  const [replace, setReplace] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState({ year: "", village: "" });
  const [list, setList] = useState<Listing | null>(null);

  const load = useCallback(async () => {
    const q = new URLSearchParams();
    if (filter.year) q.set("year", filter.year);
    if (filter.village) q.set("village", filter.village);
    setList(await get<Listing>(`/admin/asr?${q}`));
  }, [filter]);
  useEffect(() => { load().catch((e) => setErr(e.message)); }, [load]);

  const run = async (dry: boolean) => {
    if (!file) return;
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      setReport(await send<Report>("POST", `/admin/asr/import?dry_run=${dry}&replace=${replace}`, fd));
      if (!dry) await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page">
      <h1>ASR rates (रेडी रेकनर)</h1>
      {canImport && (
        <section className="card">
          <h3 style={{ marginTop: 0 }}>Import CSV</h3>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 0 }}>
            Columns: <code>year, district, village, land_rate_per_sqm</code> (required) and <code>division, taluka, zone, sub_zone,
            cts_range, residential_rate_per_sqm, commercial_rate_per_sqm, source_ref, status</code>. UTF-8. The file is validated first;
            nothing is imported if any row has an error.
          </p>
          <div className="row">
            <input type="file" accept=".csv,text/csv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setReport(null); }} />
            <label className="inline"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> replace rows with the same key</label>
            <button disabled={!file || busy} onClick={() => run(true)}>Validate</button>
            <button className="primary" disabled={!file || busy || !report?.ok || !report.dry_run} onClick={() => run(false)}>Import</button>
          </div>
          {err && <div className="error" style={{ marginTop: 8 }}>{err}</div>}
          {report && (
            <div style={{ marginTop: 10 }}>
              <div className={report.ok ? "success" : "error"}>
                {report.dry_run
                  ? report.ok ? `Valid: ${report.rows} rows — ${report.would_insert} new, ${report.would_replace} replacing existing. Click Import.`
                    : `${report.error_count} error(s) in ${report.rows} rows — nothing imported.`
                  : report.ok ? `Imported ${report.inserted} rows (${report.replaced} replaced), batch ${report.batch_id}.` : "Import failed."}
                {report.warning_count > 0 && ` ${report.warning_count} warning(s).`}
              </div>
              {(report.errors.length > 0 || report.warnings.length > 0) && (
                <table className="data" style={{ marginTop: 8 }}>
                  <thead><tr><th>Row</th><th>Column</th><th>Problem</th><th /></tr></thead>
                  <tbody>
                    {report.errors.map((i, k) => <tr key={`e${k}`}><td>{i.row || "header"}</td><td>{i.column ?? ""}</td><td>{i.message}</td><td style={{ color: "var(--bad)" }}>error</td></tr>)}
                    {report.warnings.map((i, k) => <tr key={`w${k}`}><td>{i.row}</td><td>{i.column ?? ""}</td><td>{i.message}</td><td style={{ color: "var(--warn)" }}>warning</td></tr>)}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </section>
      )}

      <h2>Imported rates</h2>
      <div className="row" style={{ marginBottom: 8 }}>
        {list?.years.map((y) => <span key={y.year} className="badge">{y.year}: {y.rows} rows, {y.verified} verified</span>)}
        {list && !list.years.length && <span className="notice">No ASR rates imported yet — the fee engine (Phase 3) needs the pilot division&apos;s table.</span>}
      </div>
      <div className="row" style={{ marginBottom: 8 }}>
        <input placeholder="year" value={filter.year} onChange={(e) => setFilter({ ...filter, year: e.target.value })} style={{ width: 80 }} />
        <input placeholder="village" value={filter.village} onChange={(e) => setFilter({ ...filter, village: e.target.value })} />
        <span className="muted">{list?.total ?? 0} matching</span>
      </div>
      <table className="data card" style={{ padding: 0 }}>
        <thead><tr><th>Year</th><th>District</th><th>Taluka</th><th>Village</th><th>Zone</th><th>Land ₹/m²</th><th>Residential</th><th>Commercial</th><th>Source</th><th>Status</th></tr></thead>
        <tbody>
          {list?.rows.map((r) => (
            <tr key={r.id}>
              <td>{r.year}</td><td>{r.district}</td><td>{r.taluka}</td><td>{r.village}</td><td>{r.zone}{r.sub_zone ? ` / ${r.sub_zone}` : ""}</td>
              <td>{r.land_rate_per_sqm}</td><td>{r.residential_rate_per_sqm ?? "—"}</td><td>{r.commercial_rate_per_sqm ?? "—"}</td>
              <td className="muted" style={{ fontSize: 12 }}>{r.source_ref}</td>
              <td><span className="pill" style={{ color: STATUS_COLOUR[r.status ?? ""] }}>{r.status}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
