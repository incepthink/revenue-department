"use client";
import { useEffect, useState } from "react";
import { useReview } from "./shared";

type Row = { code: string; date: string; exhibit_id: string; page_seq: number | "" };

// Events timeline editor: starts from the events derived from exhibits; saving replaces them with the edited list.
export default function EventsTab() {
  const { view, run, readOnly, busy, jump } = useReview();
  const exhibits = view.exhibits!;
  const catalog = view.catalogs!.events;
  const fromView = (): Row[] => (view.events ?? []).map((e) => ({
    code: e.code, date: e.date ?? "", exhibit_id: e.exhibit_id, page_seq: e.evidence?.page_seq ?? "",
  }));
  const [rows, setRows] = useState<Row[]>(fromView);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    setRows(fromView());
    setDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.events]);

  const upd = (i: number, patch: Partial<Row>) => {
    setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    setDirty(true);
  };
  const exOf = (id: string) => exhibits.find((e) => e.exhibit_id === id);
  const save = () => run([{ op: "events.set", events: rows.map((r) => ({ ...r, date: r.date || null, page_seq: r.page_seq || null })) }]);

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <span className="muted" style={{ fontSize: 12.5 }}>
          {view.events_overridden ? "Edited timeline" : "Derived from exhibits (edit to override)"} · sorted by date on save
        </span>
        {!readOnly && view.events_overridden && (
          <button className="small" disabled={busy} onClick={() => run([{ op: "events.set", reset: true }])}>Reset to derived</button>
        )}
      </div>
      <table className="data">
        <thead><tr><th>#</th><th>Event</th><th>Date</th><th>Recorded in</th>{!readOnly && <th />}</tr></thead>
        <tbody>
          {rows.map((r, i) => {
            const ex = exOf(r.exhibit_id);
            return (
              <tr key={i}>
                <td>{i + 1}</td>
                <td>
                  {readOnly ? <b style={{ fontSize: 12.5 }}>{r.code}</b> : (
                    <select value={r.code} onChange={(e) => upd(i, { code: e.target.value })} aria-label="event">
                      {catalog.map((c) => <option key={c.code} value={c.code}>{c.code} — {c.name_mr}</option>)}
                    </select>
                  )}
                </td>
                <td>{readOnly ? r.date || "—" : <input type="date" value={r.date} onChange={(e) => upd(i, { date: e.target.value })} />}</td>
                <td style={{ fontSize: 12 }}>
                  {readOnly ? (ex ? `#${ex.seq} ${ex.type}` : "—") : (
                    <select value={r.exhibit_id} onChange={(e) => upd(i, { exhibit_id: e.target.value, page_seq: exOf(e.target.value)?.start ?? "" })}
                      aria-label="exhibit" style={{ maxWidth: 150 }}>
                      <option value="">— exhibit —</option>
                      {exhibits.map((e) => <option key={e.exhibit_id} value={e.exhibit_id}>#{e.seq} {e.type}</option>)}
                    </select>
                  )}
                  {r.page_seq !== "" && <> <a style={{ cursor: "pointer" }} onClick={() => jump(Number(r.page_seq))}>p.{r.page_seq}</a></>}
                </td>
                {!readOnly && (
                  <td><button className="small icon" onClick={() => { setRows(rows.filter((_, j) => j !== i)); setDirty(true); }} aria-label="remove">✕</button></td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      {!readOnly && (
        <div className="row" style={{ marginTop: 8 }}>
          <button className="small" onClick={() => { setRows([...rows, { code: catalog[0]?.code ?? "", date: "", exhibit_id: "", page_seq: "" }]); setDirty(true); }}>+ event</button>
          <span style={{ flex: 1 }} />
          {dirty && <button className="small" onClick={() => { setRows(fromView()); setDirty(false); }}>Discard</button>}
          <button className="small primary" disabled={!dirty || busy || rows.some((r) => !r.exhibit_id)} onClick={save}>Save timeline</button>
        </div>
      )}
    </>
  );
}
