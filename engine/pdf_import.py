"""Content-based PDF classifier and unified folder importer (the "bazaar").

All financial PDFs -- brokerage statements, Koinly crypto tax reports, and
TurboTax Form 1040 exports -- are dropped into one shared local folder for
convenience. Filenames from these sources are unreliable (sites rarely produce
meaningful names), so this module identifies each PDF by its *content* and
routes it to the correct parser, loading everything it recognizes in a single
pass.

Pyodide-safe: the pdfplumber import is deferred to :func:`extract_pages`; the
pure classifier :func:`classify_pdf_text` and every downstream ``parse_*_text``
work on already-extracted page strings and never touch pdfplumber.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from engine.brokerage_statement_pdf import (
    BrokerageStatementRecord,
    detect_broker,
    parse_statement_text,
)
from engine.koinly_report_pdf import KoinlyReport, is_koinly_report, parse_koinly_text
from engine.tax_return_pdf import Form1040Record, is_form_1040, parse_form_1040_text

# Form 4868 (Application for Automatic Extension). Recognized so it is reported
# as a deliberate skip ("nothing to import") rather than an unknown file.
_EXTENSION_RE = re.compile(r"Form 4868|Application for Automatic Extension", re.IGNORECASE)


class DocKind(StrEnum):
    """The document types the importer can recognize."""

    KOINLY = "koinly"
    FORM_1040 = "form_1040"
    EXTENSION = "extension"
    BROKERAGE = "brokerage"
    UNKNOWN = "unknown"


def classify_pdf_text(pages: list[str]) -> DocKind:
    """Classify a PDF from its per-page text. Pure -- no I/O.

    Order matters. The two most distinctive documents are checked first:

    * Koinly first -- its vendor branding ("Koinly") never appears in a
      brokerage statement or an IRS form, so this can never steal another type.
    * Form 1040 next -- the "Form 1040 (YYYY)" footer is unambiguous.
    * Extension (Form 4868) next.
    * Brokerage LAST -- broker names (Vanguard/Fidelity/...) also appear as 1099
      payer lines inside a TurboTax 1040 export, so the loose broker match must
      run only after the 1040 check has had its chance.
    """
    if is_koinly_report(pages):
        return DocKind.KOINLY
    if is_form_1040(pages):
        return DocKind.FORM_1040
    full_text = "\n".join(pages)
    if _EXTENSION_RE.search(full_text):
        return DocKind.EXTENSION
    if detect_broker(full_text) is not None:
        return DocKind.BROKERAGE
    return DocKind.UNKNOWN


def extract_pages(data: bytes) -> tuple[list[str], str | None]:
    """Extract per-page text (and the PDF Creator metadata) from raw bytes.

    pdfplumber import deferred for Pyodide safety -- only local installs call
    this. Mirrors the extraction each single-document parser does, so a PDF read
    once here parses identically downstream.
    """
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
        metadata = pdf.metadata or {}
        creator: str | None = metadata.get("Creator") or metadata.get("creator")
        if isinstance(creator, bytes):
            creator = creator.decode("utf-8", errors="replace")
    return pages, creator


@dataclass
class PdfImportResult:
    """Aggregated outcome of scanning a shared folder of mixed PDFs."""

    brokerage_records: list[BrokerageStatementRecord] = field(default_factory=list)
    koinly_reports: list[KoinlyReport] = field(default_factory=list)
    form_1040_records: dict[int, Form1040Record] = field(default_factory=dict)
    # (filename, reason) -- recognized document with no importable data (e.g. 4868).
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # filenames that matched no known format at all.
    unrecognized: list[str] = field(default_factory=list)
    # (filename, message) -- a recognized type that failed to read or parse.
    errors: list[tuple[str, str]] = field(default_factory=list)


def scan_pdf_folder(folder: Path) -> PdfImportResult:
    """Scan every PDF in *folder*, classify by content, and route to its parser.

    A single unreadable or malformed file never aborts the scan -- it is
    collected into ``errors`` and the next file is tried. Files that belong to a
    different (still recognized) type are routed correctly, never reported as a
    broker-detection failure. Every Koinly report found is kept -- owner
    attribution and any per-owner dedup happens downstream in
    engine/pdf_ledger.py, not here. When several 1040s share a tax year the
    last in sorted order wins.
    """
    result = PdfImportResult()

    for pdf_path in sorted(folder.glob("*.[pP][dD][fF]")):
        name = pdf_path.name
        try:
            pages, creator = extract_pages(pdf_path.read_bytes())
        except Exception as exc:  # noqa: BLE001 -- one bad file must not kill the scan
            result.errors.append((name, f"could not read PDF: {exc}"))
            continue

        kind = classify_pdf_text(pages)
        try:
            if kind is DocKind.BROKERAGE:
                result.brokerage_records.extend(parse_statement_text(pages))
            elif kind is DocKind.KOINLY:
                result.koinly_reports.append(parse_koinly_text(pages))
            elif kind is DocKind.FORM_1040:
                rec = parse_form_1040_text(pages, pdf_creator=creator)
                result.form_1040_records[rec.tax_year] = rec
            elif kind is DocKind.EXTENSION:
                result.skipped.append((name, "Form 4868 extension — no importable data"))
            else:
                result.unrecognized.append(name)
        except Exception as exc:  # noqa: BLE001 -- one bad file must not kill the scan
            result.errors.append((name, str(exc)))

    return result
