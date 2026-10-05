# Revenue Case Intelligence — MVP

Case intelligence for Cooperative Housing Society (CHS) land conversions from Occupancy Class-II /
Government lease to Class-I. See `EXECUTION_PLAN.md` for the four phases and `HANDOFF.md` for current state.

## Layout

```
apps/web            Next.js: cases, review screen, case analysis, drafts, clause library, backtest, admin, reports
services/api        Python package `rci`: FastAPI app, pipeline, DB models, Alembic, CLI, tests
services/worker     Arq worker (Redis) running the same pipeline for queued cases
packages/schemas    card.v1 + extraction JSON schemas
infra/              docker-compose support (Postgres init, app Dockerfile)
config/             parameters.yaml, fees.yaml (department parameters, each `verified` / `to_verify`);
                    intel.yaml (matching / gap / step settings) + intel_tuned.yaml (written by `rci tune --save`);
                    drafting.yaml (office format, deadlines, font — to_verify), templates/*.docx (docxtpl),
                    backtest.yaml (holdouts, strata, CI gate tolerances)
eval/               sample dockets + ground truth, ingest checker, OCR benchmark, synthetic ASR CSV, UI e2e scripts,
                    backtest/ (reproducible CI gate script + committed baseline.json)
.github/workflows   CI: pytest, backtest gate on a fresh database, web build
```

## Quick start (local, Windows or Linux)

Needs Python 3.12, Docker (Postgres 16 + pgvector, Redis, Azurite), Node 20+ for the web app.
Windows without `make`: use `.\make.ps1 <target>` — same targets.

```
make setup        # venv, deps, .env with fresh vault keys, docker infra, migrate, seed
make ingest       # ingest eval/samples/dockets → Postgres; cards in var/out/*.card.json
make check        # boundaries, facts, validations, requirements, events, redaction vs ground truth
make idempotency  # re-ingest and verify no rows / card versions change
make test         # pytest: unit + Hypothesis + DB end-to-end + API
make ocr-bench    # CER per OCR engine per exhibit type
make api          # http://localhost:8000/docs     make web → http://localhost:3000
make users        # dev accounts: admin@ / reviewer1@ / reviewer2@ / officer@rci.local, password dev-password-1
make rules-gaps   # active CHS premium slabs that are not verified yet
make report       # extraction-accuracy + double-review agreement reports → var/out/*.md
make web-check    # typecheck + production build of the web app
make precedents   # synthetic approved precedent pool (40 cases, data_class=synthetic) for dev / demo
make analyse      # CASE=DKT-002: precedents, gaps, next steps, fee trace → var/out/DKT-002.analysis.json
make fee-golden   # fee engine vs precedent challans; exits 1 if any mismatch is unexplained
make tune         # leave-one-out tuning on precedents (TUNE_CLASS=synthetic|real) → config/intel_tuned.yaml
make clauses      # mine precedent letters / orders into the clause library (curate in the web app: Clauses)
make drafts       # CASE=DKT-002: the four Marathi drafts (internal + issue .docx) → var/out/drafts/
make holdouts     # select + lock the stratified holdout set (BT_CLASS=synthetic|real, BT_PREFIX=SYN-P)
make backtest     # replay the holdouts at filing stage → stored run + var/out/backtest/backtest_*.md|html
make backtest-gate      # the CI gate: fresh DB (rci_ci) → pool → holdouts → backtest; exit 1 on regression
make backtest-baseline  # rewrite eval/backtest/baseline.json from a fresh run (commit it deliberately)
```

Windows: `.\make.ps1 analyse -Case DKT-002`, `.\make.ps1 tune -TuneClass synthetic`.

New-case intake: **+ New Case** uploads one file per document slot (7/12, property card, grant, application, Aadhaar,
tax receipts, Index II) and opens `/cases/{id}/intake`. The run happens in the background (worker in queue mode). Each
document is read (OCR, redaction), then one LLM call checks it on its own: is it the right document, its fields
(verbatim and grounded on the page), and any problems. Deterministic checks then compare the documents (land ID, area,
holder, tenure, how recent each is, tax arrears, signatory). The page shows progress, then the verdict (ready /
needs attention / incomplete), the actions for the applicant, the facts found and a card per document. Code:
`services/api/src/rci/intake`; e2e: `python eval/e2e/e2e_intake.py`.

Case analysis (Phase 3): `POST /cases/{id}/analyse` or the **Case analysis** button. Matched precedents (SQL hard filters,
pgvector recall, weighted rerank with reasons), cited gaps labelled STATUTORY / PRACTICE / PRACTICE_OLD_RULES / SUPERSEDED,
next steps (precedent frequency + rule and fact triggers) and a deterministic fee trace. Every item links to its source page.

Drafting (Phase 4): `POST /cases/{id}/drafts` or the **Drafts** button (officer / admin). छाननी टिप्पणी, त्रुटी पत्र,
मागणी पत्र and जिल्हाधिकारी आदेश, each as an *internal* render (provenance notes, “AI-सहाय्यित मसुदा” watermark) and an *issue*
render (clean; a leak check refuses any other case's reference / society / foreign PII token). Missing inputs become
visible blanks, never guesses. The officer uploads the edited .docx; the (re-redacted) diff is stored.

Backtest (Phase 4): **Backtest** page. 20 stratified holdouts replayed at filing stage with time travel; query / step /
fee / precedent / draft metrics, officer adjudication of false positives and relevance labels, HTML + Markdown report,
CI gate against `eval/backtest/baseline.json`.

There is no login: the platform has a single user. The web app sends no token and the API acts as the built-in
**operator** user (admin role, created on first request), which can do everything. Bearer tokens from `/auth/login` are
still honoured for scripts and tests. Every mutating request and every page-image read is audited.

Postgres is published on **localhost:5434** (5432/5433 were taken on the dev machine).

CLI: `rci ingest <folder> --data-class real|synthetic` — one case per sub-folder (or the folder itself);
PDFs, images and ZIPs are accepted. `--data-class` defaults to `real`, the strictest routing.

## Data handling rules (enforced in code)

* All LLM calls (intake feedback, classification, extraction, explanations, narrative, LLM-vision OCR) go directly to
  the OpenAI API (`OPENAI_API_KEY`, model `RCI_OPENAI_MODEL`, default `gpt-6-luna`), for real and synthetic data alike.
  Azure Document Intelligence OCR, if enabled, is still limited to an allowed region (default `centralindia`).
* PII is redacted before classification, extraction or any LLM call; values live AES-GCM encrypted in
  `vault.pii_tokens`. Page images and raw OCR stay in restricted blob containers.
* Anything the department has not confirmed is `to_verify` (parameters, catalog rows, pilot office, draft wording and
  office format) and results depending on it say so.
* Drafts are detokenised only on download of the issue render, by an officer / admin, through `vault_access` (audited).
* Holdouts (role locked) never reach matching, tuning or clause mining; the backtest checks this on every run.
