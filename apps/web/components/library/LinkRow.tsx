"use client";
import { useState } from "react";
import { fmtDate, RELATION_TEXT, sourceHref, type LibraryLink, type LinkRelation } from "@/lib/api";
import { send } from "@/lib/client";
import Icon from "../Icon";

const STATUS_LVL = { proposed: "lvl-warning", confirmed: "lvl-ok", rejected: "lvl-unchecked" } as const;
const STATUS_TEXT = { proposed: "To confirm", confirmed: "Confirmed", rejected: "Rejected" } as const;

function cited(l: LibraryLink): string {
  const d = l.cited.date ? fmtDate(l.cited.date) : l.cited.date_printed ?? "";
  return `${d}${l.cited.number ? ` · ${l.cited.number}` : ""}`.trim() || "an undated document";
}

// One reference between documents: who says what about which GR, the words that say it (opens the page with them
// marked), and Confirm / Reject. Only confirmed links change what is in force.
export default function LinkRow({ link, onChanged, notify }: {
  link: LibraryLink; onChanged: () => void; notify: (kind: "error" | "success", text: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [relation, setRelation] = useState<LinkRelation>(link.relation);
  const fromNote = link.from?.collection === "case_note";
  const patch = async (body: Record<string, unknown>, done: string) => {
    setBusy(true);
    try {
      await send("PATCH", `/library/links/${link.id}`, body);
      notify("success", done);
      onChanged();
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const quoteHref = link.from ? sourceHref({ document_id: link.from.document_id, page_no: link.evidence.page_no, quote: link.evidence.quote }) : null;
  const target = link.to
    ? <a href={`/library/${link.to.document_id}`}><b>{link.to.key}</b> {link.to.type_label} {fmtDate(link.to.doc_date)}</a>
    : <span className="pill status lvl-warning" title="This GR is not in the library: it is on the missing-GR list">Not in the library: {cited(link)}</span>;

  return (
    <div className={`lib-link ${link.status}`}>
      <div className="lib-link-head">
        <span className={`pill status ${STATUS_LVL[link.status]}`}>{STATUS_TEXT[link.status]}</span>
        {link.from && <a href={`/library/${link.from.document_id}`}><b>{link.from.key}</b></a>}
        {link.from_label && <span className="muted">{link.from_label}</span>}
        {link.status === "proposed" && !fromNote ? (
          <select value={relation} onChange={(e) => setRelation(e.target.value as LinkRelation)} aria-label="Relation" className="small">
            {(Object.keys(RELATION_TEXT) as LinkRelation[]).map((r) => <option key={r} value={r}>{RELATION_TEXT[r]}</option>)}
          </select>
        ) : <b className="lib-rel">{RELATION_TEXT[link.relation]}</b>}
        {target}
        {link.to_label && <span className="muted">part {link.to_label}</span>}
        {link.source === "operator" && <span className="badge">added by hand</span>}
      </div>
      {link.evidence.quote && (
        <div className="lib-link-quote" lang="mr">
          “{link.evidence.quote}”
          {quoteHref && <a className="cite-chip" href={quoteHref}>p.{link.evidence.page_no ?? "?"}</a>}
        </div>
      )}
      <div className="row" style={{ gap: 6 }}>
        {link.status !== "confirmed" && (
          <button className="small primary" disabled={busy || (relation !== "cites" && !link.to)}
            title={relation !== "cites" && !link.to ? "The GR it changes is not in the library" : undefined}
            onClick={() => patch(relation !== link.relation ? { relation, status: "confirmed" } : { status: "confirmed" }, "Link confirmed.")}>
            <Icon name="check" size={13} /> Confirm
          </button>
        )}
        {link.status !== "rejected" && (
          <button className="small ghost" disabled={busy} onClick={() => patch({ status: "rejected" }, "Link rejected.")}>
            <Icon name="x" size={13} /> Reject
          </button>
        )}
        {link.status !== "proposed" && (
          <button className="small ghost" disabled={busy} onClick={() => patch({ status: "proposed" }, "Back to to-confirm.")}>Undo</button>
        )}
      </div>
    </div>
  );
}
