"""Content-based PDF classifier and unified folder importer (the "bazaar").

All financial PDFs -- brokerage statements, Koinly crypto tax reports, and
TurboTax Form 1040 exports -- are dropped into one shared local folder for
convenience. Filenames from these sources are unreliable (sites rarely produce
meaningful names), so this module identifies each PDF by its *content* and
routes it to the correct parser, loading everything it recognizes in a single
pass.

Pyodide-safe: the pdfplumber import is deferred into :func:`extract_pages` and
:func:`scan_pdf_documents`; the pure classifier :func:`classify_pdf_text` and
every downstream ``parse_*_text`` work on already-extracted page strings (or
strings extracted lazily on first access -- see engine/pdf_page_text.py) and
never touch pdfplumber themselves.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from engine.brokerage_statement_pdf import (
    BrokerageStatementRecord,
    detect_broker,
    parse_statement_text,
)
from engine.koinly_report_pdf import KoinlyReport, is_koinly_report, parse_koinly_text
from engine.pdf_page_text import LazyPageTexts
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


def classify_pdf_text(pages: Sequence[str]) -> DocKind:
    """Classify a PDF from its per-page text. Pure -- no I/O.

    Order matters -- checks run from strongest to weakest signal:

    * Form 1040 first -- the structured "Form 1040 (YYYY)" footer is
      unambiguous and a far stronger signal than a single vendor-name
      mention. A TurboTax 1040 export can legitimately echo "Koinly" as a
      1099 import source, so the 1040 check must win that race.
    * Koinly next -- requires its vendor branding AND a "TAX YEAR YYYY"
      marker (see is_koinly_report), so it can no longer steal a real 1040.
    * Extension (Form 4868) next.
    * Brokerage LAST -- broker names (Vanguard/Fidelity/...) also appear as 1099
      payer lines inside a TurboTax 1040 export, so the loose broker match must
      run only after the 1040 check has had its chance.
    """
    if is_form_1040(pages):
        return DocKind.FORM_1040
    if is_koinly_report(pages):
        return DocKind.KOINLY
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

    Eager by design (extracts every page up front) -- kept for callers that
    need the full ``list[str]`` result. :func:`scan_pdf_documents` does NOT
    use this helper: it opens each document itself and classifies/parses over
    a lazily-extracting :class:`~engine.pdf_page_text.LazyPageTexts` view, so
    a large document is never read past the page its classifier/parser needs.
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


def _dispatch_pdf(
    kind: DocKind,
    name: str,
    pages: Sequence[str],
    creator: str | None,
    result: PdfImportResult,
) -> None:
    """Route one already-classified document's pages to its parser, writing
    the outcome into *result*. Shared by every document in a scan."""
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


def scan_pdf_documents(documents: Iterable[tuple[str, bytes]]) -> PdfImportResult:
    """Scan already-read PDF bytes, classify by content, and route to its parser.

    Shared core behind both :func:`scan_pdf_folder` (local installs, reads a
    directory) and the public (Pyodide) site, which has no filesystem and can
    only hand over uploaded bytes. *documents* is an iterable of
    ``(name, bytes)`` pairs -- *name* is an arbitrary caller-chosen label (a
    filename for the folder path, an uploaded file's display name on the
    public site) used only for error/skipped/unrecognized reporting and never
    read from disk.

    A single unreadable or malformed document never aborts the scan -- it is
    collected into ``errors`` and the next document is tried. Documents that
    belong to a different (still recognized) type are routed correctly, never
    reported as a broker-detection failure. Every Koinly report found is kept
    -- owner attribution and any per-owner dedup happens downstream in
    engine/pdf_ledger.py, not here. When several 1040s share a tax year the
    last in iteration order wins.

    Each document is opened exactly once and classification + parsing both
    run INSIDE that ``with pdfplumber.open(...)`` block, over a lazily-
    extracting ``LazyPageTexts`` view (see engine/pdf_page_text.py) -- a
    page's text can only be extracted while its parent PDF is still open.
    ``is_form_1040``/``parse_form_1040_text`` and Koinly's equivalents locate
    their anchor by scanning pages in order and stop at the first match, so a
    large 1040 or Koinly report is typically read only a few pages deep
    rather than in full. Brokerage classification (``detect_broker``) and
    parsing (``parse_statement_text``) join every page's text unconditionally
    and so still read the whole document -- that is expected, not a
    regression to fix here.
    """
    import io

    # Deferred: pdfplumber unavailable in Pyodide
    import pdfplumber

    result = PdfImportResult()

    for name, data in documents:
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = LazyPageTexts(pdf.pages)
                metadata = pdf.metadata or {}
                creator: str | None = metadata.get("Creator") or metadata.get("creator")
                if isinstance(creator, bytes):
                    creator = creator.decode("utf-8", errors="replace")

                kind = classify_pdf_text(pages)
                try:
                    _dispatch_pdf(kind, name, pages, creator, result)
                except Exception as exc:  # noqa: BLE001 -- one bad file must not kill the scan
                    result.errors.append((name, str(exc)))
        except Exception as exc:  # noqa: BLE001 -- one bad file must not kill the scan
            result.errors.append((name, f"could not read PDF: {exc}"))

    return result


def scan_pdf_folder(folder: Path) -> PdfImportResult:
    """Scan every PDF in *folder*, classify by content, and route to its parser.

    Thin wrapper: globs *folder* for ``.pdf`` files in sorted order, reads
    each one's bytes, and delegates the classify/dispatch/error handling to
    :func:`scan_pdf_documents`. A file that cannot even be read from disk is
    reported the same way an unopenable in-memory document would be.
    """
    result = PdfImportResult()
    documents: list[tuple[str, bytes]] = []
    for pdf_path in sorted(folder.glob("*.[pP][dD][fF]")):
        try:
            documents.append((pdf_path.name, pdf_path.read_bytes()))
        except OSError as exc:
            result.errors.append((pdf_path.name, f"could not read PDF: {exc}"))

    scanned = scan_pdf_documents(documents)
    result.brokerage_records.extend(scanned.brokerage_records)
    result.koinly_reports.extend(scanned.koinly_reports)
    result.form_1040_records.update(scanned.form_1040_records)
    result.skipped.extend(scanned.skipped)
    result.unrecognized.extend(scanned.unrecognized)
    result.errors.extend(scanned.errors)
    return result
