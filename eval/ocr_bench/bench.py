"""OCR benchmark: character error rate (CER) per engine per exhibit type.

    python eval/ocr_bench/bench.py --engines text_layer,tesseract,azure_di,llm_vision [--manifest ...] [--save-db]

Inputs: one or more manifests (default: manifest.json + real/manifest.json if present). Each page entry:
    {"id", "pdf" (relative to eval/), "page_index", "exhibit_type", "transcription" | "transcription_file",
     "data_class": "synthetic" | "real" (default synthetic)}
The page is rasterised at the pipeline DPI and sent to each engine. CER = Levenshtein(ref, hyp) / len(ref)
after NFC + whitespace normalisation. Engines that are not configured are skipped (reported), and the
data-residency guards apply: real pages never reach a non-approved engine.

Note: on the synthetic samples `text_layer` is trivially ~0 (the text layer *is* the ground truth); it
is a sanity check of the harness. The meaningful numbers come from image engines and real pages.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import pymupdf

from rci.config import get_settings
from rci.pipeline.normalise import normalise_text

EVAL = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", normalise_text(s)).strip()


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> tuple[float, int, int]:
    r, h = norm(ref), norm(hyp)
    d = levenshtein(r, h)
    return (d / max(1, len(r)), d, len(r))


def build_engines(names: list[str]):
    from rci.adapters.llm import get_llm_client
    from rci.adapters.ocr import AzureDocumentIntelligenceEngine, LlmVisionEngine, TesseractEngine

    s = get_settings()
    engines, skipped = {}, {}
    for n in names:
        try:
            if n == "text_layer":
                engines[n] = "text_layer"
            elif n == "tesseract":
                engines[n] = TesseractEngine()
            elif n == "azure_di":
                engines[n] = AzureDocumentIntelligenceEngine(s)
            elif n == "llm_vision":
                llm = get_llm_client(s)
                if not llm.available:
                    raise RuntimeError("no LLM provider configured")
                engines[n] = LlmVisionEngine(llm)
            else:
                raise RuntimeError("unknown engine")
        except Exception as e:
            skipped[n] = str(e)
    return engines, skipped


def load_pages(manifests: list[Path]) -> list[dict]:
    pages = []
    for m in manifests:
        data = json.loads(m.read_text(encoding="utf-8"))
        for p in data["pages"]:
            if "transcription_file" in p:
                p["transcription"] = (m.parent / p["transcription_file"]).read_text(encoding="utf-8")
            pages.append(p)
    return pages


def run(pages: list[dict], engines: dict, dpi: int) -> list[dict]:
    from rci.pipeline.runner import _text_layer_result

    results = []
    for p in pages:
        doc = pymupdf.open(EVAL / p["pdf"])
        page = doc[p["page_index"]]
        png = page.get_pixmap(dpi=dpi).tobytes("png")
        for name, eng in engines.items():
            t0 = time.perf_counter()
            try:
                out = _text_layer_result(page) if eng == "text_layer" else eng.recognize(png, data_class=p.get("data_class", "synthetic"))
            except Exception as e:
                results.append({"sample_id": p["id"], "engine": name, "exhibit_type": p["exhibit_type"], "error": str(e)[:200]})
                continue
            c, d, n = cer(p["transcription"], out.text)
            results.append({"sample_id": p["id"], "engine": name, "exhibit_type": p["exhibit_type"], "cer": round(c, 4),
                            "edit_distance": d, "ref_chars": n, "mean_confidence": out.mean_confidence,
                            "latency_ms": int((time.perf_counter() - t0) * 1000)})
    return results


def summarise(results: list[dict]) -> str:
    table: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in results:
        if "cer" in r:
            table[(r["engine"], r["exhibit_type"])].append(r["cer"])
            table[(r["engine"], "ALL")].append(r["cer"])
    engines = sorted({e for e, _ in table})
    types = sorted({t for _, t in table if t != "ALL"}) + ["ALL"]
    lines = ["| exhibit type | " + " | ".join(engines) + " |", "|---|" + "---|" * len(engines)]
    for t in types:
        cells = []
        for e in engines:
            v = table.get((e, t))
            cells.append(f"{statistics.mean(v):.2%} (n={len(v)})" if v else "–")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def save_db(results: list[dict], config: dict) -> str:
    import datetime as dt

    from rci.db.models import EvalRun, OcrBenchmarkResult
    from rci.db.session import session_scope

    with session_scope() as s:
        run_row = EvalRun(kind="ocr_bench", config=config, status="done", finished_at=dt.datetime.now(dt.timezone.utc),
                          metrics={"pages": len({r["sample_id"] for r in results})})
        s.add(run_row)
        s.flush()
        for r in results:
            if "cer" in r:
                s.add(OcrBenchmarkResult(run_id=run_row.id, sample_id=r["sample_id"], exhibit_type=r["exhibit_type"],
                                         engine=r["engine"], ref_chars=r["ref_chars"], edit_distance=r["edit_distance"],
                                         cer=r["cer"], mean_confidence=r["mean_confidence"], latency_ms=r["latency_ms"]))
        return str(run_row.id)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="text_layer,tesseract,azure_di,llm_vision")
    ap.add_argument("--manifest", action="append", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--save-db", action="store_true")
    ap.add_argument("--out", default=str(get_settings().output_dir / "ocr_bench"))
    args = ap.parse_args()

    manifests = [Path(m) for m in args.manifest] if args.manifest else \
        [p for p in (HERE / "manifest.json", HERE / "real" / "manifest.json") if p.exists()]
    pages = load_pages(manifests)[: args.limit]
    engines, skipped = build_engines(args.engines.split(","))
    for n, why in skipped.items():
        print(f"skipped engine {n}: {why}")
    results = run(pages, engines, get_settings().raster_dpi)
    table = summarise(results)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"skipped": skipped, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(f"# OCR benchmark — CER by engine and exhibit type\n\n{table}\n\nSkipped: {skipped}\n", encoding="utf-8")
    print(table)
    errors = [r for r in results if "error" in r]
    if errors:
        print(f"{len(errors)} engine errors (see results.json)")
    if args.save_db:
        print("eval_run", save_db(results, {"engines": list(engines), "manifests": [str(m) for m in manifests]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
