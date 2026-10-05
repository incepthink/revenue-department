"""Drive the new-case intake end to end in a browser: the officer fills "New case" with one file per document slot
(pages of the DKT-002 sample, plus a photo-style PNG for the Aadhaar slot), lands on the document-check page,
watches the per-document progress and reads the verdict, the actions, the facts, the checks and the document cards.

Needs: `make api`, `make web`, `make users` and Playwright (`pip install playwright`; PW_CHROMIUM=<chrome.exe> reuses
an installed browser). Works with or without an LLM configured (without one the page says the reading was partial).

    python eval/e2e/e2e_intake.py

Screenshots go to var/e2e_shots/intake_*.png.
"""
import os
import re
import time
from pathlib import Path

import pymupdf
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://localhost:3000"
SHOTS = ROOT / "var" / "e2e_shots"
TMP = ROOT / "var" / "e2e_tmp"
DOCKET = ROOT / "eval" / "samples" / "dockets" / "DKT-002"
log = lambda *a: print(*a, flush=True)  # noqa: E731


def pages_of(filename: str, *indices: int, name: str) -> Path:
    src, out = pymupdf.open(DOCKET / filename), pymupdf.open()
    for i in indices:
        out.insert_pdf(src, from_page=i, to_page=i)
    path = TMP / name
    out.save(path)
    return path


def photo_of(filename: str, index: int, name: str) -> Path:
    """A page as a PNG: no text layer, like a phone photo of an ID card."""
    path = TMP / name
    pymupdf.open(DOCKET / filename)[index].get_pixmap(dpi=110).save(path)
    return path


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    files = {
        "satbara": pages_of("03_karyalay.pdf", 0, name="satbara.pdf"),  # really a site-inspection report
        "property_card": pages_of("02_kagadpatre.pdf", 0, name="property_card.pdf"),
        "grant": pages_of("01_arj.pdf", 1, 2, name="lease_deed.pdf"),
        "application": pages_of("01_arj.pdf", 0, name="application.pdf"),
        "aadhaar": photo_of("02_kagadpatre.pdf", 2, name="aadhaar.png"),
        "tax_receipts": pages_of("03_karyalay.pdf", 1, name="tax_receipt.pdf"),
        "index_ii": pages_of("03_karyalay.pdf", 2, name="index_ii.pdf"),
    }
    name = f"E2E intake {int(time.time()) % 100000}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        p = browser.new_context().new_page()
        p.set_viewport_size({"width": 1440, "height": 950})
        p.goto(f"{BASE}/login")
        p.fill("#email", "officer@rci.local")
        p.fill("#password", "dev-password-1")
        p.click("button:has-text('Sign in')")
        expect(p.locator("h1", has_text="Cases")).to_be_visible()
        p.wait_for_load_state("networkidle")  # the form is a client component: click after hydration

        p.click("button:has-text('+ New Case')")
        p.fill("#case-name", name)
        for slot, path in files.items():
            p.set_input_files(f"#doc-{slot}", str(path))
        p.screenshot(path=SHOTS / "intake_01_form.png", full_page=True)
        p.click("button.primary:has-text('Create case')")
        p.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/intake$"), timeout=60000)
        log("form submitted →", p.url)

        expect(p.locator("h2", has_text="Checking the documents")).to_be_visible(timeout=15000)
        expect(p.locator(".stage-list li")).to_have_count(len(files))
        p.screenshot(path=SHOTS / "intake_02_progress.png", full_page=True)
        log("progress: one row per uploaded document")

        expect(p.locator(".verdict")).to_be_visible(timeout=300000)
        verdict = p.locator(".verdict-label").inner_text()
        log("verdict:", verdict, "—", p.locator(".verdict-headline").inner_text())
        expect(p.locator("h3", has_text="What the applicant needs to do")).to_be_visible()
        expect(p.locator("h3", has_text="What we found")).to_be_visible()
        expect(p.locator(".doc-card")).to_have_count(len(files))
        # the photo has no text layer: without OCR it cannot be read, and the page says so
        aadhaar = p.locator(".doc-card", has_text="Identity proof")
        expect(aadhaar.locator(".pill")).to_have_text(re.compile("Unreadable|Looks right|Wrong document|Has problems"))
        p.screenshot(path=SHOTS / "intake_03_results.png", full_page=True)

        link = p.locator(".doc-card a.cite").first
        href = link.get_attribute("href")
        assert href and re.search(r"/cases/[0-9a-f-]+/source/\d+", href), href
        log("results: verdict, actions, facts, checks and", len(files), "document cards; page links →", href)
        browser.close()
    log("E2E intake: OK")


if __name__ == "__main__":
    main()
