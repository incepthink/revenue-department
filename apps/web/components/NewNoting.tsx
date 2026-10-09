"use client";
import { useRef, useState } from "react";
import { send } from "@/lib/client";
import Icon from "./Icon";

// "Prepare Noting": pick the Collector's report (one PDF); it is uploaded straight to the API (FormData bypasses the
// web proxy's body cap), its pages are examined, and the noting page opens on the page-range step.
export default function NewNoting() {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function upload(f: File | undefined) {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      setErr("Choose the Collector's report as a PDF.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("title", f.name.replace(/\.pdf$/i, ""));
      const r = await send<{ case_id: string }>("POST", "/notings", fd);
      window.location.href = `/notings/${r.case_id}`;
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <>
      <button onClick={() => input.current?.click()} disabled={busy} title="Upload a Collector's report and draft the department's Noting">
        {busy ? <span className="spinner" /> : <Icon name="edit" />} {busy ? "Uploading…" : "Prepare Noting"}
      </button>
      <input ref={input} type="file" accept=".pdf,application/pdf" hidden
        onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
      {err && <span className="error noting-upload-error" role="alert"><Icon name="alert" size={14} /> {err}</span>}
    </>
  );
}
