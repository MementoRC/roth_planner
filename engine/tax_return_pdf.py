"""TurboTax 1040 PDF parser — extracts MAGI components from exported PDF bundles.

Text-anchor approach (no AcroForm fields in TurboTax exports). Locates
Form 1040 and Schedule 1 pages by footer, then applies per-year regex maps.

pdfplumber import is DEFERRED into parse_form_1040_pdf to stay Pyodide-safe
(PR #49 lesson: module-level heavy imports break the public web build).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.secure_io import write_pii_json


class Form1040ParseError(Exception):
    """Raised when a 1040 PDF cannot be parsed or the tax year is unsupported."""


# ---------------------------------------------------------------------------
# Per-year anchor maps
# ---------------------------------------------------------------------------
# Each anchor entry has:
#   form   : "f1040" (Form 1040 page) or "sch1" (Schedule 1 page)
#   regex  : pattern with one capture group for the raw currency string
#   optional: if True, missing field → 0.0 (no error)
#
# Verified 2026-06-09 against a real TurboTax 2023 export.
# 2024 uses the same stable IRS line numbers (unchanged since 2020 redesign).

ANCHORS: dict[int, dict[str, dict[str, Any]]] = {
    2023: {
        "agi": {
            "form": "f1040",
            "line": "11",
            # TurboTax repeats the line number after the label with dot leaders:
            # "This is your adjusted gross income .......... 11  162,433"
            # Without the (?:11\s+)? skip, [\s.]* bridges to the first digit
            # and captures "11" (the repeated line token) instead of the value.
            # The skip is optional so synthetic fixtures without the repeat still pass.
            "regex": r"This is your adjusted gross income[\s.]+(?:11\s+)?(\d[\d,]*)",
            "optional": False,
        },
        "tax_exempt_interest": {
            "form": "f1040",
            "line": "2a",
            # "Tax-exempt interest .......... 2a  2,511" — 2a already consumed.
            # Optional skip guards against any layout variant without the label.
            "regex": r"Tax-exempt interest[\s.]+(?:2a\s+)?(\d[\d,]*)",
            "optional": True,
        },
        "qualified_dividends": {
            "form": "f1040",
            "line": "3a",
            # "Qualified dividends .......... 3a  500" — 3a already consumed.
            "regex": r"Qualified dividends[\s.]+(?:3a\s+)?(\d[\d,]*)",
            "optional": True,
        },
        "ordinary_dividends": {
            "form": "f1040",
            "line": "3b",
            # "Ordinary dividends .......... 3b  1,200" — 3b already consumed.
            "regex": r"Ordinary dividends[\s.]+(?:3b\s+)?(\d[\d,]*)",
            "optional": True,
        },
        "taxable_ss": {
            "form": "f1040",
            "line": "6b",
            # SS block spans multiple text segments; allow up to 80 chars gap.
            # 6b is already consumed in the [\s\S]{0,80}6b\s+ span.
            "regex": r"Social security benefits[\s\S]{0,80}6b\s+(\d[\d,]*)",
            "optional": True,
        },
        "feie": {
            "form": "sch1",
            "line": "8d",
            # TurboTax repeats the line number after the label with dot leaders:
            # "Foreign earned income exclusion ...... 8d 6,500". Optional skip
            # guards against layouts without the repeated 8d.
            "regex": r"Foreign earned income exclusion[\s.]+(?:8d\s+)?(\d[\d,]*)",
            "optional": True,
        },
    },
    2024: {
        # IRS line numbers unchanged from 2023 — same anchors apply
        "agi": {
            "form": "f1040",
            "line": "11",
            # See 2023 agi comment — optional (?:11\s+)? skip for realistic layout.
            "regex": r"This is your adjusted gross income[\s.]+(?:11\s+)?(\d[\d,]*)",
            "optional": False,
        },
        "tax_exempt_interest": {
            "form": "f1040",
            "line": "2a",
            "regex": r"Tax-exempt interest[\s.]+(?:2a\s+)?(\d[\d,]*)",
            "optional": True,
        },
        "qualified_dividends": {
            "form": "f1040",
            "line": "3a",
            "regex": r"Qualified dividends[\s.]+(?:3a\s+)?(\d[\d,]*)",
            "optional": True,
        },
        "ordinary_dividends": {
            "form": "f1040",
            "line": "3b",
            "regex": r"Ordinary dividends[\s.]+(?:3b\s+)?(\d[\d,]*)",
            "optional": True,
        },
        "taxable_ss": {
            "form": "f1040",
            "line": "6b",
            "regex": r"Social security benefits[\s\S]{0,80}6b\s+(\d[\d,]*)",
            "optional": True,
        },
        "feie": {
            "form": "sch1",
            "line": "8d",
            # TurboTax repeats the line number after the label with dot leaders:
            # "Foreign earned income exclusion ...... 8d 6,500". Optional skip
            # guards against layouts without the repeated 8d.
            "regex": r"Foreign earned income exclusion[\s.]+(?:8d\s+)?(\d[\d,]*)",
            "optional": True,
        },
    },
}

SUPPORTED_YEARS = frozenset(ANCHORS.keys())

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Form1040Record:
    """Structured data extracted from a TurboTax 1040 PDF export.

    ``filing_status`` is left None by the parser — checkbox detection is
    deferred to UI confirmation (v1 design decision per handoff doc §5).
    ``magi`` is computed by ``compute_magi``; taxable_ss is already inside
    AGI and is stored for reference only (not re-added to MAGI).
    """

    tax_year: int
    agi: float
    tax_exempt_interest: float
    taxable_ss: float
    qualified_dividends: float
    ordinary_dividends: float
    feie: float
    magi: float
    filing_status: str | None
    captured_at: str
    source: str = "pdf"
    parser_version: str = "1.0.0"
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "tax_year": self.tax_year,
            "agi": self.agi,
            "tax_exempt_interest": self.tax_exempt_interest,
            "taxable_ss": self.taxable_ss,
            "qualified_dividends": self.qualified_dividends,
            "ordinary_dividends": self.ordinary_dividends,
            "feie": self.feie,
            "magi": self.magi,
            "filing_status": self.filing_status,
            "captured_at": self.captured_at,
            "source": self.source,
            "parser_version": self.parser_version,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Form1040Record:
        """Deserialise from a dict (e.g. loaded from JSON cache)."""
        return cls(
            tax_year=int(data["tax_year"]),
            agi=float(data["agi"]),
            tax_exempt_interest=float(data["tax_exempt_interest"]),
            taxable_ss=float(data["taxable_ss"]),
            qualified_dividends=float(data["qualified_dividends"]),
            ordinary_dividends=float(data["ordinary_dividends"]),
            feie=float(data["feie"]),
            magi=float(data["magi"]),
            filing_status=data.get("filing_status"),
            captured_at=str(data["captured_at"]),
            source=str(data.get("source", "pdf")),
            parser_version=str(data.get("parser_version", "1.0.0")),
            provenance=dict(data.get("provenance", {})),
        )


# ---------------------------------------------------------------------------
# MAGI computation
# ---------------------------------------------------------------------------


def compute_magi(
    agi: float,
    tax_exempt_interest: float,
    feie: float,
) -> float:
    """Compute MAGI from the 4-component formula matching FinExtract's contract.

    MAGI = AGI + tax-exempt interest + FEIE (+ excluded savings bond interest
    and other rare add-backs that are 0 for most filers).
    taxable_ss is already inside AGI — do NOT add it again.
    """
    return agi + tax_exempt_interest + feie


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_currency(raw: str) -> float:
    """Strip commas and trailing dots, return float."""
    return float(raw.replace(",", "").rstrip("."))


def _extract_field(
    page_text: str,
    pattern: str,
    *,
    optional: bool,
    field_name: str,
    tax_year: int,
) -> float:
    """Apply a single anchor regex to page text; return float value or 0.0."""
    match = re.search(pattern, page_text, re.DOTALL)
    if match:
        return _parse_currency(match.group(1))
    if optional:
        return 0.0
    raise Form1040ParseError(
        f"Required field '{field_name}' not found in tax year {tax_year} 1040 text. "
        f"Pattern: {pattern!r}"
    )


# ---------------------------------------------------------------------------
# Core parser (pure — no pdfplumber dependency)
# ---------------------------------------------------------------------------


def parse_form_1040_text(
    pages: list[str],
    *,
    pdf_creator: str | None = None,
) -> Form1040Record:
    """Parse Form 1040 fields from a list of per-page text strings.

    Pure function — no I/O, no pdfplumber. The ``pages`` list mirrors
    pdfplumber's ``page.extract_text()`` output (one string per PDF page).

    Steps:
    1. Scan for ``Form 1040 (YYYY)`` footer → f1040_page_index + tax_year.
    2. Scan for ``Schedule 1 (Form 1040)`` footer → sch1_page_index (optional).
    3. Apply per-year ANCHORS to located pages.
    4. Compute MAGI; stamp captured_at.
    """
    # Step 1: locate Form 1040 page
    # re.IGNORECASE: PDF footer rendering may vary (e.g. "FORM 1040 (2023)")
    f1040_page_index: int | None = None
    tax_year: int | None = None
    for idx, text in enumerate(pages):
        m = re.search(r"Form 1040\s*\((\d{4})\)", text, re.IGNORECASE)
        if m:
            f1040_page_index = idx
            tax_year = int(m.group(1))
            break

    if f1040_page_index is None or tax_year is None:
        raise Form1040ParseError(
            "No 'Form 1040 (YYYY)' page found in the provided text. "
            "Ensure the PDF is a complete TurboTax export containing the federal 1040."
        )

    # Both are now narrowed to int — bind to non-optional locals for mypy
    resolved_page: int = f1040_page_index
    resolved_year: int = tax_year

    if resolved_year not in ANCHORS:
        raise Form1040ParseError(
            f"Tax year {resolved_year} is not supported. "
            f"Supported years: {sorted(SUPPORTED_YEARS)}. "
            "Add an entry to ANCHORS in engine/tax_return_pdf.py to extend coverage."
        )

    # Step 2: locate Schedule 1 page (optional — missing → feie=0.0)
    # re.IGNORECASE: real TurboTax PDFs render "SCHEDULE 1 (Form 1040)" uppercase
    sch1_page_index: int | None = None
    for idx, text in enumerate(pages):
        if re.search(r"Schedule 1\s*\(Form 1040\)", text, re.IGNORECASE):
            sch1_page_index = idx
            break

    # Step 3: apply anchors
    anchors = ANCHORS[resolved_year]
    f1040_text = pages[resolved_page]
    sch1_text = pages[sch1_page_index] if sch1_page_index is not None else ""

    def _apply(field_name: str) -> float:
        anchor = anchors[field_name]
        page_text = sch1_text if anchor["form"] == "sch1" else f1040_text
        # Schedule 1 absent → feie always 0.0 regardless of optional flag
        if anchor["form"] == "sch1" and sch1_page_index is None:
            return 0.0
        return _extract_field(
            page_text,
            anchor["regex"],
            optional=bool(anchor["optional"]),
            field_name=field_name,
            tax_year=resolved_year,
        )

    agi = _apply("agi")
    tax_exempt_interest = _apply("tax_exempt_interest")
    qualified_dividends = _apply("qualified_dividends")
    ordinary_dividends = _apply("ordinary_dividends")
    taxable_ss = _apply("taxable_ss")
    feie = _apply("feie")

    magi = compute_magi(agi, tax_exempt_interest, feie)

    provenance: dict[str, Any] = {
        "f1040_page_index": resolved_page,
        "sch1_page_index": sch1_page_index,
        "form_revision": f"Form 1040 ({resolved_year})",
        "pdf_creator": pdf_creator,
        "pdf_pages_total": len(pages),
    }

    return Form1040Record(
        tax_year=resolved_year,
        agi=agi,
        tax_exempt_interest=tax_exempt_interest,
        taxable_ss=taxable_ss,
        qualified_dividends=qualified_dividends,
        ordinary_dividends=ordinary_dividends,
        feie=feie,
        magi=magi,
        filing_status=None,
        captured_at=datetime.now(UTC).isoformat(),
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# PDF wrapper (pdfplumber deferred — Pyodide-safe)
# ---------------------------------------------------------------------------


def parse_form_1040_pdf(data: bytes) -> Form1040Record:
    """Parse a TurboTax 1040 PDF export from raw bytes.

    Thin wrapper around ``parse_form_1040_text``. Extracts per-page text via
    pdfplumber and pulls ``pdf:Creator`` from metadata.

    The pdfplumber import is intentionally deferred so this module stays
    importable in Pyodide (public web build) — only local installs with
    pdfplumber available will call this function.
    """
    import io

    # Deferred: pdfplumber unavailable in Pyodide
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
        metadata = pdf.metadata or {}
        pdf_creator: str | None = metadata.get("Creator") or metadata.get("creator")
        if isinstance(pdf_creator, bytes):
            pdf_creator = pdf_creator.decode("utf-8", errors="replace")

    return parse_form_1040_text(pages, pdf_creator=pdf_creator)


# ---------------------------------------------------------------------------
# JSON cache — mirrors save_tax_snapshot / load_tax_snapshot pattern
# ---------------------------------------------------------------------------

_PDF_TAX_CACHE_PATH = Path(__file__).resolve().parent.parent / ".tax_pdf_cache.json"


def save_pdf_tax_records(records: dict[int, Form1040Record]) -> None:
    """Persist parsed PDF tax records to disk as JSON.

    Keys are stored as strings (JSON requirement); year ints are converted.
    """
    serialised: dict[str, Any] = {str(k): v.to_dict() for k, v in records.items()}
    write_pii_json(_PDF_TAX_CACHE_PATH, serialised)


def load_pdf_tax_records() -> dict[int, Form1040Record]:
    """Load cached PDF tax records from disk.

    Returns an empty dict on missing or corrupt file (same tolerance as
    load_tax_snapshot in portfolio_sync.py).
    """
    if not _PDF_TAX_CACHE_PATH.exists():
        return {}
    try:
        raw: dict[str, Any] = json.loads(_PDF_TAX_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    result: dict[int, Form1040Record] = {}
    for k, v in raw.items():
        try:
            year = int(k)
            result[year] = Form1040Record.from_dict(v)
        except (KeyError, ValueError, TypeError):
            # Skip malformed entries — partial corruption should not blow up
            continue
    return result


def merge_pdf_magi(
    existing: dict[int, float],
    records: dict[int, Form1040Record],
) -> dict[int, float]:
    """Gap-fill ``existing`` MAGI dict with PDF-sourced values.

    Only fills years that are absent or falsy in ``existing`` — manual
    in-session edits and FinExtract values already in place are preserved.
    PDF takes precedence over FinExtract because FinExtract only covers the
    current + prior year from the TurboTax dashboard, while PDFs carry
    Schedule 1 detail for any historical year.

    Returns a *new* dict; does not mutate ``existing``.
    """
    result = dict(existing)
    for year, rec in records.items():
        if not result.get(year):
            result[year] = rec.magi
    return result
