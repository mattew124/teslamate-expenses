"""Screenshots for the README and docs: `scripts/dev.sh demo screenshots`.

Only ever run this against the demo stack (`scripts/dev.sh demo up`), whose data is invented.
It only reads: no expense is added, edited or deleted. Files go to OUT, named after the
`<!-- screenshot: name - ... -->` markers in docs/.
"""

import os
import re

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_PATH", "/expenses-dev")
UI = f"http://expenses-dev:8080{BASE}/"
GRAFANA = f"http://dev-grafana:3000{BASE}/grafana/"
OUT = os.environ.get("OUT", "/out")


def shot(page, name, full_page=False):
    page.screenshot(path=f"{OUT}/{name}.png", full_page=full_page)
    print(f"  {name}.png")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        desktop = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=2,
                                      color_scheme="light", locale="en-AU")
        page = desktop.new_page()

        page.goto(UI)
        expect(page.locator("html")).to_have_attribute("data-view", "rows")
        shot(page, "expense-list-rows")
        page.click("[data-view-choice=tiles]")
        shot(page, "expense-list-tiles")
        page.evaluate("localStorage.clear()")

        page.goto(UI + "new")
        shot(page, "expense-form")
        page.goto(UI + "settings")
        # The stack's own addresses are real hostnames: show example ones, without saving them.
        page.fill("[name=grafana_url]", "https://teslamate.example.com/grafana/d/tm-expenses-tco/cost-of-ownership")
        page.fill("[name=expenses_url]", "https://teslamate.example.com/expenses/")
        shot(page, "settings-page", full_page=True)

        # Invoice storage. Leave the OneDrive card out: it carries Microsoft's logo, which Microsoft's
        # brand terms don't allow in published material (the card also shows this stack's callback address).
        page.goto(UI + "storage")
        page.evaluate("document.querySelector('#onedrive').remove()")
        shot(page, "storage-page", full_page=True)
        car = re.search(r"local/import/(\d+)", page.locator("a[href*='local/import/']").first.get_attribute("href")).group(1)
        page.goto(UI + "new")
        page.click("#onedrive-pick")
        expect(page.locator("#onedrive-picker-path")).to_have_text("/Sample invoices")
        expect(page.locator("#onedrive-picker-list li").first).to_be_visible()
        shot(page, "local-folder-picker")
        # The import preview of the made-up invoices `demo up` put in the folder. Nothing is imported.
        page.goto(UI + f"local/import/{car}")
        expect(page.locator("#import-status-text")).to_have_text(re.compile(r"^Read \d+ PDF"), timeout=180000)
        shot(page, "import-preview", full_page=True)

        big = browser.new_context(viewport={"width": 1600, "height": 1800}, device_scale_factor=1,
                                  color_scheme="light", locale="en-AU")
        dash = big.new_page()
        # Kiosk drops Grafana's side menu and breadcrumb; the boxes and time range stay.
        dash.goto(GRAFANA + "d/tm-expenses-tco/cost-of-ownership"
                  "?kiosk&_dash.hideTimePicker=false&_dash.hideVariables=false&_dash.hideLinks=false")
        expect(dash.get_by_text("Total cost", exact=True)).to_be_visible(timeout=30000)
        dash.wait_for_load_state("networkidle")
        dash.wait_for_timeout(3000)
        shot(dash, "dashboard")

        phone = browser.new_context(**p.devices["Pixel 7"], locale="en-AU")
        mobile = phone.new_page()
        mobile.goto(UI)
        shot(mobile, "expense-list-phone")
        browser.close()


main()
