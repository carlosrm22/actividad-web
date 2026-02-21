from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright no disponible en este entorno: {exc}")
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    yield page
    context.close()


def test_dashboard_loads_and_privacy_backup_flow(page, live_server, tmp_path):
    base_url, _app = live_server

    page.goto(base_url, wait_until="networkidle")
    expect(page.locator("h1")).to_have_text("Actividad Web")
    expect(page.locator("#status-pill")).to_contain_text("Pausado")

    page.click("#refresh-btn")
    page.wait_for_timeout(500)

    # Crea una regla de privacidad desde UI.
    page.fill("#privacy-pattern", "banco")
    page.click("#privacy-add-btn")
    expect(page.locator("#privacy-rules-body")).to_contain_text("banco")

    # Exportación CSV debe generar descarga.
    with page.expect_download() as dl_info:
        page.click("#export-csv-btn")
    download = dl_info.value
    assert download.suggested_filename.endswith(".csv")

    # Restauración desde backup JSON por UI.
    with urlopen(f"{base_url}/api/backup/export", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))

    backup_file = Path(tmp_path) / "backup.json"
    backup_file.write_text(json.dumps(payload), encoding="utf-8")

    page.set_input_files("#restore-file-input", str(backup_file))
    page.check("#restore-replace")
    page.click("#restore-btn")
    page.wait_for_function("() => document.querySelector('#backup-status').textContent.includes('Restaurado')")

    expect(page.locator("#backup-status")).to_contain_text("Restaurado")
