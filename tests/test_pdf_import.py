"""Tests for engine.pdf_import -- the content-based PDF classifier and router."""

import os
from pathlib import Path

import pytest

from engine import pdf_import
from engine.pdf_import import DocKind, classify_pdf_text, scan_pdf_folder

# --- helpers / stubs -------------------------------------------------------


class _FakeKoinly:
    def __init__(self, tag: str = "") -> None:
        self.tag = tag


class _FakeForm:
    def __init__(self, year: int) -> None:
        self.tax_year = year


def _write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_bytes(text.encode("utf-8"))


@pytest.fixture
def stub_parsers(monkeypatch):
    """Replace pdfplumber extraction and the three parsers with deterministic
    stubs so routing can be verified without real PDFs. extract_pages decodes
    the file bytes as text (one page)."""
    monkeypatch.setattr(
        pdf_import, "extract_pages", lambda data: (data.decode("utf-8").split("\f"), None)
    )
    monkeypatch.setattr(pdf_import, "parse_statement_text", lambda pages: ["BROKER_REC"])
    monkeypatch.setattr(pdf_import, "parse_koinly_text", lambda pages: _FakeKoinly())
    monkeypatch.setattr(
        pdf_import, "parse_form_1040_text", lambda pages, pdf_creator=None: _FakeForm(2023)
    )


# --- classify_pdf_text (pure) ---------------------------------------------


def test_classify_koinly():
    pages = ["Koinly\nTAX YEAR 2026", "Capital gains summary\nNet gains"]
    assert classify_pdf_text(pages) is DocKind.KOINLY


def test_classify_form_1040():
    assert classify_pdf_text(["... Form 1040 (2023) ...", "AGI 123"]) is DocKind.FORM_1040


def test_classify_1040_with_broker_payer_is_not_brokerage():
    # A TurboTax export lists 1099 payer names; the broker match must not win.
    pages = ["Form 1040 (2024)", "Dividends from Vanguard and Fidelity"]
    assert classify_pdf_text(pages) is DocKind.FORM_1040


def test_classify_extension():
    pages = ["Form 4868", "Application for Automatic Extension of Time To File"]
    assert classify_pdf_text(pages) is DocKind.EXTENSION


def test_classify_brokerage_schwab():
    assert classify_pdf_text(["Schwab One Account ..."]) is DocKind.BROKERAGE


def test_classify_unknown():
    assert classify_pdf_text(["just some unrelated text"]) is DocKind.UNKNOWN


def test_koinly_beats_1040_when_both_markers_present():
    pages = ["Koinly complete tax report", "Form 1040 (2026) reference"]
    assert classify_pdf_text(pages) is DocKind.KOINLY


# --- scan_pdf_folder (routing) --------------------------------------------


def test_scan_routes_each_type(tmp_path, stub_parsers):
    _write(tmp_path, "a.pdf", "Schwab One statement")
    _write(tmp_path, "b.pdf", "Koinly report")
    _write(tmp_path, "c.pdf", "Form 1040 (2023)")
    _write(tmp_path, "d.pdf", "Form 4868 extension")
    _write(tmp_path, "e.pdf", "totally unrelated document")

    result = scan_pdf_folder(tmp_path)

    assert result.brokerage_records == ["BROKER_REC"]
    assert result.koinly_report is not None
    assert set(result.form_1040_records) == {2023}
    assert [n for n, _ in result.skipped] == ["d.pdf"]
    assert result.unrecognized == ["e.pdf"]
    assert result.errors == []


def test_scan_collects_parse_errors_without_aborting(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pdf_import, "extract_pages", lambda data: (data.decode("utf-8").split("\f"), None)
    )

    def boom(pages):
        raise ValueError("bad schwab")

    monkeypatch.setattr(pdf_import, "parse_statement_text", boom)
    monkeypatch.setattr(pdf_import, "parse_koinly_text", lambda pages: object())
    _write(tmp_path, "good.pdf", "Koinly report")
    _write(tmp_path, "bad.pdf", "Schwab One statement")

    result = scan_pdf_folder(tmp_path)

    assert result.koinly_report is not None
    assert len(result.errors) == 1
    assert result.errors[0][0] == "bad.pdf"
    assert "bad schwab" in result.errors[0][1]


def test_scan_unreadable_pdf_goes_to_errors(tmp_path, monkeypatch):
    def boom(data):
        raise RuntimeError("not a pdf")

    monkeypatch.setattr(pdf_import, "extract_pages", boom)
    _write(tmp_path, "junk.pdf", "whatever")

    result = scan_pdf_folder(tmp_path)

    assert result.errors
    assert result.errors[0][0] == "junk.pdf"
    assert "could not read PDF" in result.errors[0][1]


def test_newest_koinly_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pdf_import, "extract_pages", lambda data: (data.decode("utf-8").split("\f"), None)
    )
    monkeypatch.setattr(pdf_import, "parse_koinly_text", lambda pages: _FakeKoinly(pages[0]))
    _write(tmp_path, "old.pdf", "Koinly OLD")
    _write(tmp_path, "new.pdf", "Koinly NEW")
    os.utime(tmp_path / "old.pdf", (1_000_000, 1_000_000))
    os.utime(tmp_path / "new.pdf", (1_000_100, 1_000_100))

    result = scan_pdf_folder(tmp_path)

    assert result.koinly_report.tag == "Koinly NEW"
