"""Debug logging per document: enough to see what happened to a file, never its contents.

The level and handlers belong to the logging setup (LOG_LEVEL); here the records are read straight
from the "tmexpenses" logger and its children.
"""

import io
import logging

import pytest

from tmexpenses import invoices
from tests.test_app import ORIGIN
from tests.test_import import TEXT, app_for, client, cat, ms, post_rows  # noqa: F401  (fixtures)
from tests.test_onedrive import choose_folder, sign_in, with_pdf  # noqa: F401
from tests.test_app import form


@pytest.fixture(autouse=True)
def debug_level(caplog):
    caplog.set_level(logging.DEBUG, logger="tmexpenses")


def lines(caplog, contains=""):
    return [r.getMessage() for r in caplog.records if r.name.startswith("tmexpenses") and contains in r.getMessage()]


def test_prefill_logs_the_document_and_what_was_found(client, caplog):
    client.post("/api/prefill", headers=ORIGIN, data={
        "invoice_file": (io.BytesIO(b"%PDF-tyres"), "261024 - Tyres.pdf", "application/pdf")})
    (line,) = lines(caplog, "prefill")
    assert 'file="261024 - Tyres.pdf"' in line and "bytes=10" in line and "sha256=" in line
    assert "found=" in line and "date" in line and "amount" in line and "missing=" in line and "ms=" in line


def test_logs_name_fields_but_never_values_or_text(client, caplog):
    client.post("/api/prefill", headers=ORIGIN, data={
        "invoice_file": (io.BytesIO(b"%PDF-tyres"), "261024 - Tyres.pdf", "application/pdf")})
    everything = "\n".join(r.getMessage() for r in caplog.records)
    for secret in ("1234.50", "45120", "Example Tyres Pty Ltd", "Invoice Date"):
        assert secret not in everything, secret


def test_a_refused_file_is_logged_with_the_reason(client, caplog):
    client.post("/api/prefill", headers=ORIGIN, data={
        "invoice_file": (io.BytesIO(b"hello"), "bad.pdf", "application/pdf")})
    assert any("refused" in m and "bad.pdf" in m for m in lines(caplog, "prefill"))


def test_extract_text_logs_how_the_text_was_read(monkeypatch, caplog):
    monkeypatch.setattr(invoices, "_run", lambda cmd, timeout: "x" * 100)
    invoices.extract_text(b"%PDF-1.4 ...")
    (line,) = lines(caplog, "extract")
    assert "method=text" in line and "chars=100" in line and "bytes=12" in line and "ms=" in line


def test_a_missing_tool_is_a_warning_not_silence(monkeypatch, caplog):
    import subprocess
    def boom(*a, **k):
        raise FileNotFoundError("pdftotext")
    monkeypatch.setattr(subprocess, "run", boom)
    assert invoices.extract_text(b"%PDF-1.4") == ""
    warn = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warn and "pdftotext" in warn[0].getMessage()


def test_a_tool_failure_is_a_warning_with_its_message(monkeypatch, caplog):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"Syntax Error: bad xref"))
    invoices.extract_text(b"%PDF-1.4")
    assert any("Syntax Error" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_import_preview_logs_each_file(client, caplog):
    client.get("/onedrive/import/1?sync=1")
    assert lines(caplog, "import scan") and "listed=3" in lines(caplog, "import scan")[0]
    per_file = lines(caplog, "import file")
    assert len(per_file) == 3 and any('file="261024 - Tyres.pdf"' in m and "found=" in m for m in per_file)


def test_import_confirm_logs_the_outcome(client, admin, caplog):
    client.post("/onedrive/import/1", data=post_rows(admin), headers=ORIGIN)
    (line,) = lines(caplog, "import confirm")
    assert "rows=2" in line and "imported=2" in line and "renamed=0" in line


def test_a_failed_rename_is_logged_as_a_warning(client, ms, admin, caplog):
    ms.rename_status = 409
    client.post("/onedrive/import/1", data=post_rows(admin, rename_1="on"), headers=ORIGIN)
    assert any(r.levelno == logging.WARNING and "rename" in r.getMessage() and "P1" in r.getMessage()
               for r in caplog.records)


def test_upload_logs_name_folder_and_size(client, ms, admin, caplog):
    sign_in_done = client  # the import fixture already signed in and chose a folder
    sign_in_done.post("/new", data=with_pdf(form(admin, invoice_url="")), headers=ORIGIN)
    (line,) = lines(caplog, "upload")
    assert "subfolder=Maintenance" in line and 'name="261107 Tyres - Tyre Shop.pdf"' in line and "bytes=" in line
