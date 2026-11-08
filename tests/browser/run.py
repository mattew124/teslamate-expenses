"""Browser tests against the running dev stack: `scripts/dev.sh test-browser`.

Drives real Chromium through the UI on a phone viewport and renders the Grafana
dashboard, reaching the containers directly on the dev network. Screenshots go
to .dev/screenshots. Everything it creates has a marker vendor and is deleted
again, so test expenses you entered by hand are left alone.
"""

import os
import re
import sys

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_PATH", "/expenses-dev")
UI = f"http://expenses-dev:8080{BASE}/"
GRAFANA = f"http://dev-grafana:3000{BASE}/grafana/"
OUT = os.environ.get("OUT", "/out")
MARKER = "BROWSER-TEST"
INVOICE = "https://example.com/invoice-browser-test.pdf"
passed, failed = 0, []


def step(name, fn, *args):
    global passed
    try:
        fn(*args)
        passed += 1
        print(f"ok   {name}")
    except Exception as exc:  # keep going: report every failure in one run
        failed.append(name)
        print(f"FAIL {name}\n     {str(exc).splitlines()[0][:300]}")


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


def test_connect_reaches_microsoft(page):
    """Real Chromium enforces the CSP, so this is where a blocked redirect shows.
    The request to Microsoft is answered locally and never sent. Runs only when
    the dev database has an app registration saved."""
    page.goto(UI + "onedrive")
    button = page.locator("button:has-text('Connect OneDrive')")
    if not button.count():
        print("     (skipped: no app registration saved, or already connected)")
        return
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
        print("     (skipped: dev stack isn't connected to OneDrive)")
        return
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
    expect(page.get_by_text("Monthly running costs")).to_be_visible()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    shot(page, "dashboard")
    assert not errors, errors
    assert page.locator("[data-testid='data-testid Panel status error']").count() == 0, "a panel shows an error"
    body = page.inner_text("body")
    assert "No data" not in body.split("Expenses")[0], "a headline panel has no data"
    assert MARKER in body, "expenses table did not list the test row"
    assert "$62000.00" in body, "amounts must show in full, not rescaled"
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
        step("phone list shows amount, invoice and edit", test_list_shows_key_fields_on_a_phone, page)
        step("OneDrive page and upload field", test_onedrive_page, page)
        step("Connect OneDrive goes to Microsoft", test_connect_reaches_microsoft, page)
        step("folder browser (read-only)", test_folder_browser, page)
        # Expected 400s from the deliberate bad submissions above aren't script errors.
        real = [t for t in problems if "400" not in t]
        step("no script or CSP errors", lambda: (_ for _ in ()).throw(AssertionError(real)) if real else None)

        desktop = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        dash_page = desktop.new_page()
        step("dashboard renders with data and invoice links", test_dashboard, dash_page)
        remove_marked(page)
        browser.close()
    print(f"\n{passed} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


main()
