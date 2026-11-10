"""Browser tests against the running dev stack: `scripts/dev.sh test-browser`.

Drives real Chromium through the UI on a phone viewport and renders the Grafana
dashboard, reaching the containers directly on the dev network. Screenshots go
to .dev/screenshots. Everything it creates has a marker vendor and is deleted
again, so test expenses you entered by hand are left alone.
"""

import os
import re
import sys
import time

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_PATH", "/expenses-dev")
UI = f"http://expenses-dev:8080{BASE}/"
GRAFANA = f"http://dev-grafana:3000{BASE}/grafana/"
OUT = os.environ.get("OUT", "/out")
MARKER = "BROWSER-TEST"
INVOICE = "https://example.com/invoice-browser-test.pdf"
# Writes to a real OneDrive happen only in this folder (scripts/dev.sh onedrive-link points here).
TEST_FOLDER = "Expenses test"
REQUIRE_ONEDRIVE = os.environ.get("REQUIRE_ONEDRIVE") == "1"
passed, failed, skipped = 0, [], []


class Skip(Exception):
    """This check needs something the stack doesn't have. Reported, and a failure with --onedrive."""


def step(name, fn, *args):
    global passed
    try:
        fn(*args)
        passed += 1
        print(f"ok   {name}")
    except Skip as why:
        if REQUIRE_ONEDRIVE:
            failed.append(name)
            print(f"FAIL {name}\n     required, but: {why}")
        else:
            skipped.append(name)
            print(f"SKIP {name}\n     {why}")
    except Exception as exc:  # keep going: report every failure in one run
        failed.append(name)
        print(f"FAIL {name}\n     " + "\n     ".join(str(exc).splitlines()[:12]))


def shot(page, name):
    page.screenshot(path=f"{OUT}/{name}.png", full_page=True)


def add_expense(page, amount="123.45", category="Tyres"):
    page.goto(UI + "new")
    page.select_option("select[name=category_id]", label=category)
    page.fill("input[name=amount]", amount)
    page.fill("input[name=vendor]", MARKER)
    page.fill("input[name=invoice_url]", INVOICE)
    page.click("button[type=submit].button")
    page.wait_for_url(re.compile(r"msg=saved"))


def test_duplicate_is_asked_about_before_saving(page):
    """Adding the same car/amount/date twice: the page asks first (inline, nothing sent), and
    Save anyway saves the second."""
    def fill():
        page.goto(UI + "new")
        page.select_option("select[name=category_id]", label="Tyres")
        page.fill("input[name=amount]", "246.81")
        page.fill("input[name=vendor]", MARKER)
        page.click("button[type=submit].button")
    fill()
    page.wait_for_url(re.compile(r"msg=saved"))                 # nothing like it yet: saves straight away
    fill()
    expect(page.locator("#duplicate-warning")).to_be_visible()
    expect(page.locator("#duplicate-list li")).to_have_count(1)
    assert "246.81" in page.inner_text("#duplicate-list")
    assert "/new" in page.url and "msg=" not in page.url        # still on the form, nothing saved
    shot(page, "duplicate-warning")
    page.click("#duplicate-save")
    page.wait_for_url(re.compile(r"msg=saved"))
    page.goto(UI)
    assert page.locator("tbody tr", has_text=MARKER).count() == 2


def test_duplicate_warning_can_be_dismissed(page):
    page.goto(UI + "new")
    page.select_option("select[name=category_id]", label="Tyres")
    page.fill("input[name=amount]", "246.81")
    page.fill("input[name=vendor]", MARKER)
    page.click("button[type=submit].button")
    expect(page.locator("#duplicate-warning")).to_be_visible()
    page.click("#duplicate-cancel")
    expect(page.locator("#duplicate-warning")).to_be_hidden()
    assert page.input_value("input[name=vendor]") == MARKER      # the form is untouched


def test_large_pdf_is_accepted(page):
    """Uploads over 500 KB are spooled to a temp file. The container's root is read-only, so
    this fails with 'No usable temporary directory' unless TMPDIR points at the tmpfs."""
    big = b"%PDF-1.4\n" + b"0" * (900 * 1024) + b"\n%%EOF\n"
    resp = page.request.post(UI + "api/prefill", headers={"Origin": "http://expenses-dev:8080"}, multipart={
        "invoice_file": {"name": "large.pdf", "mimeType": "application/pdf", "buffer": big}})
    assert resp.status == 200, f"HTTP {resp.status}: a large PDF upload crashed the app"
    assert set(resp.json()) >= {"date", "amount", "vendor", "category_id", "odometer_km"}


def test_import_page_shows_progress(page):
    """The import page opens at once and reports its progress, instead of a blank wait."""
    page.goto(UI + "onedrive")
    if "Connected as" not in page.inner_text("main") or not page.query_selector("text=Import existing invoices"):
        raise Skip("dev stack isn't connected to OneDrive with a car folder")
    car = re.search(r"onedrive/import/(\d+)", page.get_attribute("text=Import existing invoices", "href")).group(1)
    started = time.time()
    page.goto(UI + f"onedrive/import/{car}", wait_until="domcontentloaded")
    assert time.time() - started < 5, "the import page should open at once, not wait for OneDrive"
    expect(page.locator("#import-status-text")).to_be_visible()
    # It ends with a summary (and the form ready) or a plain message saying why there is nothing to do.
    expect(page.locator("#import-status-text")).to_have_text(
        re.compile(r"^(Read \d+ PDF|Every PDF|No PDFs)"), timeout=240000)
    if page.locator("#import-form").is_visible():
        assert page.locator("#import-form button[type=submit]").is_enabled()
        shot(page, "import-progress")
        if in_test_folder(page, UI + f"onedrive/import/{car}"):
            page.goto(UI + f"onedrive/import/{car}", wait_until="domcontentloaded")
            expect(page.locator("#import-status-text")).to_have_text(re.compile(r"^Read \d+ PDF"), timeout=240000)
            assert MARKER in page.content(), "the import preview should list the PDF the upload check left there"
            check_skip_prompt(page)


def check_skip_prompt(page):
    """Pressing Import with files left unticked asks whether to skip them; OK ticks their Skip boxes.
    The POST is aborted, so nothing is saved or skipped in the dev database."""
    boxes = page.locator("#import-form input[name^=include_]")
    for i in range(boxes.count()):
        boxes.nth(i).uncheck()
    page.locator("#import-form input[name^=skip_]").first.check()
    assert page.inner_text("#import-form button[type=submit]") == "Skip 1", "the button should say it only skips"
    page.route("**/onedrive/import/*", lambda route: route.abort() if route.request.method == "POST" else route.continue_())
    asked = []
    page.once("dialog", lambda d: (asked.append(d.message), d.accept()))
    page.click("#import-form button[type=submit]")
    page.unroute("**/onedrive/import/*")
    assert asked and "ticked to import" in asked[0], "Import with nothing ticked should offer to skip the files"
    skips = page.locator("#import-form input[name^=skip_]")
    assert all(skips.nth(i).is_checked() for i in range(skips.count())), "accepting should tick every Skip box"


def in_test_folder(page, back_to=None):
    """Whether every car points inside the test folder, the only place a test may write."""
    page.goto(UI + "onedrive")
    paths = page.locator("ul.folders small").all_inner_texts()
    if back_to:
        page.goto(back_to, wait_until="domcontentloaded")
    return bool(paths) and all(p.strip().startswith("/" + TEST_FOLDER) for p in paths)


def test_real_upload(page):
    """Saves an expense with a PDF, through the real OneDrive, into the test folder only.
    The expense is deleted again; the file stays (the app has no delete) for the import check."""
    page.goto(UI + "onedrive")
    if not page.locator("button:has-text('Disconnect')").count():
        raise Skip("dev stack isn't connected to OneDrive (scripts/dev.sh onedrive-link)")
    if not in_test_folder(page):
        raise Skip(f"cars aren't pointed at /{TEST_FOLDER}, so nothing was written")
    page.goto(UI + "new")
    page.set_input_files("input[name=invoice_file]", {
        "name": "browser-test.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF\n"})
    page.wait_for_load_state("networkidle")
    page.select_option("select[name=category_id]", label="Tyres")
    page.fill("input[name=amount]", "371.93")
    page.fill("input[name=vendor]", MARKER)
    page.fill("input[name=invoice_url]", "")
    page.click("button[type=submit].button")
    page.wait_for_url(re.compile(r"msg=saved"), timeout=60000)
    page.goto(UI)
    link = page.locator("tbody tr", has_text=MARKER).locator("a[href^='https://']").first.get_attribute("href")
    assert "example.com" not in link, "the invoice link should be the uploaded file's OneDrive address"
    remove_marked(page)


def test_onedrive_picker(page):
    expect.set_options(timeout=20000)       # real Microsoft takes seconds per folder
    try:
        picker_checks(page)
    finally:
        expect.set_options(timeout=5000)


def picker_checks(page):
    """The Add form's file picker (read-only): opens in the car's folder, goes up and back down,
    and choosing a PDF links it. Needs a connected drive whose car folder holds a PDF."""
    page.goto(UI + "onedrive")
    if not page.locator("button:has-text('Disconnect')").count():
        raise Skip("dev stack isn't connected to OneDrive (scripts/dev.sh onedrive-link)")
    page.goto(UI + "new")
    if not page.locator("select[name=car_id]").input_value():
        page.select_option("select[name=car_id]", index=1)
    expect(page.locator("#onedrive-pick")).to_be_visible()
    shot(page, "form-onedrive-button")
    page.click("#onedrive-pick")
    dialog = page.locator("#onedrive-picker")
    expect(dialog).to_be_visible()
    start = dialog.locator("#onedrive-picker-path")
    expect(start).not_to_have_text("")
    if "Choose a OneDrive folder" in dialog.locator("#onedrive-picker-status").inner_text():
        raise Skip("this car has no OneDrive folder")
    here = start.inner_text()
    expect(dialog.get_by_text("⬑ Up")).to_be_visible()    # in the car's folder, so there is a way up
    shot(page, "form-onedrive-picker")                     # before going up: the top of the drive has no Up
    dialog.get_by_text("⬑ Up").click()
    expect(start).not_to_have_text(here)
    expect(dialog.get_by_text("⬑ Up")).to_have_count(0)   # nothing above the top of the drive
    expect(dialog.locator("#onedrive-picker-list li button", has_text="📁").first).to_be_visible()
    dialog.get_by_text("Cancel").click()
    expect(dialog).not_to_be_visible()
    page.click("#onedrive-pick")
    expect(start).to_have_text(here)         # reopening starts at the car's folder again
    items = dialog.locator("#onedrive-picker-list li button")
    pdf = dialog.locator("#onedrive-picker-list li button", has_text="📄")
    if not pdf.count():
        # Uploads go into category subfolders, so look in each folder in turn, as a person would.
        names = [t[2:] for t in items.all_inner_texts() if t.startswith("📁")]
        for name in names:
            dialog.get_by_role("button", name="📁 " + name, exact=True).click()
            expect(dialog.locator("#onedrive-picker-path")).not_to_have_text(here)
            expect(dialog.locator("#onedrive-picker-status")).not_to_have_text("Loading…")
            if pdf.count():
                break
            dialog.get_by_text("⬑ Up").click()
            expect(dialog.locator("#onedrive-picker-path")).to_have_text(here)
    if not pdf.count():
        raise Skip("no PDF in the car's folder or the folders directly in it")
    pdf.first.click()
    expect(page.locator("input[name=invoice_url]")).to_have_value(re.compile(r"^https://"))


def remove_marked(page):
    page.goto(UI)
    while page.locator(f"tr:has-text('{MARKER}') a:has-text('Edit')").count():
        page.locator(f"tr:has-text('{MARKER}') a:has-text('Edit')").first.click()
        page.once("dialog", lambda d: d.accept())
        page.click("button.danger")
        page.wait_for_url(re.compile(r"msg=deleted"))


def test_form(page):
    page.goto(UI + "new")
    shot(page, "form-phone")
    expect(page.locator("h1")).to_have_text("Add expense")
    expect(page.locator("select[name=car_id] option")).not_to_have_count(0)
    assert page.locator("select[name=category_id] option").count() >= 9


def test_odometer_autofills(page):
    page.goto(UI + "new")
    expect(page.locator("#odometer-hint")).to_have_text("Filled from TeslaMate.")
    assert float(page.input_value("input[name=odometer_km]")) > 0


def test_odometer_button_for_past_date(page):
    page.goto(UI + "new")
    expect(page.locator("#odometer-hint")).to_have_text("Filled from TeslaMate.")
    latest = float(page.input_value("input[name=odometer_km]"))
    page.fill("input[name=date]", "2025-01-15")
    page.fill("input[name=time]", "12:00")
    page.fill("input[name=odometer_km]", "")
    page.click("#fill-odometer")
    expect(page.locator("input[name=odometer_km]")).not_to_have_value("")
    assert float(page.input_value("input[name=odometer_km]")) < latest


def test_bad_link_is_explained(page):
    page.goto(UI + "new")
    page.select_option("select[name=category_id]", label="Tyres")
    page.fill("input[name=amount]", "10")
    page.fill("input[name=vendor]", MARKER + " keep me")
    page.fill("input[name=invoice_url]", "http://example.com/x.pdf")
    page.click("button[type=submit].button")
    expect(page.locator(".error")).to_contain_text("https://")
    expect(page.locator("input[name=vendor]")).to_have_value(MARKER + " keep me")
    shot(page, "form-error-phone")


def test_add_list_edit_delete(page):
    add_expense(page)
    row = page.locator(f"tr:has-text('{MARKER}')")
    expect(row).to_contain_text("$123.45")
    link = row.locator("a:has-text('PDF')")
    expect(link).to_have_attribute("href", INVOICE)
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    shot(page, "list-phone")
    row.locator("a:has-text('Edit')").click()
    page.fill("input[name=amount]", "200")
    page.click("button[type=submit].button")
    page.wait_for_url(re.compile(r"msg=updated"))
    expect(page.locator(f"tr:has-text('{MARKER}')")).to_contain_text("$200.00")
    remove_marked(page)
    expect(page.locator(f"tr:has-text('{MARKER}')")).to_have_count(0)


def test_delete_can_be_cancelled(page):
    add_expense(page, "50")
    page.locator(f"tr:has-text('{MARKER}') a:has-text('Edit')").click()
    page.once("dialog", lambda d: d.dismiss())
    page.click("button.danger")
    page.goto(UI)
    expect(page.locator(f"tr:has-text('{MARKER}')")).to_have_count(1)
    remove_marked(page)


def test_phone_layout_has_no_sideways_scroll(page):
    for path in ("new", "categories"):
        page.goto(UI + path)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), path
    page.goto(UI + "categories")
    shot(page, "categories-phone")


def test_header_stays_tidy_on_a_phone(page):
    """Every header link is one line, and the links share rows rather than wrapping inside."""
    page.goto(UI)
    boxes = page.evaluate("""() => [...document.querySelectorAll('header nav a, header .add')].map(a => {
        const r = a.getBoundingClientRect(); return {text: a.textContent.trim(), h: r.height, top: r.top}})""")
    assert len(boxes) >= 4, boxes                      # All, Categories, Dashboards, Settings, Add
    line = page.evaluate("parseFloat(getComputedStyle(document.body).lineHeight)")
    for b in boxes:
        assert b["h"] < line * 2.6, f"{b['text']} wraps inside its link: {b}"
    add = next(b for b in boxes if b["text"] == "+ Add")
    brand_top = page.evaluate("document.querySelector('.brand').getBoundingClientRect().top")
    assert abs(add["top"] - brand_top) < 20, f"Add should sit beside the title: {add}, brand at {brand_top}"
    page.goto(UI + "settings")
    shot(page, "settings-phone")
    page.goto(UI)
    shot(page, "header-phone")


def test_header_on_a_desktop(page):
    """One row: title on the left, links then Add on the right."""
    page.goto(UI)
    tops = page.evaluate("""() => [...document.querySelectorAll('header .brand, header nav a, header .add')]
        .map(a => Math.round(a.getBoundingClientRect().top + a.getBoundingClientRect().height / 2))""")
    assert max(tops) - min(tops) < 12, tops
    shot(page, "header-desktop")


def test_footer_links_to_the_docs_and_names_the_build(page):
    """Every page ends with the docs link and the version; the dev stack is a build with no version."""
    page.goto(UI)
    footer = page.locator("footer")
    docs = footer.get_by_role("link", name="Documentation")
    assert docs.get_attribute("href") == "https://mattew124.github.io/teslamate-expenses/"
    assert docs.get_attribute("rel") == "noopener noreferrer"
    assert "Development build" in footer.inner_text()
    shot(page, "footer")


def test_list_shows_key_fields_on_a_phone(page):
    add_expense(page)
    row = page.locator(f"tr:has-text('{MARKER}')")
    width = page.viewport_size["width"]
    for text in ("$123.45", "Edit", "PDF"):
        box = row.get_by_text(text, exact=False).first.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, f"{text} is off-screen: {box}"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, "list-phone")
    remove_marked(page)


def test_onedrive_page(page):
    """The setup steps render on a phone, with the address to register. Only
    invalid details are submitted, so a real registration in the dev database
    is left alone."""
    page.goto(UI + "onedrive")
    expect(page.locator("h1")).to_have_text("OneDrive")
    expect(page.locator("code.copy")).to_have_text(re.compile(re.escape(BASE) + r"/onedrive/callback$"))
    if not page.locator("details[open]").count():
        page.click("summary")
    page.fill("input[name=client_id]", "not-a-client-id")
    page.fill("input[name=client_secret]", "x")
    page.click("details button[type=submit]")
    expect(page.locator(".error").first).to_contain_text("Application (client) ID")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, "onedrive-phone")
    page.goto(UI + "new")
    expect(page.locator("input[name=invoice_file]")).to_have_attribute("accept", "application/pdf,.pdf")
    shot(page, "form-onedrive-phone")


def test_settings_file_name_defaults_can_be_edited(page):
    """The defaults are real text, so typing adds to them instead of wiping a placeholder.
    Nothing is saved."""
    page.goto(UI + "settings")
    field = page.locator("input[name=invoice_name_format]")
    default = field.input_value()
    assert default == "%date %category - %vendor", default
    assert page.locator("input[name=invoice_date_format]").input_value() == "yymmdd"
    field.click()
    page.keyboard.press("End")
    page.keyboard.type(" %car")
    assert field.input_value() == default + " %car"
    shot(page, "settings-phone")


def test_connect_reaches_microsoft(page):
    """Real Chromium enforces the CSP, so this is where a blocked redirect shows.
    The request to Microsoft is answered locally and never sent. Runs only when
    the dev database has an app registration saved."""
    page.goto(UI + "onedrive")
    button = page.locator("button:has-text('Connect OneDrive')")
    if not button.count():
        if page.locator("button:has-text('Disconnect')").count():
            print("     (not applicable: already connected)")
            return
        raise Skip("no app registration saved")
    reached = []

    def stub(route):
        reached.append(route.request.url)
        route.fulfill(status=200, content_type="text/html", body="<p>stub Microsoft sign-in</p>")
    page.route("https://login.microsoftonline.com/**", stub)
    button.click()
    page.wait_for_url(re.compile(r"^https://login\.microsoftonline\.com/consumers/oauth2/v2\.0/authorize\?"))
    assert reached and "code_challenge=" in reached[0]
    page.unroute("https://login.microsoftonline.com/**")


def test_folder_browser(page):
    """Read-only: when the dev stack is connected to a real OneDrive, open the
    folder browser for the first car. Creates nothing and changes no choice."""
    page.goto(UI + "onedrive")
    if not page.locator("button:has-text('Disconnect')").count():
        raise Skip("dev stack isn't connected to OneDrive")
    page.locator("ul.folders a").first.click()
    expect(page.locator("h1")).to_contain_text("Folder for")
    expect(page.locator("form.new-folder input[name=name]")).to_be_visible()
    expect(page.locator("button:has-text('Use ')")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, "folder-browser-phone")


def test_dashboard(page):
    add_expense(page, "62000", "Initial purchase")
    add_expense(page, "1500", "Tyres")
    errors = []
    page.on("response", lambda r: errors.append(f"{r.status} {r.url}")
            if "/api/ds/query" in r.url and r.status >= 400 else None)
    page.set_viewport_size({"width": 1600, "height": 1800})
    page.goto(GRAFANA + "d/tm-expenses-tco/cost-of-ownership")
    # Stat titles appear, and a value comes back from the datasource.
    expect(page.get_by_text("Total cost", exact=True)).to_be_visible(timeout=30000)
    expect(page.get_by_text("Cost per km — running")).to_be_visible()
    expect(page.get_by_text("Running costs per month")).to_be_visible()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    shot(page, "dashboard")
    assert not errors, errors
    assert page.locator("[data-testid='data-testid Panel status error']").count() == 0, "a panel shows an error"
    body = page.inner_text("body")
    assert "No data" not in body.split("Expenses")[0], "a headline panel has no data"
    assert MARKER in body, "expenses table did not list the test row"
    assert "$62,000.00" in body, "amounts must show in full, grouped, not rescaled"
    assert " Mm" not in body and not re.search(r"\$\d+(\.\d+)?K", body), "units were auto-scaled"
    table_link = page.locator(f"a[href='{INVOICE}']").first
    expect(table_link).to_be_visible()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        phone = browser.new_context(**p.devices["Pixel 7"])
        page = phone.new_page()
        problems = []
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        remove_marked(page)

        step("form renders on a phone", test_form, page)
        step("odometer fills itself in", test_odometer_autofills, page)
        step("odometer button uses the chosen date", test_odometer_button_for_past_date, page)
        step("a bad invoice link is explained, input kept", test_bad_link_is_explained, page)
        step("add, list, invoice link, edit, delete", test_add_list_edit_delete, page)
        step("delete can be cancelled", test_delete_can_be_cancelled, page)
        step("no sideways scroll on a phone", test_phone_layout_has_no_sideways_scroll, page)
        step("header stays tidy on a phone", test_header_stays_tidy_on_a_phone, page)
        step("phone list shows amount, invoice and edit", test_list_shows_key_fields_on_a_phone, page)
        step("a likely duplicate is asked about, then saved anyway", test_duplicate_is_asked_about_before_saving, page)
        step("the duplicate warning can be dismissed", test_duplicate_warning_can_be_dismissed, page)
        step("a PDF over 500 KB uploads (read-only container)", test_large_pdf_is_accepted, page)
        step("OneDrive page and upload field", test_onedrive_page, page)
        step("file name settings start from the real defaults", test_settings_file_name_defaults_can_be_edited, page)
        step("Connect OneDrive goes to Microsoft", test_connect_reaches_microsoft, page)
        step("folder browser (read-only)", test_folder_browser, page)
        step("real upload to the OneDrive test folder", test_real_upload, page)
        step("OneDrive file picker on the Add form", test_onedrive_picker, page)
        step("import page shows progress", test_import_page_shows_progress, page)
        # Expected 400s from the deliberate bad submissions above aren't script errors.
        real = [t for t in problems if "400" not in t]
        step("no script or CSP errors", lambda: (_ for _ in ()).throw(AssertionError(real)) if real else None)

        desktop = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        dash_page = desktop.new_page()
        step("dashboard renders with data and invoice links", test_dashboard, dash_page)
        step("header is one row on a desktop", test_header_on_a_desktop, desktop.new_page())
        step("footer links to the docs and names the build", test_footer_links_to_the_docs_and_names_the_build, desktop.new_page())
        remove_marked(page)
        browser.close()
    print(f"\n{passed} passed, {len(failed)} failed, {len(skipped)} skipped")
    sys.exit(1 if failed else 0)


main()
