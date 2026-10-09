"use client";
import { useState } from "react";
import { fmtDate, GR_STATUS, sourceHref, type AppliedGr, type Grounded, type PrecedentView } from "@/lib/api";
import { send } from "@/lib/client";
import Icon from "../Icon";

const FACT_TEXT: Record<string, string> = {
  society_name: "Society", registration_no: "Registration no.", village: "Village", taluka: "Taluka", district: "District",
  survey_no: "Survey no.", plot_no: "Plot no.", cts_no: "CTS no.", area_sqm: "Area (sq m)", land_use: "Land use",
  original_grantee: "Original grantee", grant_date: "Grant date", grant_order: "Grant order", grant_terms: "Grant terms",
  hh1_agreement_date: "HH-1 agreement", transfer_to_society: "How the society got it", breach: "Breach",
  application_date: "Application", tenure: "Holding",
};
const TENURE: Record<string, string> = { occupancy_class_2: "Occupancy Class-II", govt_lease: "Government lease",
  occupancy_class_1: "Occupancy Class-I", unknown: "not stated" };
const OUTCOME: Record<string, string> = { approved: "Approved", rejected: "Rejected", partly: "Partly approved", proposed: "Proposed (decision not recorded)" };

/** A value read from the note, with a chip to the words it was read from (or a warning when they were not found). */
export function Evidence({ g, docId }: { g: Grounded; docId: string }) {
  const href = g.page_no ? sourceHref({ document_id: docId, page_no: g.page_no, quote: g.anchor?.quote ?? g.quote }) : null;
  return (
    <>
      {href && <a className="cite-chip" href={href} title={g.quote}>p.{g.page_no}</a>}
      {g.grounded === false && <span className="pill status lvl-warning" title={g.quote}>not found as read</span>}
    </>
  );
}

export function AppliedGrs({ grs }: { grs: AppliedGr[] }) {
  if (!grs.length) return <p className="muted">No GR citations read from this note yet.</p>;
  return (
    <ul className="lib-applied">
      {grs.map((g) => (
        <li key={g.link_id}>
          {g.document_id ? <a href={`/library/${g.document_id}`}><b>{g.doc_key}</b> {g.label}</a> : <b>{g.label}</b>}
          {g.number && <span className="muted"> {g.number}</span>}{" "}
          <span className={`pill status ${GR_STATUS[g.status].lvl}`}>{GR_STATUS[g.status].text}</span>
          {g.quote && g.page_no && (
            <a className="cite-chip" href={sourceHref({ document_id: g.note_document_id, page_no: g.page_no, quote: g.quote }) ?? undefined}
              title={g.quote}>note p.{g.page_no}</a>
          )}
        </li>
      ))}
    </ul>
  );
}

// The accepted case read from the note: land facts, what was proposed and decided, the amounts charged and the GRs
// applied — every value linked to its page. Confirm it once checked.
export default function PrecedentPanel({ p, onSaved, notify }: {
  p: PrecedentView; onSaved: () => void; notify: (kind: "error" | "success", text: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const facts = Object.entries(p.facts).filter(([k]) => !["proposal", "decision"].includes(k));
  const prop = p.facts.proposal, dec = p.facts.decision;
  const verify = async (verified: boolean) => {
    setBusy(true);
    try {
      await send("PATCH", `/library/precedents/${p.id}`, { verified });
      notify("success", verified ? "Precedent confirmed." : "Marked to verify.");
      onSaved();
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="lib-precedent">
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <b>Accepted case</b>
        <span className={`pill status ${p.outcome === "approved" ? "lvl-ok" : "lvl-info"}`}>{OUTCOME[p.outcome ?? "proposed"] ?? p.outcome}</span>
        {p.decided_on && <span className="muted">decided {fmtDate(p.decided_on)}</span>}
        {p.status === "verified" ? <span className="pill status lvl-ok">Confirmed</span> : <span className="pill status lvl-unchecked">To verify</span>}
      </div>
      <p className="lib-statement">{p.statement.en}</p>
      {p.summary.en && <p className="muted">{p.summary.en}</p>}
      {(p.flags.ungrounded?.length ?? 0) > 0 && (
        <div className="notice"><Icon name="alert" /> <span>Not found in the note as read: {p.flags.ungrounded!.join(", ")}. Check these against the page.</span></div>
      )}
      <h4>The land</h4>
      <table className="data compact">
        <tbody>
          {facts.map(([k, f]) => (
            <tr key={k}>
              <th>{FACT_TEXT[k] ?? k}</th>
              <td lang="mr">{k === "tenure" ? TENURE[f.value ?? "unknown"] ?? f.value : f.value} <Evidence g={f} docId={p.document_id} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {(prop?.summary || dec?.summary) && <h4>Proposed and decided</h4>}
      {prop?.summary && <p><b>{prop.by || "Proposal"}</b>{prop.date ? ` (${fmtDate(prop.date)})` : ""}: {prop.summary} <Evidence g={prop} docId={p.document_id} /></p>}
      {dec?.summary && <p><b>{dec.by || "Decision"}</b>{dec.date ? ` (${fmtDate(dec.date)})` : ""}: {dec.summary} <Evidence g={dec} docId={p.document_id} /></p>}
      {p.amounts.length > 0 && (
        <>
          <h4>Amounts</h4>
          <table className="data compact">
            <tbody>
              {p.amounts.map((a, i) => (
                <tr key={i}>
                  <th>{a.label ?? a.kind}</th>
                  <td>{a.amount_printed}{a.rate_pct ? ` · ${a.rate_pct} %` : ""}{a.basis ? <span className="muted"> · {a.basis}</span> : null} <Evidence g={a} docId={p.document_id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {p.conditions.length > 0 && (
        <>
          <h4>Conditions</h4>
          <ul>{p.conditions.map((c, i) => <li key={i}>{c.text} <Evidence g={c} docId={p.document_id} /></li>)}</ul>
        </>
      )}
      <h4>GRs applied <span className="muted">(status today)</span></h4>
      <AppliedGrs grs={p.applied_grs} />
      <div className="row" style={{ gap: 8, marginTop: 10 }}>
        {p.status === "verified"
          ? <button className="ghost" disabled={busy} onClick={() => verify(false)}>Mark to verify</button>
          : <button className="primary" disabled={busy} onClick={() => verify(true)}><Icon name="check" /> Confirm precedent</button>}
      </div>
    </div>
  );
}
