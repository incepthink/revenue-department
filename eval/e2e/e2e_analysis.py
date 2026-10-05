"""Drive the Case Analysis screen end to end: an officer opens an ingested incoming docket, runs the analysis, checks
the four outputs (precedents with score breakdown, gaps filterable by basis, next-steps timeline, fee trace), follows a
citation to its source page, and time-travels the precedent pool; then the Reports page shows the fee golden check
and the tuning run.

Needs: `make api`, `make web`, `make users`, `make precedents` (synthetic pool), an ingested case, and Playwright in any
Python env (`pip install playwright`; PW_CHROMIUM=<chrome.exe> reuses an installed browser).

    python eval/e2e/e2e_analysis.py DKT-002

Screenshots go to var/e2e_shots/analysis_*.png.
"""
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:3000"
SHOTS = Path(__file__).resolve().parents[2] / "var" / "e2e_shots"
SHOTS.mkdir(parents=True, exist_ok=True)
PW = "dev-password-1"
CASE_REF = sys.argv[1] if len(sys.argv) > 1 else "DKT-002"
log = lambda *a: print(*a, flush=True)  # noqa: E731


def login(ctx, email):
    p = ctx.new_page()
    p.set_viewport_size({"width": 1500, "height": 950})
    p.goto(f"{BASE}/")
    expect(p).to_have_url(re.compile(r"/login"))
    p.fill("#email", email)
    p.fill("#password", PW)
    p.click("button:has-text('Sign in')")
    expect(p.locator("h1", has_text="Cases")).to_be_visible()
    return p


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        ctx = browser.new_context()
        p = login(ctx, "officer@rci.local")

        # cases list → Analyse
        row = p.locator("tr", has=p.locator("td >> nth=0", has_text=re.compile(rf"^{re.escape(CASE_REF)}$")))
        row.locator("a.btn", has_text="Analyse").click()
        expect(p).to_have_url(re.compile(r"/analysis$"))
        p.locator("button.primary", has_text=re.compile("analysis")).click()
        expect(p.locator("h2", has_text="Matched precedents")).to_be_visible(timeout=60000)
        log("analysis ran")

        # 1. precedents: ranked with reasons; expand the first for the feature breakdown
        prec = p.locator("section", has=p.locator("h2", has_text="Matched precedents"))
        rows = prec.locator("tbody > tr")
        assert rows.count() >= 3, "expected precedents"
        rows.first.click()
        expect(prec.locator("table.compact")).to_be_visible()
        expect(prec.locator("table.compact tbody tr")).to_have_count(10)
        p.screenshot(path=SHOTS / "analysis_01_precedents.png", full_page=True)

        # 2. gaps: filter by basis
        gaps = p.locator("section", has=p.locator("h2", has_text="Gaps"))
        total = gaps.locator(".gap").count()
        assert total >= 3, f"expected gaps, got {total}"
        gaps.locator(".seg button", has_text="Statutory").click()
        statutory = gaps.locator(".gap").count()
        assert 0 < statutory < total, (statutory, total)
        gaps.locator(".seg button", has_text=re.compile(r"^Practice \(\d+\)$")).click()
        assert gaps.locator(".gap").count() == total - statutory
        gaps.locator(".seg button", has_text="All").click()
        expect(gaps.locator(".gap")).to_have_count(total)
        assert gaps.locator("a.cite").count() >= 3, "gaps should cite pages"
        gaps.screenshot(path=SHOTS / "analysis_02_gaps.png")
        log(f"gaps: {total} ({statutory} statutory)")

        # 3. steps timeline and 4. fee trace
        steps = p.locator("section", has=p.locator("h2", has_text="Next steps"))
        assert steps.locator("li.step.next, li.step").count() >= 3
        steps.screenshot(path=SHOTS / "analysis_03_steps.png")
        fee = p.locator("section", has=p.locator("h2", has_text="Fee trace"))
        expect(fee.locator("tbody tr").first).to_be_visible()
        assert fee.locator("tbody tr").count() >= 5
        fee.screenshot(path=SHOTS / "analysis_04_fee.png")

        # a citation opens its source page with the snippet highlighted
        with ctx.expect_page() as newp:
            gaps.locator("a.cite").first.click()
        src = newp.value
        src.set_viewport_size({"width": 1500, "height": 950})
        expect(src.locator("h1", has_text="page")).to_be_visible()
        expect(src.locator(".ln.cited").first).to_be_visible()
        src.screenshot(path=SHOTS / "analysis_05_source.png")
        src.close()
        log("citation → source page ok")

        # time travel: a pool as of 2019 is empty
        p.fill("input[type=date]", "2019-01-01")
        p.locator("button.primary", has_text=re.compile("analysis")).click()
        expect(p.locator("text=No approved precedent passed the filters.")).to_be_visible(timeout=60000)
        p.fill("input[type=date]", "")
        p.locator("button.primary", has_text=re.compile("analysis")).click()
        expect(prec.locator("tbody > tr").first).to_be_visible(timeout=60000)
        log("time travel ok")

        # reports: fee golden + tuning
        p.goto(f"{BASE}/reports")
        expect(p.locator("h2", has_text="Fee golden check")).to_be_visible()
        expect(p.locator("text=unexplained 0")).to_be_visible()
        expect(p.locator("h2", has_text="Leave-one-out tuning")).to_be_visible()
        p.locator("section", has=p.locator("h2", has_text="Fee golden check")).screenshot(path=SHOTS / "analysis_06_reports.png")
        log("reports ok")
        browser.close()
    log("E2E analysis: PASS")


if __name__ == "__main__":
    main()
