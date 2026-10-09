"use client";
import { useState } from "react";
import type { LibraryDocDetail, LibraryTaxonomy } from "@/lib/api";
import { send } from "@/lib/client";
import Icon from "../Icon";
import type { Notify } from "./LibraryDocApp";

const FLAG_TEXT: Record<string, string> = {
  date_differs_from_index: "The date printed on the GR differs from the compendium index",
};

// The GR's metadata (from the compendium index and the AI) with the evidence for it; edit and confirm. Confirmed
// values are never overwritten by a reload.
export default function MetaPanel({ doc, tx, onSaved, notify }: {
  doc: LibraryDocDetail; tx: LibraryTaxonomy; onSaved: () => void; notify: Notify;
}) {
  const [f, setF] = useState({
    doc_type: doc.doc_type ?? "gr", doc_date: doc.doc_date ?? "", doc_number: doc.doc_number ?? "",
    issuing_desk: doc.issuing_desk ?? "", subject_mr: doc.subject_mr ?? "", subject_en: doc.subject_en ?? "",
    topics: doc.topics, notes: doc.notes ?? "",
  });
  const [busy, setBusy] = useState(false);
  const flags = doc.proposed?.flags ?? {};
  const ungrounded = (flags.ungrounded as string[] | undefined) ?? [];
  const shown = Object.entries(flags).filter(([k, v]) => k !== "ungrounded" && v);

  const save = async (verified?: boolean) => {
    setBusy(true);
    try {
      await send("PATCH", `/library/documents/${doc.id}`, {
        ...f, doc_date: f.doc_date || null, doc_number: f.doc_number || null, issuing_desk: f.issuing_desk || null,
        subject_en: f.subject_en || null, notes: f.notes || null, ...(verified !== undefined ? { verified } : {}),
      });
      onSaved();
      notify("success", verified ? "Metadata confirmed." : verified === false ? "Marked to verify." : "Saved.");
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  return (
    <div className="lib-meta">
      {(ungrounded.length > 0 || shown.length > 0) && (
        <div className="notice">
          <Icon name="alert" />
          <span>
            {ungrounded.length > 0 && <>Not found on the pages: {ungrounded.join(", ")}. </>}
            {shown.map(([k]) => FLAG_TEXT[k] ?? k).join(". ")}
          </span>
        </div>
      )}
      <label>Type
        <select value={f.doc_type} onChange={set("doc_type")}>
          {Object.entries(tx.types).map(([k, t]) => <option key={k} value={k}>{t.en} ({t.mr})</option>)}
        </select>
      </label>
      <label>Date <input type="date" value={f.doc_date} onChange={set("doc_date")} /></label>
      <label>Number <input value={f.doc_number} onChange={set("doc_number")} lang="mr" /></label>
      <label>Issued by <input value={f.issuing_desk} onChange={set("issuing_desk")} lang="mr" /></label>
      <label>Subject (Marathi) <textarea rows={3} value={f.subject_mr} onChange={set("subject_mr")} lang="mr" /></label>
      <label>Subject (English) <textarea rows={2} value={f.subject_en} onChange={set("subject_en")} /></label>
      <fieldset>
        <legend>Topics</legend>
        <div className="fp-chips">
          {Object.entries(tx.topics).map(([k, t]) => (
            <button type="button" key={k} className={`fp-chip${f.topics.includes(k) ? " on" : ""}`} title={t.mr}
              onClick={() => setF({ ...f, topics: f.topics.includes(k) ? f.topics.filter((x) => x !== k) : [...f.topics, k] })}>{t.en}</button>
          ))}
        </div>
      </fieldset>
      <label>Notes <textarea rows={2} value={f.notes} onChange={set("notes")} /></label>
      <div className="row" style={{ gap: 8 }}>
        <button disabled={busy} onClick={() => save()}>Save</button>
        {doc.verification === "verified"
          ? <button className="ghost" disabled={busy} onClick={() => save(false)}>Mark to verify</button>
          : <button className="primary" disabled={busy} onClick={() => save(true)}><Icon name="check" /> Confirm metadata</button>}
      </div>
      <p className="muted lib-note">
        Section {doc.section}, index row {doc.section_seq}; compendium PDF pages {doc.source_pages[0]}–{doc.source_pages[1]}.
      </p>
    </div>
  );
}
