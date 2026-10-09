"use client";
import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { fmtDate, libraryHref, type LibraryTaxonomy, type Passage, type SearchResult } from "@/lib/api";
import { get, send } from "@/lib/client";
import Icon from "../Icon";

// One question per loaded section (A–E), in English and Marathi, to show what the search can answer.
const EXAMPLES = [
  "Transfer charge to regularise a society flat on government land sold without the Collector's permission",
  "Mortgage fee for a flat mortgaged to a bank without permission",
  "Who can give advance possession of government land: the Collector or the Divisional Commissioner?",
  "Which year's annual statement of rates is used to value government land?",
  "What premium is charged to convert land leased to a housing society into Occupancy Class-I?",
  "वर्ग-१ रूपांतरणासाठी राज्य शासनाची पूर्वमान्यता केव्हा आवश्यक आहे?",
  "Is premium already paid for change of use adjusted against the Class-I conversion premium?",
];

// Query words (and the glossary's Marathi terms) in bold inside a passage.
function Marked({ text, terms }: { text: string; terms: string[] }) {
  const parts = useMemo(() => {
    const words = terms.filter((t) => t.length >= 2).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!words.length) return [text];
    const re = new RegExp(`(${words.join("|")})`, "gi");
    return text.split(re);
  }, [text, terms]);
  return <>{parts.map((p, i) => (i % 2 ? <b key={i}>{p}</b> : p))}</>;
}

const today = () => new Date().toISOString().slice(0, 10);

const STATUS: Record<string, { text: string; lvl: string }> = {
  in_force: { text: "In force", lvl: "lvl-ok" }, amended: { text: "In force, amended", lvl: "lvl-info" },
  superseded: { text: "Superseded", lvl: "lvl-problem" }, not_yet: { text: "Not yet issued", lvl: "lvl-unchecked" },
};

// GR search: the query is kept in the URL (?q=) so a search can be linked to. Results are the rules in force on a date
// (today by default), from the links between GRs the operator confirmed on the Timeline.
export default function SearchApp({ initialQuery }: { initialQuery: string }) {
  const [q, setQ] = useState(initialQuery);
  const [tx, setTx] = useState<LibraryTaxonomy | null>(null);
  const [type, setType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [onDate, setOnDate] = useState(today());
  const [withSuperseded, setWithSuperseded] = useState(false);
  const [res, setRes] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { get<LibraryTaxonomy>("/library/taxonomy").then(setTx).catch(() => setTx(null)); }, []);

  const run = useCallback(async (query: string) => {
    const text = query.trim();
    if (text.length < 2) return;
    setBusy(true);
    setError(null);
    window.history.replaceState(null, "", `/library/search?q=${encodeURIComponent(text)}`);
    try {
      setRes(await send<SearchResult>("POST", "/library/search", {
        q: text, types: type ? [type] : [], date_from: from || null, date_to: to || null, answer: true,
        in_force_on: onDate || null, include_superseded: withSuperseded,
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [type, from, to, onDate, withSuperseded]);

  useEffect(() => { if (initialQuery) run(initialQuery); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = (e: FormEvent) => { e.preventDefault(); run(q); };
  const terms = useMemo(() => {
    const rw = res?.rewrite as { tokens?: string[]; terms_mr?: string[] } | undefined;
    return [...(rw?.terms_mr ?? []), ...(res?.query ?? "").split(/\s+/).filter((w) => w.length > 3)];
  }, [res]);
  const byId = useMemo(() => new Map((res?.passages ?? []).map((p) => [p.provision_id, p])), [res]);

  const answer = res?.answer;
  const cards: ReactNode[] = (res?.passages ?? []).map((p, i) => <PassageCard key={p.provision_id} p={p} n={i + 1} terms={terms} />);

  return (
    <main className="page lib-search">
      <div className="page-head">
        <div>
          <h1>GR search</h1>
          <div className="sub">
            Ask in Marathi or English. The answer is written only from the GRs of the department&apos;s compendium, and every
            sentence cites the passage it comes from; a chip opens the GR page with the quoted words marked.
          </div>
        </div>
        <div className="actions-bar"><a className="btn" href="/library"><Icon name="book" /> Browse library</a></div>
      </div>

      <form className="card lib-searchbox" onSubmit={submit}>
        <div className="row" style={{ gap: 8 }}>
          <input className="lib-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. premium for Class-I conversion of a housing society's land"
            aria-label="Search the GRs" autoFocus />
          <button className="primary" type="submit" disabled={busy || q.trim().length < 2}>
            {busy ? <span className="spinner" /> : <Icon name="search" />} Search
          </button>
        </div>
        {tx && (
          <div className="lib-filterrow">
            <div className="row" style={{ gap: 8 }}>
              <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Document type">
                <option value="">All types</option>
                {Object.entries(tx.types).filter(([k]) => k !== "case_note").map(([k, t]) => <option key={k} value={k}>{t.en}</option>)}
              </select>
              <label className="muted">From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
              <label className="muted">To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
            </div>
            <div className="row" style={{ gap: 12 }}>
              <label className="muted" title="Only provisions in force on this date (empty: show every date)">
                In force on <input type="date" value={onDate} onChange={(e) => setOnDate(e.target.value)} />
              </label>
              <label className="muted">
                <input type="checkbox" checked={withSuperseded} onChange={(e) => setWithSuperseded(e.target.checked)} /> Include superseded provisions
              </label>
            </div>
          </div>
        )}
        {!res && !busy && (
          <div className="lib-examples">
            <span className="muted">Try:</span>
            {EXAMPLES.map((x) => <button type="button" key={x} className="ghost small" onClick={() => { setQ(x); run(x); }}>{x}</button>)}
          </div>
        )}
      </form>

      {error && <div className="error"><Icon name="alert" /> <span>{error}</span></div>}

      {answer && (
        <section className={`card lib-answer${answer.status === "answered" ? "" : " none"}`} aria-live="polite">
          <div className="card-title"><Icon name="checkCircle" /> Answer</div>
          {answer.status === "answered" ? (
            <div lang={answer.lang}>
              {answer.sentences.map((s, i) => (
                <p key={i} className="lib-sentence">
                  {s.text}{" "}
                  {s.cites.map((c, j) => {
                    const p = byId.get(c.passage_id);
                    if (!p) return null;
                    const page = c.anchor?.page_no ?? p.page_from;
                    return (
                      <a key={j} className="cite-chip" href={libraryHref(p.document_id, { prov: p.provision_id, page, q: c.quote })}
                        title={c.quote}>
                        {p.doc_key} · {p.chip}
                      </a>
                    );
                  })}
                </p>
              ))}
            </div>
          ) : (
            <div className="muted">
              <p style={{ margin: 0 }}>{answer.text}</p>
              {answer.status === "not_found" && <p className="lib-note">Searches only the GRs the department provided (the GR compendium, sections A–L).</p>}
            </div>
          )}
          <p className="lib-note">
            <Icon name="info" size={12} />
            {res?.in_force_on
              ? <>Rules in force on {fmtDate(res.in_force_on)}, from the links between GRs confirmed on the <a href="/library/timeline">timeline</a>; links still to confirm do not count.</>
              : <>All dates: later GRs may change what a passage says.</>}
          </p>
        </section>
      )}

      {res && (
        <section>
          <h2 className="lib-h2">Passages <span className="muted">({res.passages.length})</span></h2>
          {cards.length ? <div className="lib-passages">{cards}</div> : (
            <div className="card"><div className="empty-state"><b>No passages found</b><span>Try other words, or fewer filters.</span></div></div>
          )}
        </section>
      )}
    </main>
  );
}

function PassageCard({ p, n, terms }: { p: Passage; n: number; terms: string[] }) {
  return (
    <article className="card lib-passage">
      <div className="lib-passage-head">
        <span className="lib-rank">{n}</span>
        <span className="pill">{p.doc_key}</span>
        <b>{p.chip}</b>
        {!p.verified && <span className="pill status lvl-unchecked" title="The document's metadata has not been confirmed">Metadata not confirmed</span>}
        {p.status && STATUS[p.status] && (
          <span className={`pill status ${STATUS[p.status].lvl}`} title={p.time_note ?? undefined}>{STATUS[p.status].text}</span>
        )}
        {p.pulled_in && (
          <span className="badge" title="Shown because a result points at it">
            {p.pulled_in.reason === "amends" ? "Amends" : "Replaces"} {p.pulled_in.doc_key} {p.pulled_in.label}
          </span>
        )}
        <span className="spacer" />
        <a className="btn small" href={libraryHref(p.document_id, { prov: p.provision_id, page: p.page_from })}>
          <Icon name="external" size={13} /> Open on page
        </a>
      </div>
      <div className="muted lib-subject-line" lang="mr">{p.subject_mr}{p.subject_en ? ` — ${p.subject_en}` : ""}</div>
      {p.heading_path.length > 0 && <div className="muted lib-heading">{p.heading_path.join(" › ")}</div>}
      {p.time_note && p.status !== "in_force" && <div className="lib-timenote">{p.time_note}</div>}
      <div className="lib-passage-text" lang="mr"><Marked text={p.text} terms={terms} /></div>
    </article>
  );
}
