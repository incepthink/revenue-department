"use client";
import { useState } from "react";
import { send } from "@/lib/client";
import Icon from "../Icon";

// A new rule set starts empty; its rules are added on its page.
export default function NewRuleSet() {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", code: "", notification_ref: "", notification_date: "", effective_from: "", effective_to: "" });
  const [err, setErr] = useState<string | null>(null);
  if (!open) return <button className="primary" onClick={() => setOpen(true)}><Icon name="plus" /> New rule set</button>;
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v });
  const create = async () => {
    setErr(null);
    try {
      const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== ""));
      const rs = await send<{ id: string }>("POST", "/admin/rule-sets", body);
      window.location.href = `/admin/rules/${rs.id}`;
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div className="modal-bg" onClick={() => setOpen(false)}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="new-rule-set" onClick={(e) => e.stopPropagation()}>
        <h2 id="new-rule-set">New rule set</h2>
        <p className="muted" style={{ fontSize: 13, margin: "0 0 18px" }}>For a notification or circular that changes the rules, e.g. a 2026 amendment.</p>
        <div className="form-grid">
          <div className="full">
            <label className="field-label" htmlFor="rs-name">Name *</label>
            <input id="rs-name" autoFocus value={f.name} onChange={(e) => set("name", e.target.value)} />
          </div>
          <div>
            <label className="field-label" htmlFor="rs-code">Short code *</label>
            <input id="rs-code" className="mono" placeholder="e.g. AMEND_2026_01" value={f.code} onChange={(e) => set("code", e.target.value.toUpperCase().replace(/\s+/g, "_"))} />
          </div>
          <div>
            <label className="field-label" htmlFor="rs-ref">Notification reference</label>
            <input id="rs-ref" value={f.notification_ref} onChange={(e) => set("notification_ref", e.target.value)} />
          </div>
          <div className="full">
            <label className="field-label" htmlFor="rs-date">Notification date</label>
            <input id="rs-date" type="date" value={f.notification_date} onChange={(e) => set("notification_date", e.target.value)} />
          </div>
          <div>
            <label className="field-label" htmlFor="rs-from">In force from *</label>
            <input id="rs-from" type="date" value={f.effective_from} onChange={(e) => set("effective_from", e.target.value)} />
          </div>
          <div>
            <label className="field-label" htmlFor="rs-to">In force until <span className="muted" style={{ fontWeight: 400 }}>(blank = open)</span></label>
            <input id="rs-to" type="date" value={f.effective_to} onChange={(e) => set("effective_to", e.target.value)} />
          </div>
        </div>
        {err && <div className="error" style={{ marginTop: 14 }}><Icon name="alert" /> <span>{err}</span></div>}
        <div className="modal-foot">
          <button onClick={() => setOpen(false)}>Cancel</button>
          <button className="primary" disabled={!f.code || !f.name || !f.effective_from} onClick={create}>Create rule set</button>
        </div>
      </div>
    </div>
  );
}
