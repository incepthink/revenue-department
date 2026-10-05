# HANDOFF — end of Phase 4 (Precedent Drafting & 20-Case Backtest)

Date: 2026-09-27. This closes the four build phases of `EXECUTION_PLAN.md`. The Phase 2 and Phase 3 handoffs (previous
versions of this file) still apply for their "stubbed" items and open questions 1–15 unless listed as closed below.

## Status against "Done when"

| Criterion | Result |
|---|---|
| All four drafts render for any analysed case | **Pass.** छाननी टिप्पणी, त्रुटी पत्र, मागणी पत्र and जिल्हाधिकारी आदेश render, in both render modes, for the incoming sample (DKT-002), for decided precedents, and for all 20 holdouts at their filing stage in every backtest run (`draft_render_ok_rate` 1.0, gated). Every issue render passes the leak check (`leak_failures` 0, gated). Missing inputs become visible blanks with an `incomplete` status and a reason — never a guessed value. Checked visually: LibreOffice → PDF renders the Devanagari, the diagonal watermark and the tables correctly. |
| Backtest report generated and gated in CI | **Pass.** `rci backtest` writes Markdown + HTML (stored per run, served at `/backtest/runs/{id}/report.html`). `eval/backtest/ci_backtest.py` rebuilds a fresh database, selects the holdouts, runs the backtest and gates it against the committed `eval/backtest/baseline.json`; `.github/workflows/ci.yml` runs it after pytest. Locally `make backtest-gate` passes, and a deliberately worse baseline fails (tested). |
| Tests | **266 passing** (`make test`): 198 from Phases 1–3 + 68 new (Marathi filters 29 incl. exhaustive / Hypothesis round-trips, drafting units 20, backtest units 8 incl. a Hypothesis allocation property, drafting DB/API 6, backtest DB/API 5). `make check` and `make web-check` still pass. `eval/e2e/e2e_phase4.py` drives the three new screens in Chrome (screenshots in `var/e2e_shots/phase4_*.png`). |

### Backtest numbers (synthetic — measure the machinery, not office practice)

Clean database, 60-case synthetic pool, 20 stratified holdouts (tenure × era: 6 / 4 / 6 / 4), weights tuned on the other
40 only, fee world `synthetic_5pct`:

| | |
|---|---|
| Queries | precision 0.840 · recall 0.913 · **F1 0.875** · first-letter completeness 0.883 (office's own first letter 1.0) · **statutory false negatives 0** |
| Next steps | precision 0.923 · recall 0.885 · F1 0.904 · Kendall τ 1.0 |
| Fee | coverage 1.0 · exact match 1.0 · mean abs error 0 % (the synthetic world uses the rate the pool was generated with) |
| Precedents | P@5 / nDCG@5: no officer labels yet · proxy nDCG@5 0.719 (not gated) |
| Drafts | render ok 1.0 · no blanks 0.49 (blocked fees, orders before payment) · leak failures 0 |
| Integrity | holdout / time-travel / same-society leaks 0 · tuning leak 0 · replay errors 0 |

Pre-2023 holdouts score lower (F1 0.79 vs 0.96): the synthetic pool has office practice shift at the 2023 amendment. That
is the case the rule-compatibility labels exist for.

**The department's 20 real holdouts have not been run** — there are no approved real cards yet (see "Stubbed").

## What was built in Phase 4

### Drafting (`services/api/src/rci/drafting`)

* **`mrtext.py`** — Jinja filters: `mr_num` (Devanagari digits), `inr` (Indian grouping 45,64,325; paise only when non-zero),
  `mr_words` (lakh / crore, recursive crores), `mr_rupees` ("रुपये … आणि … पैसे फक्त"), `mr_date` (२८/०६/२०२४ or २८ जून २०२४),
  `fy_label`. Numbers 0–99 come from a table; `parse_mr_words` is the exact inverse. Tests: every number 0–199,999 round-trips
  with a unique spelling, 3,000 Hypothesis cases up to 10^15 with structural checks, goldens incl. the DKT-001 amount.
  A missing slot is a `Blank` that every filter passes through, so drafts show `________`.
* **Clause library (`clauses.py`, table `clause_library`)**
  * *Seed*: `rci/seed/clauses.yaml` — 53 canonical clauses for every section of the four drafts and every applicant
    requirement, modelled on the DKT-001/002/003 formats; loaded by `rci seed` (`origin=seed`, `data_class=any`, `to_verify`).
  * *Mining* (`rci clauses mine`, Admin → Clauses → Mine): precedents only (never holdouts). Each QUERY_LETTER /
    DEMAND_LETTER / COLLECTOR_ORDER / OFFICE_NOTE is split by letter structure (header up to विषय / आदेश skipped; numbered
    items; ज्याअर्थी / त्याअर्थी; अटी; प्रत; signature skipped) into clause keys — query items get `query_item.<CODE>` from
    the reviewed item → code mapping. The case's own facts are replaced by slots (society, place, CTS, area, every
    date, amounts, GRN, order no.). Leftover digits flag `needs_slots`, PII tokens `has_pii`; one row per distinct
    wording with its sources; embedded (same client and residency guard as the case facets) and leader-clustered
    per kind (cosine ≥ `clauses.cluster_threshold`, 0.8).
  * *Curation* (web **Clauses** page; `PATCH /clauses/{id}`): officers pick canonical wording, edit the Jinja
    template (slot palette, live preview with example or a real case's values), tag rule sets and conditions
    (`tenure`, `validator_failed`, `premium_paid`, `has_query_letter`, `fee_status`). Canonical templates are refused
    when they contain literal digits, PII tokens, unknown slots / filters or syntax errors. Only an admin marks wording
    `verified`. One canonical row per (data class, key, conditions, rule sets).
  * *Selection*: the most specific canonical row whose conditions hold and whose rule-set tags intersect the case's
    rule stack; class-specific beats `any`, curated beats seed. Synthetic wording is never used for a real case.
* **`crosscheck.py` + `narrative.py`** — narrative slots (note हकीकत, order वस्तुस्थिती) are built from card facts in date
  order. An LLM may re-word them (Azure Regional only for real data). The output is rejected unless every
  number / date / ID traces to the case facts and no figure of the template recital is dropped. Every rendered
  clause goes through the same check; untraceable figures are reported on the draft.
* **`context.py`** (pure) — per-template content: facts table, documents, checks, gaps with basis, fee lines from the
  trace, next steps, proposal (query / demand / order / fee pending); query items = gaps whose basis is in
  `query_letter.include_bases` (SUPERSEDED go to the internal notes only). Internal mode adds `[टीप n]` markers and
  notes: clause key / origin / verification / source cases, precedent citations, rule rows, fee engine status, and
  which office-format items are still to_verify.
* **`docxbuild.py`** builds the docxtpl templates (`config/templates/*.docx`, `rci build-templates`). The font is set
  on every style in all rFonts slots — Devanagari is a complex script in Word, so `w:cs` / `bCs` / `szCs` are what
  apply — with the `mr-IN` language tag. The office can restyle them in Word, keeping the tags.
* **`render.py`** — docxtpl render (values XML-escaped). Internal renders get a VML diagonal watermark and a red header
  line. `leak_check`: an issue render may not contain another case's ref, id, exhibit id or society, the watermark or
  markers, or a PII token that is not this case's. A failing issue render is withheld (not stored, not downloadable).
  `diff` compares paragraphs, shows which clauses the officer kept, and lists the numbers changed.
* **`service.py` + API** (`POST /cases/{id}/drafts`, `GET /cases/{id}/drafts`, `GET /drafts/{id}`,
  `…/download`, `…/upload`, `…/rating`; officer / admin only):
  * Each generation stores both renders under one version; earlier versions become `superseded`.
  * Downloading the issue render detokenises this case's PII through `vault_access.render_with_pii` (officer / admin,
    audited `vault.detokenize`). Stored drafts keep tokens.
  * An uploaded edit is stored, re-tokenised and redacted (so the diff in the DB holds no real name or number), then
    diffed. Status becomes `officer_edited`.
  * Ratings go to `eval_labels` (`draft_rating`), one current rating per case, template and officer.
* **Web: `/cases/[id]/drafts`** — generate / regenerate; per template: status, leak and figure checks, blanks, warnings,
  internal and issue downloads, paragraph preview of both renders, clauses used, upload with the diff view, rating.

### Backtest (`services/api/src/rci/backtest`)

* **`holdouts.py`** — stratified selection (`rci holdouts select`, Backtest page):
  * Strata are tenure × era (the 2023-03-27 amendment); allocation is largest-remainder with ≥ 1 per non-empty stratum.
  * Draws are seeded and reproducible.
  * Selected cases are locked (`role=holdout`, `role_locked`). A selection is final; re-selecting returns it unchanged.
  * `release` exists only for a deliberate re-selection (`--yes`).
* **`harness.py`** — per holdout:
  * Replay: the card is masked to its filing stage (`filing_card`). `analyse_case` runs with `as_of` = the application
    date and `today` = the application date. Each result is checked for holdouts, later-decided or same-society
    precedents (`holdout_leaks`).
  * Outputs: predicted query items, next-step events, the fee in the class's fee world (`database` for real,
    `synthetic_5pct` for synthetic), the top-5 precedents, and all four drafts (`drafts: check | store | off`).
  * Truth comes from the approved card: every queried code, the first letter's codes, the events after filing, the
    challan / order / demand premium, and the statutory codes on the rule date.
  * Tuned weights are refused if their recorded tuning set (`intel_tuned.yaml` → `tuned.cases`, now written by
    `rci tune --save`) contains a holdout. The run falls back to defaults, and `tuning_leak` fails the gate.
  * Runs are stored in `eval_runs` (kind `backtest`) and `eval_case_results`.
* **`metrics.py`** — the metrics in the plan:
  * query precision / recall / F1 (micro), first-letter completeness (with the office's own first letter as baseline),
    statutory false negatives, adjudicated precision;
  * steps precision / recall / F1 and Kendall τ;
  * fee coverage, exact-match rate and absolute % error;
  * P@5 / nDCG@5 from officer relevance labels (0 / 1 / 2), with a proxy nDCG shown but not gated;
  * draft render / leak / completeness rates and officer ratings;
  * a per-stratum breakdown (tenure, era, decision, queried).

  Label-based metrics are re-scored from `eval_labels` every time a run is read, so adjudications made later count
  without re-running.
* **`gate.py`** — `config/backtest.yaml`:
  * Absolute checks: leak failures 0, render ok 1.0, holdout leaks 0, no tuning leak, no replay errors, at least 20 holdouts.
  * Regression checks against the baseline, each with its own tolerance and direction.
  * A different holdout set (hash) fails until a new baseline is written deliberately.
  * Metrics missing on either side are marked "skipped", never "passed".
* **`report.py`** — Markdown + self-contained HTML (gate table, headline, strata, per-holdout rows, warnings).
* **API / web:** `/backtest` (runs, holdouts, select, run — admin) and `/backtest/[id]` (gate banner, summary, strata,
  per-holdout table, false-positive adjudication "valid but not asked" / "truly unnecessary", precedent relevance
  labelling, report links).

### Storage, CLI, CI

* **Migration `0004` (additive):**
  * `clause_library`: data class, origin, verification, seed key, text hash, sources, flags, curation columns and checks.
  * `drafts`: analysis / run links, hashes, officer upload columns, status and mode checks.
  * `eval_labels`: `label_key`, `run_id`, `updated_at`, one label per (case, kind, key, labeller).
  * An index on `eval_case_results`.
* **CLI:** `build-templates`, `clauses mine|seed|list|slots`, `draft <ref> [--template] [--store]`,
  `holdouts select|list|release`, `backtest [--gate] [--update-baseline] [--drafts]`.
* **Make targets** (`make.ps1` has the same): `templates`, `clauses`, `drafts`, `holdouts`, `backtest`, `backtest-gate`,
  `backtest-baseline`.
* **`.github/workflows/ci.yml`:** pytest against a pgvector service, then the gate on a fresh `rci_ci` database
  (report uploaded and written to the job summary), then web typecheck + build.
* **Not yet exercised:** the repo is not under git and the workflow has not run on GitHub. It mirrors the local
  `make backtest-gate`, which passes.

### Changed Phase 3 pieces

* **Synthetic generator:** letters and orders now carry the real office format (letterhead, subject, reference, closing,
  वाचले / ज्याअर्थी / अटी / प्रत). This is static text only, so the RNG sequence is unchanged; a check over all 40 old cases
  showed identical facts.
* **Tuning:** `rci tune --save` records the tuning case list.
* **`config/intel_tuned.yaml`:** re-tuned on the clean pool without holdouts — 36 queries, F1 0.819 → 0.847, threshold 0.6.
* **`test_intel_units`:** now reads the default weights explicitly instead of whatever the last tuning run wrote.

## How to run

```
.\make.ps1 up ; .\make.ps1 migrate ; .\make.ps1 seed                     # (make … on Linux)
.\make.ps1 analyse -Case DKT-002 ; .\make.ps1 drafts -Case DKT-002       # → var/out/drafts/*.docx (internal + issue)
.\make.ps1 clauses                                                        # mine; curate in the web app (Clauses)
.\make.ps1 holdouts ; .\make.ps1 backtest                                 # BtClass / BtPrefix default synthetic / SYN-P
.\make.ps1 backtest-gate                                                  # what CI runs; exit 1 on regression
.\make.ps1 api ; .\make.ps1 web                                           # officer@ / admin@rci.local, dev-password-1
python eval/e2e/e2e_phase4.py DKT-002                                     # PW_CHROMIUM=<chrome.exe>; needs `pip install playwright`
```

Real data, once 50+ real cards are approved:

```
rci holdouts select --data-class real
rci tune --save --data-class real
rci clauses mine --data-class real
rci backtest --data-class real
```

Commit the real baseline only after the department has rated drafts and adjudicated the false positives.

## Dev database state after this session

* **Migrations:** `0004` applied.
* **Pool:** `SYN-P-001 … 060` regenerated (60 cases). 20 of them are locked holdouts (set `2993bd4a9eb7ab4d`). The
  pool was regenerated, so stored analyses of the old pool are stale; DKT-001 / 002 / 003 were re-analysed.
* **Clause library:** 53 seed clauses; 60 mined synthetic candidates in clusters. Nothing is curated in the dev DB.
  The E2E only previews edits.
* **Drafts:** DKT-002 has drafts v1–v3. v3's query letter carries an officer upload and a rating from the E2E run. The
  latest backtest run stored drafts for every holdout.
* **Backtest runs:** 2 in dev. The E2E added one FP adjudication and one relevance label on the latest run.
* **`rci_ci`:** the gate's own database; the gate script recreates it.
* **Other:** DEMO2 / DEMO3-DKT-002 (approved demo copies) are still in the dev pool. They are why dev and CI numbers
  are only compared on the clean `rci_ci` database.

## Stubbed / not verified

* **No real holdouts:** there are no approved real cards (the Phase 2–3 gap persists), so no real backtest, no
  officer relevance labels, adjudications or draft ratings. P@5, nDCG@5, adjudicated precision and the rating are
  "skipped" in the gate until they exist. The synthetic gate proves the machinery and catches regressions, not accuracy.
* **Office format (all `to_verify` in `config/drafting.yaml`):** letterhead, reference-number format, addressee block,
  signatories, reply / payment days (30 / 30), head of account, the Marathi names of the rule sets, and the font
  (Nirmala UI) are placeholders. No letterhead or format samples were available.
* **Seed wording:** every seed clause is `to_verify`. Marathi spellings of numbers follow one common standard
  (e.g. तेहेतीस, सेहेचाळीस, एक्क्याऐंशी); confirm the office's preferred forms.
* **Demand letters in dev:** these are `incomplete`, because fees stay blocked (no verified slab rate, no ASR table;
  Phase 2–3 gap). They render with blanks and the reason.
* **Orders drafted before payment** are `incomplete` by design. Orders assume a grant; a rejection order template is
  not built.
* **LLM narrative:** tested only with the fake client (acceptance and every rejection path). Live Azure wording is untested.
* **Clause mining:** runs on synthetic letters. Real letters with other layouts may need more section rules in
  `split_exhibit`. Clause text of mined candidates is the source wording, shown only to curators.
* **The docx watermark:** a VML shape (what Word writes). Checked in LibreOffice; not opened in Word itself.
* **Environment:** Docker Desktop's engine stopped once during the session (the API hung on DB calls until it was
  restarted). Worth knowing when an API call hangs while `/health` still answers.

## Open questions for the department (new; 1–15 still open)

16. Letter formats: letterhead, reference-number format, who signs query and demand letters, and the copy-to list on
    orders. Samples would allow editing `config/templates/*.docx` directly.
17. Deadlines to state: days to comply with a query letter, and days to pay a demand.
18. The head of account printed on demand letters, and whether one letter may demand both premium and lease-rent arrears.
19. The standard conditions (अटी व शर्ती) of a conversion order, and whether they differ for leasehold land or by rule set.
20. Is there a standard rejection order? Should the tool draft one?
21. Who rates drafts and adjudicates backtest false positives, and on what scale? The tool uses 1–5 plus usability.

## Notes for deployment (after Phase 4)

* **Hosting and PII:** drafts and officer uploads live in blob container `out`, which holds PII once officers upload
  their edits. Give it the same restricted access, CMK and backup policy as `raw`, `pages` and `ocr`.
* **Real-data order:**
  1. approve cards;
  2. select real holdouts;
  3. re-tune on real precedents;
  4. mine and curate real clauses;
  5. run the real backtest;
  6. collect officer labels;
  7. write the real baseline;
  8. switch CI to gate on it — it needs a fixture or a secured database with those cards, since real dockets cannot
     go to a public CI runner.
* **Embeddings:** real clauses are embedded with `local-hash-v1` until an Azure embedding deployment exists.
  Re-embedding later means re-running `rci clauses mine`; rows with a changed template lose their vector and are
  re-embedded.
