# Revenue Case Intelligence — developer targets. Windows without make: use ./make.ps1 <target>.
PY      ?= .venv/bin/python
ifeq ($(OS),Windows_NT)
PY      := .venv/Scripts/python
endif
RCI     := $(PY) -m rci.cli
SAMPLES := eval/samples/dockets

.PHONY: help env venv install up down migrate seed setup samples ingest check idempotency test ocr-bench api worker web schemas app-up users rules-gaps report web-check precedents analyse fee-golden tune templates clauses drafts holdouts backtest backtest-gate backtest-baseline

help:
	@echo "setup      venv + deps + .env + docker infra + migrate + seed"
	@echo "ingest     ingest the sample dockets (synthetic) and write var/out/*.card.json"
	@echo "check      verify ingest output against eval/samples/expected (boundaries, facts, redaction)"
	@echo "idempotency re-ingest and verify nothing changes"
	@echo "test       pytest (unit + property + DB end-to-end)"
	@echo "ocr-bench  OCR CER per engine per exhibit type"
	@echo "users      create dev users (admin/reviewer1/reviewer2/officer @rci.local, password dev-password-1)"
	@echo "rules-gaps active CHS premium slabs that are not verified"
	@echo "report     extraction-accuracy and agreement reports (markdown, saved as eval_runs)"
	@echo "web-check  typecheck + production build of apps/web"
	@echo "precedents synthetic approved precedent pool (40 cases, data_class=synthetic)"
	@echo "analyse    analyse CASE (default DKT-002): precedents, gaps, next steps, fee trace → var/out/CASE.analysis.json"
	@echo "fee-golden fee engine vs precedent challans; fails if any mismatch is unexplained"
	@echo "tune       leave-one-out tuning on precedents (TUNE_CLASS=synthetic|real) → config/intel_tuned.yaml"
	@echo "templates  regenerate config/templates/*.docx (drafting layout + font)"
	@echo "clauses    mine precedent letters / orders into the clause library (candidates + clusters)"
	@echo "drafts     the four Marathi drafts for CASE (both renders) → var/out/drafts/"
	@echo "holdouts   select + lock the stratified holdout set (BT_CLASS=synthetic|real, BT_PREFIX=SYN-P)"
	@echo "backtest   replay the holdouts of BT_CLASS / BT_PREFIX, store the run, write var/out/backtest/*.md|html"
	@echo "backtest-gate  CI gate on a fresh database (rci_ci): exit 1 on any regression"

venv:
	python -m venv .venv

install:
	$(PY) -m pip install -e "services/api[dev]"

env:
	$(RCI) init-env

up:
	docker compose up -d db redis azurite

down:
	docker compose down

migrate:
	$(RCI) migrate

seed:
	$(RCI) seed

setup: venv install env up migrate seed

samples:
	$(PY) eval/samples/generate_samples.py

ingest: migrate seed
	$(RCI) ingest $(SAMPLES) --data-class synthetic

check:
	$(PY) eval/check_ingest.py

idempotency:
	$(PY) eval/check_ingest.py --rerun

test:
	cd services/api && ../../$(PY) -m pytest -q

ocr-bench:
	$(PY) eval/ocr_bench/bench.py

schemas:
	$(RCI) export-schemas

api:
	cd services/api && ../../$(PY) -m uvicorn rci.api.main:app --reload --port 8000

worker:
	cd services/worker && ../../$(PY) -m arq worker.WorkerSettings

web:
	cd apps/web && npm install && npm run dev

app-up:
	docker compose --profile app up -d --build

DEV_PW ?= dev-password-1
users:
	$(RCI) create-user admin@rci.local admin --name Admin --password $(DEV_PW) --exist-ok
	$(RCI) create-user reviewer1@rci.local reviewer --name Reviewer-A --password $(DEV_PW) --exist-ok
	$(RCI) create-user reviewer2@rci.local reviewer --name Reviewer-B --password $(DEV_PW) --exist-ok
	$(RCI) create-user officer@rci.local officer --name Officer --password $(DEV_PW) --exist-ok

rules-gaps:
	$(RCI) rules-gaps

report:
	$(RCI) report extraction --save --out var/out/extraction_accuracy.md
	$(RCI) report agreement --save --out var/out/agreement.md

web-check:
	cd apps/web && npm install && npx tsc --noEmit && npm run build

CASE       ?= DKT-002
TUNE_CLASS ?= synthetic
precedents:
	$(RCI) seed-precedents --n 40

analyse:
	$(RCI) analyse $(CASE) --out var/out/$(CASE).analysis.json

fee-golden:
	$(RCI) fee-golden --save --out var/out/fee_golden.md

tune:
	$(RCI) tune --save --data-class $(TUNE_CLASS) --out var/out/tuning.md

BT_CLASS  ?= synthetic
BT_PREFIX ?= SYN-P
templates:
	$(RCI) build-templates

clauses:
	$(RCI) clauses mine

drafts:
	$(RCI) draft $(CASE)

holdouts:
	$(RCI) holdouts select --data-class $(BT_CLASS) --prefix $(BT_PREFIX)

backtest:
	$(RCI) backtest --data-class $(BT_CLASS) --prefix $(BT_PREFIX)

backtest-gate:
	$(PY) eval/backtest/ci_backtest.py

backtest-baseline:
	$(PY) eval/backtest/ci_backtest.py --update-baseline
