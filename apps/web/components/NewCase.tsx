"use client";
import { useRef, useState, type DragEvent, type FormEvent, type ReactNode } from "react";
import { send } from "@/lib/client";
import Icon from "./Icon";
import NewNoting from "./NewNoting";

const EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".zip"];
const ACCEPT = EXTENSIONS.join(",");

// What a case is usually filed with — a hint only: the intake identifies every file itself (services/api/src/rci/intake/identify.py).
const EXPECTED = [
  "7/12 extract (Satbara utara)", "Property card", "Original grant document or allotment letter",
  "Written application addressed to the District Collector", "Identity proof — Aadhaar card", "Latest property tax receipts",
  "Index II of the last registered transaction",
];

const key = (f: File) => `${f.name}|${f.size}|${f.lastModified}`;
const size = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
const supported = (f: File) => EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext));

// Header row (heading + "+ New case" button); the form opens below it. Every file of the case goes into one drop
// zone, uploaded with the slot "auto": the intake works out what each file is (a standard filing document or an
// additional one), checks it, then checks the documents against each other. Creates the case, uploads, starts the
// intake and opens the progress page, which turns into the pages with the findings drawn on them when the check is done.
export default function NewCase({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [step, setStep] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const busy = step !== null;

  function add(list: FileList | null) {
    const picked = Array.from(list ?? []);
    const rejected = picked.filter((f) => !supported(f)).map((f) => f.name);
    setMsg(rejected.length ? { kind: "error", text: `Not added (only PDF, image or ZIP files): ${rejected.join(", ")}` } : null);
    setFiles((cur) => {
      const seen = new Set(cur.map(key));
      return [...cur, ...picked.filter((f) => supported(f) && !seen.has(key(f)))];
    });
  }

  function drop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (!busy) add(e.dataTransfer.files);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (!files.length) {
      setMsg({ kind: "error", text: "Add at least one document." });
      return;
    }
    try {
      setStep("Creating the case…");
      const c = await send<{ id: string }>("POST", "/cases", { case_ref: name.trim(), title: name.trim(), data_class: "real" });
      const fd = new FormData();
      files.forEach((f) => {
        fd.append("files", f);
        fd.append("slots", "auto");
      });
      setStep(`Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`);
      await send("POST", `/cases/${c.id}/documents?process=false`, fd);
      await send("POST", `/cases/${c.id}/intake`);
      window.location.href = `/cases/${c.id}/intake?new=1`;
    } catch (err) {
      setMsg({ kind: "error", text: String((err as Error).message) });
      setStep(null);
    }
  }

  return (
    <>
      <div className="page-head">
        {children}
        <div className="row">
          <NewNoting />
          {!open && <button className="primary" onClick={() => setOpen(true)}><Icon name="plus" /> New case</button>}
        </div>
      </div>
      {open && (
        <form onSubmit={submit} className="card stack new-case" style={{ gap: 16 }}>
          <div className="new-case-head">
            <div>
              <h3>New case</h3>
              <span className="muted">Every file is identified and checked automatically.</span>
            </div>
            <button type="button" className="ghost icon" aria-label="Close" onClick={() => setOpen(false)} disabled={busy}><Icon name="x" /></button>
          </div>
          <div>
            <label className="field-label" htmlFor="case-name">Case name</label>
            <input id="case-name" style={{ width: "100%" }} value={name} onChange={(e) => setName(e.target.value)} required placeholder="Name this case" />
          </div>
          <div>
            <label className="field-label" htmlFor="case-files">Documents</label>
            <div
              className={`dropzone${dragging ? " over" : ""}${busy ? " disabled" : ""}`}
              role="button"
              tabIndex={0}
              aria-disabled={busy}
              onClick={() => !busy && input.current?.click()}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !busy && (e.preventDefault(), input.current?.click())}
              onDragOver={(e) => { e.preventDefault(); if (!busy) setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={drop}
            >
              <span className="drop-icon"><Icon name="upload" size={20} /></span>
              <b>Drop all the files of the case here</b>
              <span className="muted">or click to choose them · PDF, JPG, PNG, TIFF or a ZIP of the whole case</span>
            </div>
            <input
              ref={input}
              id="case-files"
              type="file"
              accept={ACCEPT}
              multiple
              hidden
              disabled={busy}
              onChange={(e) => { add(e.target.files); e.target.value = ""; }}
            />
          </div>
          {files.length > 0 && (
            <ul className="file-list">
              {files.map((f) => (
                <li key={key(f)}>
                  <Icon name="fileText" className="file-ico" />
                  <span className="file-name" title={f.name}>{f.name}</span>
                  <span className="muted">{size(f.size)}</span>
                  <button type="button" className="small icon ghost" aria-label={`Remove ${f.name}`} disabled={busy}
                    onClick={() => setFiles((cur) => cur.filter((x) => key(x) !== key(f)))}><Icon name="x" size={14} /></button>
                </li>
              ))}
            </ul>
          )}
          <details className="hint">
            <summary>What the AI looks for</summary>
            <p className="muted" style={{ margin: "8px 0 4px" }}>
              Each file is identified automatically, so upload them in any order and with any name. A case is usually filed with:
            </p>
            <ol>{EXPECTED.map((d) => <li key={d}>{d}</li>)}</ol>
            <p className="muted" style={{ margin: "8px 0 0" }}>
              Add anything else that belongs to the case too (society registration, resolutions, NOCs, letters, …) — it is read,
              summarised and compared with the other documents. Missing, wrong or unrelated documents are reported in the document check.
            </p>
          </details>
          {msg && <div className={msg.kind}><Icon name={msg.kind === "error" ? "alert" : "check"} /> <span>{msg.text}</span></div>}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" onClick={() => setOpen(false)} disabled={busy}>Cancel</button>
            <button className="primary" disabled={busy || !files.length}>
              {busy ? <span className="spinner" style={{ borderColor: "rgba(255,255,255,.35)", borderTopColor: "#fff" }} /> : <Icon name="check" />}
              {step ?? `Create case and check ${files.length ? `${files.length} file${files.length === 1 ? "" : "s"}` : "documents"}`}
            </button>
          </div>
        </form>
      )}
    </>
  );
}
