"""Drive the review UI end to end: two reviewers double-review an ingested docket in the browser, then approve;
then check the case page, reports, rules admin (resolve tool), ASR validation, users page and officer read-only access.

Needs: `make api`, `make web` (or `next start`), `make users`, a freshly ingested *draft* case (the first 10 reviewed
cases are double-reviewed), and Playwright in any Python env (`pip install playwright && playwright install chromium`).

    python eval/e2e/e2e_review.py <CASE_REF> eval/samples/asr/asr_synthetic_pune.csv

Screenshots go to var/e2e_shots/.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:3000"
SHOTS = Path(__file__).resolve().parents[2] / "var" / "e2e_shots"
SHOTS.mkdir(parents=True, exist_ok=True)
PW = "dev-password-1"
CASE_REF = sys.argv[1] if len(sys.argv) > 1 else "DEMO-DKT-002"
log = lambda *a: print(*a, flush=True)  # noqa: E731


def login(ctx, email):
    p = ctx.new_page()
    p.set_viewport_size({"width": 1600, "height": 950})
    p.goto(f"{BASE}/")
    expect(p).to_have_url(re.compile(r"/login"))
    p.fill("#email", email)
    p.fill("#password", PW)
    p.click("button:has-text('Sign in')")
    expect(p.locator("h1", has_text="Cases")).to_be_visible()
    return p


def open_review(p):
    p.goto(f"{BASE}/")
    row = p.locator("tr", has_text=CASE_REF)
    row.locator("a.btn").click()
    expect(p).to_have_url(re.compile(r"/review$"))


def wait_saved(p):
    p.wait_for_timeout(150)
    expect(p.locator("text=saving…")).to_have_count(0, timeout=20000)


def rail(p, text, n=0):
    item = items_of(p, text).nth(n)
    item.click()
    wait_saved(p)
    return item


def items_of(p, text):
    return p.locator(".rail-item", has=p.locator(".t", has_text=text))


def tab(p, name):
    p.locator(".tabs button", has_text=name).click()


def accept_remaining_everywhere(p):
    n = p.locator(".rail-item").count()
    for i in range(n):
        p.locator(".rail-item").nth(i).click()
        tab(p, "Fields")
        btn = p.locator("button:has-text('Accept remaining')")
        if btn.count():
            btn.click()
            wait_saved(p)


def map_queries(p, make_new_code=False):
    tab(p, "Queries")
    items = p.locator(".qitem")
    for i in range(items.count()):
        it = items.nth(i)
        if "mapped" in (it.get_attribute("class") or "").split():
            continue
        if make_new_code and i == items.count() - 1:
            it.locator("button:has-text('+ New code')").click()
            m = p.locator(".modal")
            m.locator("input").nth(0).fill("ENCROACHMENT_CLARIFICATION")
            m.locator("input").nth(1).fill("Clarification on encroachment")
            m.locator("input").nth(2).fill("अतिक्रमणाबाबत खुलासा")
            m.locator("select").select_option("legal_condition")
            m.locator("button:has-text('Create and map')").click()
            p.wait_for_timeout(800)
            if p.locator(".modal .error").count():  # code exists from an earlier run: pick it in the type-ahead
                m.locator("button:has-text('Cancel')").click()
                it.locator(".typeahead input").fill("ENCROACH")
                it.locator(".typeahead .menu div").first.dispatch_event("mousedown")
            expect(p.locator(".modal")).to_have_count(0)
            wait_saved(p)
            continue
        sug = it.locator(".chip.suggest")
        if sug.count():
            sug.first.click()
        else:
            it.locator("button:has-text('Not a requirement')").click()
        wait_saved(p)
    expect(p.locator(".qitem.unmapped")).to_have_count(0)


def submit(p):
    p.locator(".review-bar button", has_text=re.compile(r"^(Re-)?[Ss]ubmit$")).click()
    p.locator(".modal button:has-text('Submit')").click()
    expect(p.locator(".toast", has_text="Submitted")).to_be_visible()


with sync_playwright() as pw:
    import os
    # PW_CHROMIUM: an already-installed chromium / headless-shell executable (skips `playwright install`)
    browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    # ------------------------------------------------------------------ reviewer A
    a = login(browser.new_context(), "reviewer1@rci.local")
    open_review(a)
    a.screenshot(path=SHOTS / "01_start.png")
    a.wait_for_selector(".review-grid, button:has-text('Start review')")
    if a.locator("button:has-text('Start review')").count():
        a.click("button:has-text('Start review')")
    expect(a.locator(".review-grid")).to_be_visible()
    expect(a.locator(".review-bar")).to_contain_text("double review")
    log("A: review started (double mode)")

    # exhibit rail: split the lease deed, merge it back, move MEMBER_LIST to query_response
    rail(a, "LEASE_SANAD")
    a.locator(".rail-item.active button:has-text('Split')").click()
    wait_saved(a)
    expect(items_of(a, "LEASE_SANAD")).to_have_count(2)
    a.screenshot(path=SHOTS / "02_split.png")
    rail(a, "LEASE_SANAD", 0)
    a.locator(".rail-item.active button:has-text('with next exhibit')").click()
    wait_saved(a)
    expect(items_of(a, "LEASE_SANAD")).to_have_count(1)
    rail(a, "MEMBER_LIST")
    a.locator(".rail-item.active select[aria-label='Stage']").select_option("query_response")
    wait_saved(a)
    expect(a.locator(".rail-item.active .meta")).to_contain_text("query_response")
    log("A: split / merge / stage OK")

    # page viewer: masked image + OCR boxes; cite a line; toggle OCR text view and zoom
    rail(a, "PROPERTY_CARD")
    img = a.locator(".page-wrap img")
    expect(img).to_be_visible()
    a.wait_for_function("document.querySelector('.page-wrap img').naturalWidth > 0")
    boxes = a.locator(".page-wrap rect.line")
    expect(boxes.first).to_be_attached()
    log(f"A: page image loaded, {boxes.count()} OCR boxes")
    area_line = a.locator(".page-wrap rect.line", has=a.locator("title", has_text="क्षेत्र"))
    area_line.first.dispatch_event("click")
    expect(a.locator(".toast", has_text="Cited")).to_be_visible()
    a.locator("button:has-text('+')").last.click()
    a.locator("button.small:has-text('%')").click()
    a.locator("button:has-text('OCR text')").click()
    expect(a.locator(".ocr-text .ln").first).to_be_visible()
    a.screenshot(path=SHOTS / "03_ocr_text.png")
    a.locator("button:has-text('Image')").click()

    # card form: edit area with a page reference (cited line prefilled), accept tenure
    tab(a, "Fields")
    area = a.locator(".field", has=a.locator(".fname", has_text=re.compile(r"^area_sqm")))
    area.locator("button:has-text('Edit')").click()
    area.locator(".editor input").first.fill("805.5")
    expect(area.locator(".editor input").nth(1)).to_have_value(re.compile("क्षेत्र"))
    area.locator("button:has-text('Save')").click()
    wait_saved(a)
    expect(area.locator(".fval")).to_have_text("805.50")
    expect(area).to_contain_text("edited")
    a.screenshot(path=SHOTS / "04_field_edit.png")
    log("A: field edit with page reference OK")

    accept_remaining_everywhere(a)
    map_queries(a, make_new_code=True)
    a.screenshot(path=SHOTS / "05_queries.png")
    log("A: queries mapped (incl. a new code)")

    tab(a, "Requirements")
    a.locator("select[aria-label='OCCUPANCY_CERTIFICATE status']").select_option("waived")
    wait_saved(a)
    tab(a, "Events")
    a.click("button:has-text('+ event')")
    last = a.locator(".tab-body table tbody tr").last
    last.locator("select[aria-label='event']").select_option("QUERY_REPLIED")
    last.locator("input[type='date']").fill("2025-04-02")
    ql = last.locator("select[aria-label='exhibit'] option", has_text="QUERY_LETTER").first.get_attribute("value")
    last.locator("select[aria-label='exhibit']").select_option(ql)
    a.click("button:has-text('Save timeline')")
    wait_saved(a)
    expect(a.locator(".tab-body")).to_contain_text("Edited timeline")
    a.screenshot(path=SHOTS / "06_events.png")
    expect(a.locator(".review-bar button:has-text('Approve')")).to_be_disabled()  # double review: B has not submitted
    submit(a)
    log("A: submitted")

    # ------------------------------------------------------------------ reviewer B (independent)
    b = login(browser.new_context(), "reviewer2@rci.local")
    open_review(b)
    expect(b.locator(".card")).to_contain_text("second reviewer")
    b.click("button:has-text('Start review')")
    expect(b.locator(".review-grid")).to_be_visible()
    accept_remaining_everywhere(b)
    map_queries(b)
    submit(b)
    log("B: submitted")

    # ------------------------------------------------------------------ A sees disagreements, approves
    a.reload()
    expect(a.locator(".review-grid")).to_be_visible()
    tab(a, "Checks")
    expect(a.locator(".tab-body")).to_contain_text("Double-review disagreements")
    expect(a.locator(".tab-body table").last).to_contain_text("area_sqm")
    a.screenshot(path=SHOTS / "07_disagreements.png", full_page=True)
    approve = a.locator(".review-bar button:has-text('Approve')")
    expect(approve).to_be_enabled()
    approve.click()
    a.locator(".modal button:has-text('Approve')").click()
    expect(a.locator(".toast", has_text="Approved")).to_be_visible(timeout=30000)
    expect(a.locator(".review-bar")).to_contain_text("read-only")
    a.screenshot(path=SHOTS / "08_approved.png")
    log("A: approved ->", a.locator(".review-bar .badge").first.inner_text())

    a.locator(".review-bar a.title").click()
    expect(a.locator("text=Facts summary (approved)")).to_be_visible()
    a.screenshot(path=SHOTS / "09_case_page.png", full_page=True)
    a.goto(f"{BASE}/reports")
    expect(a.locator("h2", has_text="extraction accuracy")).to_be_visible()
    expect(a.locator("main")).to_contain_text("PROPERTY_CARD.area_sqm")
    expect(a.locator("main")).to_contain_text(CASE_REF)
    a.screenshot(path=SHOTS / "10_reports.png", full_page=True)
    log("reports OK")

    # ------------------------------------------------------------------ admin + officer
    ad = login(browser.new_context(), "admin@rci.local")
    ad.goto(f"{BASE}/admin/rules")
    expect(ad.locator("main")).to_contain_text("RULES_2019")
    ad.screenshot(path=SHOTS / "11_rules.png", full_page=True)
    ad.locator("a", has_text="RULES_2019").click()
    expect(ad.locator("h2", has_text="What the AI checks")).to_be_visible()
    ad.screenshot(path=SHOTS / "12_rule_set.png", full_page=True)
    ad.goto(f"{BASE}/admin/asr")
    ad.set_input_files("input[type='file']", str(Path(sys.argv[2]) if len(sys.argv) > 2 else "asr.csv"))
    ad.click("button:has-text('Validate')")
    expect(ad.locator(".success, .error").first).to_be_visible()
    ad.screenshot(path=SHOTS / "13_asr.png", full_page=True)
    log("ASR validate:", ad.locator(".success, .error").first.inner_text()[:120])
    ad.goto(f"{BASE}/admin/users")
    expect(ad.locator("main")).to_contain_text("reviewer2@rci.local")

    of = login(browser.new_context(), "officer@rci.local")
    open_review(of)
    expect(of.locator(".review-grid")).to_be_visible()  # approved review, read-only
    expect(of.locator("text=unmask PII")).to_have_count(0)
    expect(of.locator(".review-bar button:has-text('Approve')")).to_have_count(0)
    of.goto(f"{BASE}/admin/users")
    expect(of.locator(".error")).to_be_visible()
    log("officer read-only OK")
    browser.close()
    log("E2E PASS")
