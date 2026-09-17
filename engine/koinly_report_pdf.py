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
from collections.abc import Sequence
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

# TODO(verify): the "Prepared for <name>" anchor is UNVERIFIED against a real
# Koinly report cover page -- confirm against
# PDF-Statements/koinly_2026_complete_tax_report_July.pdf and adjust the regex
# before relying on this in production.
_OWNER_NAME_RE = re.compile(r"Prepared for\s+(.+)", re.IGNORECASE)
_OWNER_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

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
    owner_key: str | None = None

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
            "owner_key": self.owner_key,
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
            owner_key=data.get("owner_key"),
        )


def _parse_currency(raw: str) -> float:
    """`$-2.02` -> -2.02, `1,234.56` -> 1234.56, `(5,000)` -> -5000.0."""
    s = raw.strip().replace("$", "").replace(",", "").rstrip(".")
    if s.startswith("(") and s.endswith(")"):
        return -float(s[1:-1])
    return float(s)


def _find_page(pages: Sequence[str], anchor: str) -> str | None:
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
    last_category_end = 0
    for label in INCOME_CATEGORIES:
        m = re.search(re.escape(label) + r"\s+" + _CURRENCY, income_text, re.IGNORECASE)
        per_category[label] = _parse_currency(m.group(1)) if m else 0.0
        if m:
            last_category_end = max(last_category_end, m.end())
    summed = sum(per_category.values())

    # Reported income Total. The Income and Expenses summaries are two side-by-side
    # columns on one page; pdfplumber flattens the page by vertical position, so
    # BOTH columns' "Total" lines land in the text and the shorter Expenses column's
    # "Total" frequently appears FIRST. Anchor the income Total to the first
    # line-start "Total" that appears AFTER the last income-category row -- the
    # income column's Total always follows its own last category, regardless of
    # which column is longer. This keeps the cross-check honest (it can still catch
    # a Koinly-added category the fixed sum misses) instead of latching onto the
    # Expenses total.
    reported_total: float | None = None
    tm = re.search(r"(?mi)^\s*Total\s+" + _CURRENCY, income_text[last_category_end:])
    if tm:
        reported_total = _parse_currency(tm.group(1))
    return summed, per_category, reported_total


def _search_owner_pattern(pages: Sequence[str], pattern: re.Pattern[str]) -> re.Match[str] | None:
    """Search *pattern* over ``pages`` incrementally, stopping as soon as a
    match is provably final. See ``extract_owner_key``'s docstring for the
    correctness proof; this returns EXACTLY what ``pattern.search("\\n".join
    (pages))`` would, just without necessarily reading every page to get there.

    Maintains ``prefix = "\\n".join(pages[:k])`` incrementally (append "\\n" +
    page text; no quadratic re-joins) and re-searches the growing prefix after
    each page. A match ending strictly before the end of the prefix is final
    and returned immediately; otherwise the walk continues. After the last
    page, falls back to a search of the full prefix (identical to the old
    eager behaviour) so the return value never diverges from it.
    """
    prefix = ""
    for i, page in enumerate(pages):
        prefix = page if i == 0 else prefix + "\n" + page
        m = pattern.search(prefix)
        if m is not None and m.end() < len(prefix):
            return m
    return pattern.search(prefix)


def extract_owner_key(pages: Sequence[str]) -> str | None:
    """Best-effort extraction of an owner-identifying string (name or email)
    from a Koinly report's cover/header text.

    TODO(verify): the "Prepared for <name>" anchor is UNVERIFIED against a
    real Koinly report cover page -- confirm against
    PDF-Statements/koinly_2026_complete_tax_report_July.pdf and adjust the
    regex before relying on this in production. Returns None (never guesses)
    when no name or email pattern is found, matching the design's documented
    fallback to manual owner selection in the UI.

    Reads pages incrementally via ``_search_owner_pattern`` (lazy-safe, same
    priority order as before: name, then email) instead of eagerly joining
    every page up front, stopping as soon as a match is PROVABLY final --
    identical to what an eager ``"\\n".join(pages)`` search would return, in
    every case:

    1. A match ending strictly before the end of the current accumulated
       prefix cannot be extended by appending more text. ``(.+)`` never
       matches ``"\\n"``, so such a match is already terminated by a ``"\\n"``
       (or the pattern's own end) that is already present in the prefix;
       appending text after that terminator cannot reach backward into the
       match.
    2. No earlier-starting match can appear as more text is appended.
       Consider a starting position ``p`` earlier than the found match's
       start that failed to match within the accumulated prefix. Pages are
       joined with a literal ``"\\n"``, so the character immediately
       following the old prefix is always ``"\\n"``. For the attempt at
       ``p`` to newly succeed after appending text, it would have to consume
       that ``"\\n"`` -- but it can only have failed at the very end of the
       prefix (a partial match earlier would already have been found), and
       every character it still needs at that point comes from ``\\s+``
       (which CAN match ``"\\n"``, but only when nothing non-whitespace has
       been consumed by the tail yet) or from the newline-free tail that
       comes after ``\\s+`` starts consuming non-whitespace. Since the found
       match's own text (its non-whitespace tail) lies strictly between
       ``p``'s anchor and the prefix end, everything from ``p`` up to the
       prefix end would have to be pure whitespace for ``p`` to still be
       "only needing \\s+" at the boundary -- contradicting the existence of
       the found match's non-whitespace content in that same span. So ``p``
       cannot resurrect as an earlier-starting match. Hence the leftmost
       match in the full joined text equals the first match found by this
       walk.

    This argument covers any pattern whose only newline-crossing element is
    a leading ``\\s+``/whitespace run followed by a newline-free tail --
    which is exactly ``_OWNER_NAME_RE``. ``_OWNER_EMAIL_RE`` =
    ``r"[\\w.+-]+@[\\w-]+\\.[\\w.-]+"`` has no whitespace element at all (a
    zero-length instance of that leading run), so it satisfies the argument
    trivially and, more strongly, can never span a page boundary in the
    first place -- every character class in it excludes ``"\\n"``, so a
    partial match run out of input at a prefix end always fails again (not
    "needs more") once the boundary ``"\\n"`` is the next character.

    Falls back to a full-prefix search after the last page in both cases, so
    the result is IDENTICAL to the old eager implementation always -- this
    only changes how many pages get read, never what gets returned. Pages
    already read while searching one pattern are cached by ``LazyPageTexts``,
    so trying the next pattern costs nothing for pages already visited.
    """
    name_m = _search_owner_pattern(pages, _OWNER_NAME_RE)
    if name_m:
        return name_m.group(1).strip().splitlines()[0].strip()
    email_m = _search_owner_pattern(pages, _OWNER_EMAIL_RE)
    if email_m:
        return email_m.group(0)
    return None


_TAX_YEAR_RE = re.compile(r"TAX\s+YEAR\s+(\d{4})", re.IGNORECASE)


def is_koinly_report(pages: list[str]) -> bool:
    """True if *pages* look like a Koinly crypto tax report.

    Content-based, since filenames are unreliable. Requires BOTH the vendor
    name "Koinly" AND the "TAX YEAR YYYY" marker that parse_koinly_text itself
    demands (see _TAX_YEAR_RE, reused here rather than duplicated) -- a
    detector must require at least what its parser requires, otherwise it
    claims files it cannot parse. A bare "koinly" mention (e.g. a TurboTax
    1099 import-source line naming Koinly as a data source) is not enough."""
    has_koinly = any("koinly" in (page or "").lower() for page in pages)
    has_tax_year = any(_TAX_YEAR_RE.search(page or "") for page in pages)
    return has_koinly and has_tax_year


def parse_koinly_text(pages: Sequence[str]) -> KoinlyReport:
    """Parse crypto YTD figures from Koinly report page texts. Pure -- no I/O.

    ``pages`` mirrors pdfplumber's ``page.extract_text()`` output (one
    string per page); it may be a plain ``list[str]`` or a lazily-extracting
    ``Sequence`` (see ``engine/pdf_page_text.py``).
    """
    year: int | None = None
    for text in pages:
        ym = _TAX_YEAR_RE.search(text)
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
        owner_key=extract_owner_key(pages),
    )


def parse_koinly_pdf(data: bytes) -> KoinlyReport:
    """Parse a Koinly report PDF from raw bytes. pdfplumber import deferred
    (Pyodide-safe) -- only local installs call this.

    Pages are wrapped in ``LazyPageTexts`` so ``page.extract_text()`` only
    runs for the pages ``parse_koinly_text`` actually reads (year marker,
    "Capital gains summary", "Income summary" -- typically 2 early pages of
    a report that can run to hundreds). Parsing runs INSIDE the ``with``
    block, since a page's text can only be extracted while its parent PDF
    is still open. ``extract_owner_key`` (called from ``parse_koinly_text``)
    reads pages incrementally too, stopping as soon as a match is provably
    final -- see its docstring for the proof.
    """
    import io

    import pdfplumber

    from engine.pdf_page_text import LazyPageTexts

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = LazyPageTexts(pdf.pages)
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
