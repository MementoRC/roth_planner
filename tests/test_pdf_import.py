"""Tests for engine.pdf_import -- the content-based PDF classifier and router."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from engine import pdf_import
from engine.pdf_import import (
    DocKind,
    PdfImportResult,
    classify_pdf_text,
    scan_pdf_documents,
    scan_pdf_folder,
)

# --- helpers / stubs -------------------------------------------------------


@dataclasses.dataclass
class _FakeKoinly:
    """Dataclass (not a plain object) so two independently-constructed
    instances with the same tag compare equal -- needed by the equivalence
    test, which asserts two ``PdfImportResult``s (each holding freshly-built
    fakes) are ``==``."""

    tag: str = ""


@dataclasses.dataclass
class _FakeForm:
    tax_year: int


def _write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_bytes(text.encode("utf-8"))


class _FakePage:
    """Stand-in for a pdfplumber Page: records extract_text() calls."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.extract_calls = 0

    def extract_text(self) -> str:
        self.extract_calls += 1
        return self._text

    def close(self) -> None:
        pass


class _FakePdf:
    def __init__(self, pages: list[_FakePage], metadata: dict[str, Any] | None = None) -> None:
        self.pages = pages
        self.metadata = metadata or {}

    def __enter__(self) -> _FakePdf:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_open_text_pages(stream: Any) -> _FakePdf:
    """pdfplumber.open() stand-in: decodes the given bytes as text and splits
    on "\\f" into one fake page per chunk -- mirrors the plain-text ".pdf"
    fixtures ``_write`` produces. Used by ``stub_parsers`` below in place of
    real PDF bytes."""
    text = stream.getvalue().decode("utf-8")
    return _FakePdf([_FakePage(t) for t in text.split("\f")])


@pytest.fixture
def stub_parsers(monkeypatch):
    """Replace pdfplumber.open and the three parsers with deterministic stubs
    so routing can be verified without real PDFs. The fake pdfplumber.open
    decodes the file bytes as text (one page per "\\f"-separated chunk)."""
    monkeypatch.setattr("pdfplumber.open", _fake_open_text_pages)
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


def test_form_1040_beats_koinly_when_both_signals_present():
    """A TurboTax 1040 export that also carries a fully corroborated Koinly
    signal (vendor name + TAX YEAR marker, e.g. quoting Koinly as a 1099
    import source) must still route to FORM_1040 -- the structured 1040
    footer is the stronger, order-priority signal (see classify_pdf_text)."""
    pages = ["Koinly TAX YEAR 2026 complete tax report", "Form 1040 (2026) reference"]
    assert classify_pdf_text(pages) is DocKind.FORM_1040


# --- scan_pdf_folder (routing) --------------------------------------------


def test_scan_routes_each_type(tmp_path, stub_parsers):
    _write(tmp_path, "a.pdf", "Schwab One statement")
    _write(tmp_path, "b.pdf", "Koinly report\nTAX YEAR 2023")
    _write(tmp_path, "c.pdf", "Form 1040 (2023)")
    _write(tmp_path, "d.pdf", "Form 4868 extension")
    _write(tmp_path, "e.pdf", "totally unrelated document")

    result = scan_pdf_folder(tmp_path)

    assert result.brokerage_records == ["BROKER_REC"]
    assert len(result.koinly_reports) == 1
    assert set(result.form_1040_records) == {2023}
    assert [n for n, _ in result.skipped] == ["d.pdf"]
    assert result.unrecognized == ["e.pdf"]
    assert result.errors == []


def test_scan_collects_parse_errors_without_aborting(tmp_path, monkeypatch):
    monkeypatch.setattr("pdfplumber.open", _fake_open_text_pages)

    def boom(pages):
        raise ValueError("bad schwab")

    monkeypatch.setattr(pdf_import, "parse_statement_text", boom)
    monkeypatch.setattr(pdf_import, "parse_koinly_text", lambda pages: object())
    _write(tmp_path, "good.pdf", "Koinly report\nTAX YEAR 2023")
    _write(tmp_path, "bad.pdf", "Schwab One statement")

    result = scan_pdf_folder(tmp_path)

    assert len(result.koinly_reports) == 1
    assert len(result.errors) == 1
    assert result.errors[0][0] == "bad.pdf"
    assert "bad schwab" in result.errors[0][1]


def test_scan_unreadable_pdf_goes_to_errors(tmp_path, monkeypatch):
    # NOTE: previously monkeypatched pdf_import.extract_pages (the eager
    # helper) to simulate an unreadable file. scan_pdf_folder/scan_pdf_documents
    # no longer route through extract_pages at all -- they open pdfplumber
    # directly per document -- so the equivalent failure point is
    # pdfplumber.open() itself raising.
    def boom(stream):
        raise RuntimeError("not a pdf")

    monkeypatch.setattr("pdfplumber.open", boom)
    _write(tmp_path, "junk.pdf", "whatever")

    result = scan_pdf_folder(tmp_path)

    assert result.errors
    assert result.errors[0][0] == "junk.pdf"
    assert "could not read PDF" in result.errors[0][1]


def test_multiple_koinly_reports_all_survive_scan(tmp_path, monkeypatch):
    """Two owners' Koinly PDFs in one folder must BOTH appear in the result --
    this is the pdf_import half of the override-fix (engine/pdf_ledger.py
    proves the derive-sum half). No mtime-based collapse to a single winner."""
    monkeypatch.setattr("pdfplumber.open", _fake_open_text_pages)
    monkeypatch.setattr(pdf_import, "parse_koinly_text", lambda pages: _FakeKoinly(pages[0]))
    _write(tmp_path, "you.pdf", "Koinly YOU\fTAX YEAR 2023")
    _write(tmp_path, "spouse.pdf", "Koinly SPOUSE\fTAX YEAR 2023")

    result = scan_pdf_folder(tmp_path)

    tags = {r.tag for r in result.koinly_reports}
    assert tags == {"Koinly YOU", "Koinly SPOUSE"}


# --- scan_pdf_documents (in-memory bytes) -----------------------------------


def test_scan_pdf_documents_routes_each_type(stub_parsers):
    documents = [
        ("a", b"Schwab One statement"),
        ("b", b"Koinly report\nTAX YEAR 2023"),
        ("c", b"Form 1040 (2023)"),
        ("d", b"Form 4868 extension"),
        ("e", b"totally unrelated document"),
    ]

    result = scan_pdf_documents(documents)

    assert result.brokerage_records == ["BROKER_REC"]
    assert len(result.koinly_reports) == 1
    assert set(result.form_1040_records) == {2023}
    assert [n for n, _ in result.skipped] == ["d"]
    assert result.unrecognized == ["e"]
    assert result.errors == []


def test_scan_pdf_documents_error_keyed_by_given_name(stub_parsers, monkeypatch):
    def boom(pages):
        raise ValueError("bad schwab")

    monkeypatch.setattr(pdf_import, "parse_statement_text", boom)
    documents = [("my-label", b"Schwab One statement")]

    result = scan_pdf_documents(documents)

    assert result.errors == [("my-label", "bad schwab")]


def test_scan_pdf_documents_unreadable_document_goes_to_errors(monkeypatch):
    def boom(stream):
        raise RuntimeError("not a pdf")

    monkeypatch.setattr("pdfplumber.open", boom)

    result = scan_pdf_documents([("junk", b"whatever")])

    assert result.errors
    assert result.errors[0][0] == "junk"
    assert "could not read PDF" in result.errors[0][1]


# --- equivalence: scan_pdf_folder vs scan_pdf_documents ---------------------


def _result_fields_sans_wallclock(result: PdfImportResult) -> dict:
    """dict of every PdfImportResult field, for equality comparison.

    No field on PdfImportResult itself is wall-clock-derived; the fakes used
    here (``_FakeForm``/``_FakeKoinly``) don't carry a ``captured_at``-style
    field either, so there is nothing to strip in this suite. Kept as a
    named seam (rather than a bare ``==``) so a future field that IS
    wall-clock-derived has one place to exclude it.
    """
    return dataclasses.asdict(result)


def test_scan_pdf_folder_and_scan_pdf_documents_are_equivalent(tmp_path, stub_parsers):
    files = {
        "a.pdf": "Schwab One statement",
        "b.pdf": "Koinly report\nTAX YEAR 2023",
        "c.pdf": "Form 1040 (2023)",
        "d.pdf": "Form 4868 extension",
        "e.pdf": "totally unrelated document",
    }
    for name, text in files.items():
        _write(tmp_path, name, text)

    via_folder = scan_pdf_folder(tmp_path)
    via_documents = scan_pdf_documents(
        (name, text.encode("utf-8")) for name, text in sorted(files.items())
    )

    assert _result_fields_sans_wallclock(via_folder) == _result_fields_sans_wallclock(via_documents)


# --- laziness: classification + parsing stop early on large documents ------


class _CountingFakePage:
    def __init__(self, text: str) -> None:
        self._text = text
        self.extract_calls = 0

    def extract_text(self) -> str:
        self.extract_calls += 1
        return self._text

    def close(self) -> None:
        pass


class _CountingFakePdf:
    def __init__(self, pages: list[_CountingFakePage]) -> None:
        self.pages = pages
        self.metadata: dict[str, str] = {}

    def __enter__(self) -> _CountingFakePdf:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


# Real, fully-anchored 2023 Form 1040 + Schedule 1 page text (same fixtures as
# tests/test_tax_return_pdf.py's _F1040_2023/_SCH1_2023) -- used so the parse
# actually succeeds, not just the classification.
_F1040_2023 = """\
Department of the Treasury — Internal Revenue Service
Form 1040 (2023)         U.S. Individual Income Tax Return
Filing Status  Single  Married filing jointly  Head of household (HOH)

2a  Tax-exempt interest . .  2a  2,511   b  Taxable interest  2b  1,000
3a  Qualified dividends . .  3a     500   b  Ordinary dividends  3b  1,200
6   Social security benefits  6b  12,000
11  Subtract line 10 from line 9. This is your adjusted gross income  162,433
"""

_SCH1_2023 = """\
SCHEDULE 1 (Form 1040)
Schedule 1  (Form 1040)   Additional Income and Adjustments
8d  Foreign earned income exclusion  8d  3,000
"""

_FILLER_PAGE = "This is a worksheet page with no 1040 content."


def test_scan_pdf_documents_reads_only_needed_pages_of_large_1040(monkeypatch):
    """A 50-page bundle with the Form 1040 footer on page 0 and Schedule 1 on
    page 2 must classify+parse reading only a handful of pages, never all 50
    -- proving classification/parsing run lazily over LazyPageTexts instead
    of the old eager per-page extraction."""
    texts = [_F1040_2023, _FILLER_PAGE, _SCH1_2023] + [_FILLER_PAGE] * 47
    fake_pages = [_CountingFakePage(t) for t in texts]
    fake_pdf = _CountingFakePdf(fake_pages)

    def fake_open(stream: object) -> _CountingFakePdf:
        return fake_pdf

    monkeypatch.setattr("pdfplumber.open", fake_open)

    result = scan_pdf_documents([("big.pdf", b"irrelevant-bytes")])

    assert result.errors == []
    assert set(result.form_1040_records) == {2023}
    total_extract_calls = sum(p.extract_calls for p in fake_pages)
    assert total_extract_calls <= 5, (
        f"expected <= 5 extract_text() calls, got {total_extract_calls}"
    )
