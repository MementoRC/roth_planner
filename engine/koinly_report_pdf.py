"""Koinly crypto tax-report PDF parser -- extracts YTD crypto STCG/LTCG/income.

Text-anchor approach over the report's summary pages ("Capital gains summary",
"Income summary"). Locates sections by anchor across all pages (not fixed page
numbers), same shape as engine/tax_return_pdf.py.

pdfplumber import is DEFERRED into parse_koinly_pdf to stay Pyodide-safe
(PR #49 lesson: module-level heavy imports break the public web build).

Only three values are extracted, matching the three YTDSnapshot crypto fields:
  crypto_stcg   <- Capital gains summary -> Net gains -> Short term
  crypto_ltcg   <- Capital gains summary -> Net gains -> Long term
  crypto_income <- Income summary -> sum of the seven income categories

Income is SUMMED from the fixed category rows rather than read off the "Total"
line, because the Income summary shares a two-column page with an Expenses
summary that has its own "Total" -- summing the known categories is unambiguous
and yields a per-category provenance breakdown. The reported income Total is
still parsed when unambiguous and cross-checked (mismatch -> provenance note).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.secure_io import read_pii_json, write_pii_json


class KoinlyParseError(Exception):
    """Raised when a Koinly report PDF cannot be parsed."""


# Fixed Koinly "Income summary" category rows (report schema).
INCOME_CATEGORIES: tuple[str, ...] = (
    "Airdrop",
    "Fork",
    "Mining",
    "Reward",
    "Salary",
    "Lending interest",
    "Other income",
)

# Currency token: "$1,234.56", "$0.00", "$-2.02" (minus after the $).
_CURRENCY = r"\$\s*(-?[\d,]+(?:\.\d{1,2})?)"

PARSER_VERSION = "1.0.0"


@dataclass
class KoinlyReport:
    """Structured crypto YTD figures extracted from a Koinly tax-report PDF."""

    tax_year: int
    crypto_stcg: float
    crypto_ltcg: float
    crypto_income: float
    captured_at: str
    source: str = "koinly_pdf"
    parser_version: str = PARSER_VERSION
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tax_year": self.tax_year,
            "crypto_stcg": self.crypto_stcg,
            "crypto_ltcg": self.crypto_ltcg,
            "crypto_income": self.crypto_income,
            "captured_at": self.captured_at,
            "source": self.source,
            "parser_version": self.parser_version,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KoinlyReport:
        return cls(
            tax_year=int(data["tax_year"]),
            crypto_stcg=float(data["crypto_stcg"]),
            crypto_ltcg=float(data["crypto_ltcg"]),
            crypto_income=float(data["crypto_income"]),
            captured_at=str(data["captured_at"]),
            source=str(data.get("source", "koinly_pdf")),
            parser_version=str(data.get("parser_version", PARSER_VERSION)),
            provenance=dict(data.get("provenance", {})),
        )


def _parse_currency(raw: str) -> float:
    """`$-2.02` -> -2.02, `1,234.56` -> 1234.56, `(5,000)` -> -5000.0."""
    s = raw.strip().replace("$", "").replace(",", "").rstrip(".")
    if s.startswith("(") and s.endswith(")"):
        return -float(s[1:-1])
    return float(s)


def _find_page(pages: list[str], anchor: str) -> str | None:
    """Return the first page text containing *anchor* as its own section heading
    (case-insensitive), i.e. *anchor* starting a line, not preceded by a table-of-
    contents numeral like "1. ". The report's cover page lists every section title
    in a numbered "Content" index, which would otherwise false-positive-match the
    first page instead of the actual data page.
    """
    pattern = re.compile(r"(?m)^" + re.escape(anchor), re.IGNORECASE)
    for text in pages:
        if pattern.search(text):
            return text
    return None


def _extract_net_gains(cg_text: str) -> tuple[float, float]:
    """From the Capital gains summary page, return (short_term, long_term) net gains.

    The labels "Short term"/"Long term" repeat under every row (Proceeds,
    Acquisition costs, Profits, Losses, Net gains), so anchor on the "Net gains"
    line and take the two sub-lines that immediately follow it.
    """
    m = re.search(
        r"Net\s+gains\b[^\n]*\n\s*Short\s+term\s+"
        + _CURRENCY
        + r"\s*\n\s*Long\s+term\s+"
        + _CURRENCY,
        cg_text,
        re.IGNORECASE,
    )
    if not m:
        raise KoinlyParseError(
            "Could not locate the 'Net gains' Short term / Long term block on the "
            "Capital gains summary page."
        )
    return _parse_currency(m.group(1)), _parse_currency(m.group(2))


def _extract_income(income_text: str) -> tuple[float, dict[str, float], float | None]:
    """Return (summed_income, per_category, reported_total_or_None).

    Sums the fixed Koinly income categories. Each category label is followed by
    its own value as the first currency token on the line (income is the left
    column), so this is robust to the Expenses column sharing the page.
    """
    per_category: dict[str, float] = {}
    for label in INCOME_CATEGORIES:
        m = re.search(re.escape(label) + r"\s+" + _CURRENCY, income_text, re.IGNORECASE)
        per_category[label] = _parse_currency(m.group(1)) if m else 0.0
    summed = sum(per_category.values())

    # Reported income Total: only trust a line that STARTS with "Total" (the
    # income column's Total lands on its own line; the expenses Total shares a
    # line with an income category label and won't match a line-start anchor).
    reported_total: float | None = None
    tm = re.search(r"(?mi)^\s*Total\s+" + _CURRENCY, income_text)
    if tm:
        reported_total = _parse_currency(tm.group(1))
    return summed, per_category, reported_total


def parse_koinly_text(pages: list[str]) -> KoinlyReport:
    """Parse crypto YTD figures from Koinly report page texts. Pure -- no I/O."""
    year: int | None = None
    for text in pages:
        ym = re.search(r"TAX\s+YEAR\s+(\d{4})", text, re.IGNORECASE)
        if ym:
            year = int(ym.group(1))
            break
    if year is None:
        raise KoinlyParseError(
            "No 'TAX YEAR YYYY' marker found -- is this a Koinly complete tax report PDF?"
        )

    cg_text = _find_page(pages, "Capital gains summary")
    if cg_text is None:
        raise KoinlyParseError("No 'Capital gains summary' page found in the PDF.")
    stcg, ltcg = _extract_net_gains(cg_text)

    income_text = _find_page(pages, "Income summary")
    if income_text is None:
        raise KoinlyParseError("No 'Income summary' page found in the PDF.")
    income, per_category, reported_total = _extract_income(income_text)

    provenance: dict[str, Any] = {
        "income_by_category": per_category,
        "income_reported_total": reported_total,
        "pdf_pages_total": len(pages),
    }
    if reported_total is not None and abs(reported_total - income) > 0.01:
        provenance["income_total_mismatch"] = (
            f"summed categories ${income:.2f} != reported Total ${reported_total:.2f} "
            "(Koinly may have added an income category not in INCOME_CATEGORIES)"
        )

    return KoinlyReport(
        tax_year=year,
        crypto_stcg=stcg,
        crypto_ltcg=ltcg,
        crypto_income=income,
        captured_at=datetime.now(UTC).isoformat(),
        provenance=provenance,
    )


def parse_koinly_pdf(data: bytes) -> KoinlyReport:
    """Parse a Koinly report PDF from raw bytes. pdfplumber import deferred
    (Pyodide-safe) -- only local installs call this."""
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return parse_koinly_text(pages)


# ---------------------------------------------------------------------------
# Folder scanner
# ---------------------------------------------------------------------------


def scan_koinly_folder(folder: Path) -> tuple[KoinlyReport | None, list[str]]:
    """Parse the newest `*koinly*.pdf` in *folder*. Returns (report_or_None, errors).

    A single malformed file does not abort -- its error is collected and the
    next-newest candidate is tried."""
    errors: list[str] = []
    candidates = sorted(
        (p for p in folder.glob("*.[pP][dD][fF]") if "koinly" in p.name.lower()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for pdf_path in candidates:
        try:
            return parse_koinly_pdf(pdf_path.read_bytes()), errors
        except Exception as exc:  # noqa: BLE001 -- one bad file must not kill the scan
            errors.append(f"{pdf_path.name}: {exc}")
    return None, errors


# ---------------------------------------------------------------------------
# JSON cache
# ---------------------------------------------------------------------------

_KOINLY_CACHE_PATH = Path(__file__).resolve().parent.parent / ".koinly_cache.json"


def save_koinly_report(report: KoinlyReport) -> None:
    write_pii_json(_KOINLY_CACHE_PATH, report.to_dict())


def load_koinly_report() -> KoinlyReport | None:
    if not _KOINLY_CACHE_PATH.exists():
        return None
    try:
        raw = read_pii_json(_KOINLY_CACHE_PATH)
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return KoinlyReport.from_dict(raw)
    except (KeyError, ValueError, TypeError):
        return None
