# Revenue Case Intelligence MVP — Execution Plan

Scope: Cooperative Housing Societies (CHS) on Occupancy Class-II / Government leasehold land, conversion to Occupancy Class-I. Reference spec: the full engineering plan (schema, ingestion, rules layer, engine, fee engine, drafting, backtest).

Four phases. Each phase is one dedicated build session and ends in a working, testable milestone.

---

## Ground rules for every session

- One monorepo: `apps/web` (Next.js + TypeScript), `services/api` (FastAPI), `services/worker` (Celery or Arq on Redis), `packages/schemas` (JSON schemas shared by API and UI), `infra/` (docker-compose now, Bicep/Terraform later), `eval/`.
- Local-first: docker-compose with Postgres 16 + pgvector, Redis, Azurite (Blob emulator).
- One LLM client interface with `provider = azure_openai | openai`. Real department dockets go ONLY through Azure OpenAI Regional deployments + Document Intelligence in Central India. The public OpenAI API is for synthetic/sample documents only.
- Anything that needs department confirmation (fee parameters, document validity windows, unverified rule rows) lives in config or DB rows marked `to_verify`. Never hardcoded.
- Tests ship with the code (pytest; Hypothesis for the fee engine).
- Every session ends by writing `HANDOFF.md` at the repo root: what is built, how to run it, what is stubbed, open questions. Paste it at the start of the next session together with this plan.

---

## Phase 1 — Foundation & Ingestion Pipeline

**Goal:** drop a docket PDF in, get classified exhibits and a draft case card in Postgres.

**Build**
- Repo scaffold, docker-compose, settings/env handling, Makefile targets.
- Full database schema as the first Alembic migration: every table from the spec (cases, documents, pages, exhibits, case_cards, case_requirements, case_events, case_embeddings, catalogs, rule tables, asr_rates, fee_calculations, clause_library, drafts, vault.pii_tokens, audit_log, eval tables). Create everything now; later phases fill it.
- Seed data: pilot office, `exhibit_types` with Marathi keywords, `requirement_catalog` v0, `event_catalog` v0.
- Adapters: LLM client (strict JSON-schema outputs), OCR (Azure Document Intelligence Read/Layout, LLM-vision fallback below a confidence threshold).
- Pipeline tasks, idempotent on sha256:
  1. ingest → split combined PDFs
  2. rasterise (PyMuPDF, 300 dpi)
  3. legacy-Marathi-font text-layer detector (force OCR when the text layer is not Unicode Devanagari)
  4. OCR → normalise (NFC, ASCII-digit shadow copy of Devanagari digits)
  5. PII redaction: Presidio + custom recognisers (Aadhaar with Verhoeff, VID, mobile, PAN, email, bank a/c near IFSC), structural name redaction (member lists, office-bearer lines), vault tokens with AES-GCM
  6. page classifier (keyword scorer first, LLM fallback) → segmenter → stage assignment (filing / query_response / internal / final)
  7. per-exhibit extraction with evidence (value, raw, page_no, snippet), starting with the 8 highest-value types: APPLICATION, PROPERTY_CARD, LEASE_SANAD, SGM_RESOLUTION, QUERY_LETTER, FIELD_REPORT / CTSO_REPORT, CHALLAN, COLLECTOR_ORDER
  8. validators (CTS match, area tolerance, holder = society, SGM date order, property-card recency as a parameter)
  9. case card assembly (`card.v1`) + draft case_requirements and case_events
- API: `POST /cases`, `POST /cases/{id}/documents`, `GET /cases/{id}`; CLI `ingest <folder>`.
- OCR benchmark script: character error rate per engine per exhibit type on a hand-transcribed page set.

**Bring to the session:** 2–5 sample dockets (real or realistic), Azure OpenAI + Document Intelligence endpoints (or an OpenAI key for synthetic docs), pilot office details.

**Done when:** `make ingest` on the samples produces correct exhibit boundaries, a card JSON with page-referenced evidence, redacted text passes the no-Aadhaar/no-phone tests, and re-running is idempotent.

---

## Phase 2 — Review UI, Rules Layer & Knowledge Base

**Goal:** turn draft cards into approved ground truth, and load the rules and ASR data the engine depends on.

**Build**
- Review screen (three panes):
  - exhibit rail: split / merge / retype / set stage
  - page viewer: OCR bounding-box overlay, raw/OCR toggle, zoom
  - card form: confidence colouring, accept / edit / not-present, page reference required on edits
  - query-item → requirement-code mapper (type-ahead, create new code, "not a requirement")
  - events timeline editor, requirements matrix
  - Approve → new locked card version
- On approve: regenerate case_requirements, case_events, templated facts summary, and the three facet embeddings (facts / documents / issues).
- Correction logging (LLM draft vs approved diff) → field-level extraction accuracy report.
- Double-review mode + agreement report for the first 10 cards.
- Rules layer: rule_sets, rule_premium_slabs, rule_requirements, rule_obligations, rule_adjustments seeded with verified and to_verify rows; `stack_for(date)` and `resolve_slab()` raising UnverifiedRule / AmbiguousRule; tests on the 30-Sep-2024 and 31-Dec-2025 boundaries.
- Admin pages: rule set / slab editor with verification toggle and source upload; ASR rates CSV import with validation.
- Auth with roles (reviewer, officer, admin), audit_log middleware, vault access only for authorised draft rendering.

**Bring to the session:** Gazette/notification texts (2019 rules, 27-Mar-2023 amendment, 16-Mar-2024 and 28-Jun-2024 amnesty notifications, 4-Mar-2025 notification, the 2021 circular, any 2026 extension), ASR table for the pilot division.

**Done when:** a reviewer takes an ingested docket to an approved card entirely in the UI; approval populates requirements, events and embeddings; rule-resolution tests pass; no active CHS slab is left as to_verify (or the gap is documented).

**Between Phase 2 and 3 (human work, not a session):** review and approve as many cards as possible. Target 50+, with at least 30 before Phase 3.

---

## Phase 3 — Intelligence Engine & Fee Engine

**Goal:** for a new incoming application, show matched precedents, cited gaps, next steps and a fee trace.

**Build**
- Matching: SQL hard filters + pgvector recall (time-travel `as_of`, exclude same society) → Python rerank with feature breakdown and human-readable reasons.
- Rule-compatibility labelling of every recommendation: STATUTORY / PRACTICE / PRACTICE_OLD_RULES / SUPERSEDED.
- Gap analysis: weighted support, `was_queried_in`, validator-driven "invalid" gaps, citations to case + exhibit + page; Marathi explanation via constrained LLM with a post-check that the codes match exactly.
- Next steps: weighted event frequency, typical position, median durations, rule triggers and fact triggers (area mismatch → CTSO report; leasehold → lease-rent dues check).
- Fee engine: pure Python Decimal, zero LLM; parameters in config (ASR year basis, area basis, rounding); adjustments per rule set; full trace; engine version stored; golden tests against precedent challans; Hypothesis property tests.
- Leave-one-out tuning script on precedents (never holdouts) for SUPPORT_THRESHOLD and rerank weights.
- API `POST /cases/{id}/analyse`; officer "Case Analysis" screen: precedents with score breakdown, gaps table filterable by basis, steps timeline, fee trace; every item links to its source page.

**Bring to the session:** department answers on fee parameters; the approved cards.

**Done when:** analysing an incoming docket returns all four outputs with citations; fee golden tests pass or every mismatch is classified; tuned weights are saved.

---

## Phase 4 — Precedent Drafting & 20-Case Backtest

**Goal:** generate officer-ready Marathi drafts and prove accuracy on 20 held-out cases.

**Build**
- Clause library: split precedent orders/letters into clauses, embed and cluster, curation UI to pick canonical wording, add Jinja slots, tag rule sets and conditions, keep source_case_ids.
- docxtpl templates: छाननी टिप्पणी, त्रुटी पत्र, मागणी पत्र, जिल्हाधिकारी आदेश. Filters: `mr_num`, `inr`, `mr_words` (lakh/crore, exhaustively tested), `mr_date`. Unicode Devanagari font.
- Narrative slots with numeric/date/ID cross-check against structured facts.
- Two renders: internal (provenance notes + "AI-सहाय्यित मसुदा" watermark) and issue (clean, no other cases' IDs). Officer uploads the edited version; the diff is stored.
- Backtest harness: stratified holdout selection (locks role = holdout), time-travel, filing-stage masking, same-society exclusion.
- Metrics: query precision/recall/F1 + first-letter completeness, statutory false negatives, steps precision/recall + Kendall tau, fee absolute % error + exact-match rate, P@5 / nDCG@5 from officer labels, draft ratings.
- Adjudication UI for false positives ("truly unnecessary" vs "valid but not asked"); per-run HTML/Markdown report with per-stratum breakdown; CI gate that fails on regressions.

**Bring to the session:** 50+ approved cards including the 20 holdouts, office letterhead and format samples, officer time for draft rating and adjudication.

**Done when:** all four drafts render for any analysed case, and the backtest report is generated and gated in CI.

---

## After Phase 4 (deployment, not a build session)

Azure landing zone in Central India via IaC: private endpoints on everything, Regional OpenAI deployments, modified abuse monitoring approved, CMK if required, GRS backups to South India, then the shadow pilot on live files.

---

## Session kickoff prompts

Paste the prompt, this plan, and the latest HANDOFF.md.

**Phase 1:** "We are building Phase 1 of the Revenue Case Intelligence MVP (plan attached). Scaffold the monorepo and implement everything under Phase 1 with tests, local-first with docker-compose. Stop when the Done-when criteria pass and write HANDOFF.md."

**Phase 2:** "Continue with Phase 2 (plan + HANDOFF.md attached). Build the review UI, the rules layer with seed data and resolution tests, the admin pages and auth. Stop when the Done-when criteria pass and update HANDOFF.md."

**Phase 3:** "Continue with Phase 3 (plan + HANDOFF.md attached). Build matching, gap analysis, next steps, the deterministic fee engine with golden and property tests, and the Case Analysis screen. Stop when the Done-when criteria pass and update HANDOFF.md."

**Phase 4:** "Continue with Phase 4 (plan + HANDOFF.md attached). Build the clause library, the four Marathi draft templates with both render modes, and the 20-case backtest harness with CI gating. Stop when the Done-when criteria pass and update HANDOFF.md."
