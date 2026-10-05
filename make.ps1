# Windows equivalent of the Makefile:  .\make.ps1 <target>
param([Parameter(Position = 0)][string]$Target = "help", [string]$Case = "DKT-002", [string]$TuneClass = "synthetic",
      [string]$BtClass = "synthetic", [string]$BtPrefix = "SYN-P")
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
function Rci { & $Py -m rci.cli @args; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
function Run($exe) { & $exe @args; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
$env:PYTHONIOENCODING = "utf-8"
Push-Location $Root
try {
    switch ($Target) {
        "venv"        { Run python -m venv .venv }
        "install"     { Run $Py -m pip install -e "services/api[dev]" }
        "env"         { Rci init-env }
        "up"          { Run docker compose up -d db redis azurite }
        "down"        { Run docker compose down }
        "migrate"     { Rci migrate }
        "seed"        { Rci seed }
        "setup"       { & $PSCommandPath venv; & $PSCommandPath install; & $PSCommandPath env; & $PSCommandPath up; Start-Sleep 5; & $PSCommandPath migrate; & $PSCommandPath seed }
        "samples"     { Run $Py eval/samples/generate_samples.py }
        "ingest"      { Rci migrate; Rci seed; Rci ingest eval/samples/dockets --data-class synthetic }
        "check"       { Run $Py eval/check_ingest.py }
        "idempotency" { Run $Py eval/check_ingest.py --rerun }
        "test"        { Push-Location services/api; try { Run $Py -m pytest -q } finally { Pop-Location } }
        "ocr-bench"   { Run $Py eval/ocr_bench/bench.py }
        "schemas"     { Rci export-schemas }
        "api"         { Push-Location services/api; try { Run $Py -m uvicorn rci.api.main:app --reload --port 8000 } finally { Pop-Location } }
        "worker"      { Push-Location services/worker; try { Run $Py -m arq worker.WorkerSettings } finally { Pop-Location } }
        "web"         { Push-Location apps/web; try { Run npm.cmd install; Run npm.cmd run dev } finally { Pop-Location } }
        "app-up"      { Run docker compose --profile app up -d --build }
        "users"       { foreach ($u in @(@("admin@rci.local","admin","Admin"), @("reviewer1@rci.local","reviewer","Reviewer-A"), @("reviewer2@rci.local","reviewer","Reviewer-B"), @("officer@rci.local","officer","Officer"))) { Rci create-user $u[0] $u[1] --name $u[2] --password dev-password-1 --exist-ok } }
        "rules-gaps"  { Rci rules-gaps }
        "report"      { Rci report extraction --save --out var/out/extraction_accuracy.md; Rci report agreement --save --out var/out/agreement.md }
        "web-check"   { Push-Location apps/web; try { Run npm.cmd install; Run npx.cmd tsc --noEmit; Run npm.cmd run build } finally { Pop-Location } }
        "precedents"  { Rci seed-precedents --n 40 }
        "analyse"     { Rci analyse $Case --out "var/out/$Case.analysis.json" }
        "fee-golden"  { Rci fee-golden --save --out var/out/fee_golden.md }
        "tune"        { Rci tune --save --data-class $TuneClass --out var/out/tuning.md }
        "templates"   { Rci build-templates }
        "clauses"     { Rci clauses mine }
        "drafts"      { Rci draft $Case }
        "holdouts"    { Rci holdouts select --data-class $BtClass --prefix $BtPrefix }
        "backtest"    { Rci backtest --data-class $BtClass --prefix $BtPrefix }
        "backtest-gate"     { Run $Py eval/backtest/ci_backtest.py }
        "backtest-baseline" { Run $Py eval/backtest/ci_backtest.py --update-baseline }
        default       { "targets: setup up down migrate seed samples ingest check idempotency test ocr-bench schemas api worker web app-up users rules-gaps report web-check precedents analyse fee-golden tune templates clauses drafts holdouts backtest backtest-gate backtest-baseline" }
    }
} finally { Pop-Location }
