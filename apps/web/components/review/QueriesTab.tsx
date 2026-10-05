"use client";
import { useMemo, useState } from "react";
import type { QueryItem } from "@/lib/api";
import { send } from "@/lib/client";
import { useReview } from "./shared";

type Code = { code: string; name_en: string; name_mr: string; category: string; status: string };

export default function QueriesTab() {
  const { view } = useReview();
  const queries = view.queries ?? [];
  const [newFor, setNewFor] = useState<{ exhibit_id: string; item: QueryItem } | null>(null);
  if (!queries.length) return <p className="muted">No query letters in this docket (or none recognised — check the exhibit types).</p>;
  const exSeq = (id: string) => view.exhibits!.find((e) => e.exhibit_id === id);
  return (
    <>
      <p className="muted" style={{ marginTop: 0, fontSize: 12.5 }}>
        Map every query item to the requirement code(s) it asks for, or mark it “not a requirement”. Mappings decide which
        requirements count as <i>queried</i> and feed the gap analysis.
      </p>
      {queries.map((q) => {
        const ex = exSeq(q.exhibit_id);
        return (
          <section key={q.exhibit_id} style={{ marginBottom: 14 }}>
            <h3 style={{ margin: "4px 0 8px" }}>
              Query letter {ex ? `#${ex.seq} (p.${ex.start}–${ex.end})` : ""} <span className="muted" style={{ fontWeight: 400 }}>
                {q.letter_date ?? "undated"}{q.ref_no ? ` · ${q.ref_no}` : ""}</span>
            </h3>
            {q.items.length === 0 && <p className="muted">No items extracted — add them in the Fields tab (query_items).</p>}
            {q.items.map((it) => <ItemMapper key={it.index} exhibitId={q.exhibit_id} item={it} onNew={() => setNewFor({ exhibit_id: q.exhibit_id, item: it })} />)}
          </section>
        );
      })}
      {newFor && <NewCodeModal target={newFor} onClose={() => setNewFor(null)} />}
    </>
  );
}

function ItemMapper({ exhibitId, item, onNew }: { exhibitId: string; item: QueryItem; onNew: () => void }) {
  const { view, run, readOnly, busy, jump } = useReview();
  const catalog = view.catalogs!.requirements as Code[];
  const byCode = useMemo(() => Object.fromEntries(catalog.map((c) => [c.code, c])), [catalog]);
  const codes = item.mapping?.codes ?? [];
  const notReq = item.mapping?.not_requirement ?? false;
  const save = (next: string[], nr = false) =>
    run([{ op: "query.map", exhibit_id: exhibitId, index: item.index, codes: next, not_requirement: nr }]);
  const suggestions = item.suggested_codes.filter((c) => !codes.includes(c));
  return (
    <div className={`qitem ${item.mapping ? "mapped" : "unmapped"}`}>
      <div style={{ fontSize: 13.5 }}>
        <b>{item.index + 1}.</b> {item.value ?? item.raw}{" "}
        <a className="muted" style={{ fontSize: 11.5, cursor: "pointer" }} onClick={() => jump(item.page_seq, item.raw)}>p.{item.page_seq}</a>
      </div>
      <div className="row" style={{ marginTop: 6, gap: 4 }}>
        {notReq && <span className="chip" style={{ background: "var(--panel-2)", color: "var(--muted)", borderColor: "var(--line)" }}>not a requirement
          {!readOnly && <button onClick={() => save([], false)} aria-label="clear">✕</button>}</span>}
        {codes.map((c) => (
          <span key={c} className="chip" title={byCode[c]?.name_en}>
            {c}{byCode[c]?.status === "to_verify" ? "*" : ""}
            {!readOnly && <button disabled={busy} onClick={() => save(codes.filter((x) => x !== c))} aria-label={`remove ${c}`}>✕</button>}
          </span>
        ))}
        {!item.mapping && <span className="muted" style={{ fontSize: 12 }}>unmapped</span>}
      </div>
      {!readOnly && (
        <>
          {suggestions.length > 0 && !notReq && (
            <div className="row" style={{ marginTop: 6, gap: 4 }}>
              <span className="muted" style={{ fontSize: 12 }}>suggested:</span>
              {suggestions.map((c) => (
                <span key={c} className="chip suggest" role="button" tabIndex={0} title={byCode[c]?.name_mr}
                  onClick={() => save([...codes, c])} onKeyDown={(e) => e.key === "Enter" && save([...codes, c])}>+ {c}</span>
              ))}
            </div>
          )}
          <div className="row" style={{ marginTop: 6, gap: 4 }}>
            <TypeAhead catalog={catalog} exclude={codes} onPick={(c) => save([...codes, c])} disabled={busy || notReq} />
            <button className="small" disabled={busy || notReq} onClick={() => save([], true)}>Not a requirement</button>
            <button className="small" disabled={busy} onClick={onNew}>+ New code</button>
          </div>
        </>
      )}
    </div>
  );
}

function TypeAhead({ catalog, exclude, onPick, disabled }: { catalog: Code[]; exclude: string[]; onPick: (c: string) => void; disabled: boolean }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const k = q.trim().toLowerCase();
  const matches = catalog
    .filter((c) => !exclude.includes(c.code))
    .filter((c) => !k || c.code.toLowerCase().includes(k) || c.name_en.toLowerCase().includes(k) || c.name_mr.includes(q.trim()))
    .slice(0, 12);
  const pick = (c: string) => {
    onPick(c);
    setQ("");
    setOpen(false);
  };
  return (
    <div className="typeahead" style={{ flex: 1, minWidth: 180 }}>
      <input value={q} disabled={disabled} placeholder="add code — type code, English or मराठी" style={{ width: "100%" }}
        onChange={(e) => { setQ(e.target.value); setOpen(true); setHi(0); }}
        onFocus={() => setOpen(true)} onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setHi(Math.min(hi + 1, matches.length - 1));
          if (e.key === "ArrowUp") setHi(Math.max(hi - 1, 0));
          if (e.key === "Enter" && matches[hi]) { e.preventDefault(); pick(matches[hi].code); }
          if (e.key === "Escape") setOpen(false);
        }} />
      {open && matches.length > 0 && (
        <div className="menu">
          {matches.map((c, i) => (
            <div key={c.code} className={i === hi ? "on" : ""} onMouseDown={() => pick(c.code)}>
              <b>{c.code}</b> <span className="muted">{c.name_mr} · {c.name_en}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function NewCodeModal({ target, onClose }: { target: { exhibit_id: string; item: QueryItem }; onClose: () => void }) {
  const { run, notify, reload } = useReview();
  const [code, setCode] = useState("");
  const [nameEn, setNameEn] = useState("");
  const [nameMr, setNameMr] = useState("");
  const [category, setCategory] = useState("applicant_document");
  const [err, setErr] = useState<string | null>(null);
  const existing = target.item.mapping?.codes ?? [];
  const create = async () => {
    setErr(null);
    try {
      const c = await send<{ code: string }>("POST", "/catalog/requirements", {
        code, name_en: nameEn, name_mr: nameMr, category, query_keywords: [],
      });
      await reload(); // the catalog in the view now contains the new code
      const ok = await run([{ op: "query.map", exhibit_id: target.exhibit_id, index: target.item.index, codes: [...existing, c.code] }]);
      if (ok) notify("success", `Created ${c.code} (to_verify) and mapped the item`);
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>New requirement code</h2>
        <p className="muted" style={{ fontSize: 12.5 }}>“{target.item.value}”</p>
        <div className="stack">
          <label>Code (UPPER_SNAKE_CASE)</label>
          <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))} placeholder="ENCROACHMENT_CLARIFICATION" />
          <label>Name (English)</label>
          <input value={nameEn} onChange={(e) => setNameEn(e.target.value)} />
          <label>नाव (मराठी)</label>
          <input value={nameMr} onChange={(e) => setNameMr(e.target.value)} />
          <label>Category</label>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {["applicant_document", "office_process", "payment", "legal_condition", "other"].map((c) => <option key={c}>{c}</option>)}
          </select>
          <p className="muted" style={{ fontSize: 12 }}>New codes start as <b>to_verify</b>; an admin confirms them against the rules.</p>
          {err && <div className="error">{err}</div>}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button onClick={onClose}>Cancel</button>
            <button className="primary" disabled={code.length < 3 || !nameEn || !nameMr} onClick={create}>Create and map</button>
          </div>
        </div>
      </div>
    </div>
  );
}
