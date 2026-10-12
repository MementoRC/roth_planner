"""TurboTax 1040 PDF parser — extracts MAGI components from exported PDF bundles.

Text-anchor approach (no AcroForm fields in TurboTax exports). Locates
Form 1040 and Schedule 1 pages by footer, then applies per-year regex maps.

pdfplumber import is DEFERRED into parse_form_1040_pdf to stay Pyodide-safe
(PR #49 lesson: module-level heavy imports break the public web build).
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


class Form1040ParseError(Exception):
    """Raised when a 1040 PDF cannot be parsed or the tax year is unsupported."""


# ---------------------------------------------------------------------------
# Shared regex fragments — ONE definition reused by every tax year below.
# ---------------------------------------------------------------------------
# Bug history (2026-09-14): ANCHORS[2025] was first added as a verbatim copy
# of 2024. The IRS relettered Form 1040 line 11 to "11a" for tax year 2025
# ("This is your adjusted gross income . . . 11a 123,456." instead of
# "... 11 123,456."). The old skip `(?:11\s+)?` requires "11" immediately
# followed by whitespace; against "11a" that fails, the optional group
# backtracks to zero-width, and the amount capture then starts matching at
# "11a" itself — `\d[\d,]*` grabs the digits "11" off the front of the line
# token and stops at the letter "a", silently returning 11.0 instead of
# 123456.0. The 2023 and 2024 layouts were NOT affected (their line tokens
# never carry an unexpected suffix).
#
# Fix: every anchor below is built from two reusable, newline-safe pieces:
#   _LEADER    — dot leaders / spaces between a label and its value; never
#                matches "\n", so an anchor can't accidentally bridge onto a
#                neighbouring line (rejected: a `[\s\S]`-based "run to the
#                trailing period" design was considered and rejected for
#                exactly this reason — on an empty box, such as a blank
#                taxable_ss or feie line, it can cross onto the
#                next line and silently capture an unrelated neighbour's
#                amount instead of correctly producing no match).
#   _line_skip — an OPTIONAL line-number token: the expected numeral plus at
#                most one lowercase letter suffix (tolerates "11" -> "11a",
#                "2a", "3b", "8d", etc.), followed by REQUIRED horizontal
#                whitespace. The amount itself is captured by an ATOMIC
#                group (`(?>...)`, Python 3.11+) followed by a negative
#                lookahead for a trailing letter. Atomic = no backtracking:
#                if a line-number-shaped token isn't fully consumed by
#                _line_skip (an unrecognised shape, e.g. two letters), the
#                amount capture cannot fall back to grabbing a truncated
#                prefix of it — the whole anchor simply fails to match. For
#                a required field (agi) that surfaces as Form1040ParseError,
#                a visible failure, instead of a silently wrong value.
# Checked 2026-09-14 against the 2023/2024/2025 TurboTax export layouts.

_LEADER = r"(?:[^\S\n]|\.)+"  # dot-leaders and/or horizontal space; never "\n"
_AMOUNT = r"(\(?-?\$?(?>\d[\d,]*)\)?)(?![a-zA-Z])"


def _line_skip(numeral: str) -> str:
    """Optional "<numeral><single-letter-suffix?><required-gap>" skip.

    Tolerates an IRS relettering (numeral gains/loses a trailing letter)
    without ever letting the amount capture swallow part of the token —
    see the module-level comment above _LEADER for the full mechanism.
    """
    return r"(?:" + numeral + r"[a-z]?[^\S\n]+)?"


_AGI_REGEX = r"This is your adjusted gross income" + _LEADER + _line_skip("11") + _AMOUNT
_TAX_EXEMPT_INTEREST_REGEX = r"Tax-exempt interest" + _LEADER + _line_skip("2") + _AMOUNT
_QUALIFIED_DIVIDENDS_REGEX = r"Qualified dividends" + _LEADER + _line_skip("3") + _AMOUNT
_ORDINARY_DIVIDENDS_REGEX = r"Ordinary dividends" + _LEADER + _line_skip("3") + _AMOUNT
# SS block has free text ("6a  b Taxable amount . . .") between the label and
# "6b" — bounded to 80 chars, but [^\n] (not [\s\S]) so it can never cross a
# newline onto a neighbouring line.
_TAXABLE_SS_REGEX = r"Social security benefits[^\n]{0,80}6b[^\S\n]+" + _AMOUNT
_FEIE_REGEX = r"Foreign earned income exclusion" + _LEADER + _line_skip("8") + _AMOUNT

# Line 24 lives on 1040 PAGE 2, not the footer-located page 1. Anchored on the
# FULL phrase, not a bare "24" token: the preceding line ("23 Other taxes ...
# 23 0.") ends in a literal 0, so a loose anchor silently captures that zero
# instead — the same silent-wrong-value failure mode as the 2025 "11"->"11a"
# bug documented at the top of this module. The line's text is identical in
# the 2023/2024/2025 TurboTax layouts.
_TOTAL_TAX_REGEX = (
    r"Add lines 22 and 23\. This is your total tax" + _LEADER + _line_skip("24") + _AMOUNT
)

# ---------------------------------------------------------------------------
# Per-year anchor maps
# ---------------------------------------------------------------------------
# Each anchor entry has:
#   form   : one of "f1040" (page 1), "f1040_p2" (page 2), or "sch1"
#   regex  : pattern with one capture group for the raw currency string
#   optional: if True, missing field → 0.0 (no error)
#
# Modelled on the TurboTax 2023 export layout. 2024 and 2025 use the same
# stable IRS line numbers (unchanged since the 2020 redesign) via the shared
# _line_skip("11")/_line_skip("2")/etc. fragments above, which tolerate
# 2025's "11" -> "11a" relettering without drifting into three separate
# copies. taxable_ss and feie legitimately no-match when those boxes are
# empty on a return, and both are `optional`, so they correctly default to
# 0.0.

ANCHORS: dict[int, dict[str, dict[str, Any]]] = {
    2023: {
        "agi": {"form": "f1040", "line": "11", "regex": _AGI_REGEX, "optional": False},
        "tax_exempt_interest": {
            "form": "f1040",
            "line": "2a",
            "regex": _TAX_EXEMPT_INTEREST_REGEX,
            "optional": True,
        },
        "qualified_dividends": {
            "form": "f1040",
            "line": "3a",
            "regex": _QUALIFIED_DIVIDENDS_REGEX,
            "optional": True,
        },
        "ordinary_dividends": {
            "form": "f1040",
            "line": "3b",
            "regex": _ORDINARY_DIVIDENDS_REGEX,
            "optional": True,
        },
        "taxable_ss": {
            "form": "f1040",
            "line": "6b",
            "regex": _TAXABLE_SS_REGEX,
            "optional": True,
        },
        "feie": {"form": "sch1", "line": "8d", "regex": _FEIE_REGEX, "optional": True},
        "total_federal_tax": {
            "form": "f1040_p2",
            "line": "24",
            "regex": _TOTAL_TAX_REGEX,
            "optional": True,
        },
    },
    2024: {
        "agi": {"form": "f1040", "line": "11", "regex": _AGI_REGEX, "optional": False},
        "tax_exempt_interest": {
            "form": "f1040",
            "line": "2a",
            "regex": _TAX_EXEMPT_INTEREST_REGEX,
            "optional": True,
        },
        "qualified_dividends": {
            "form": "f1040",
            "line": "3a",
            "regex": _QUALIFIED_DIVIDENDS_REGEX,
            "optional": True,
        },
        "ordinary_dividends": {
            "form": "f1040",
            "line": "3b",
            "regex": _ORDINARY_DIVIDENDS_REGEX,
            "optional": True,
        },
        "taxable_ss": {
            "form": "f1040",
            "line": "6b",
            "regex": _TAXABLE_SS_REGEX,
            "optional": True,
        },
        "feie": {"form": "sch1", "line": "8d", "regex": _FEIE_REGEX, "optional": True},
        "total_federal_tax": {
            "form": "f1040_p2",
            "line": "24",
            "regex": _TOTAL_TAX_REGEX,
            "optional": True,
        },
    },
    2025: {
        # Line 11 -> "11a" relettering (see module comment); _AGI_REGEX
        # already tolerates it via _line_skip("11")'s optional [a-z]? suffix.
        "agi": {"form": "f1040", "line": "11a", "regex": _AGI_REGEX, "optional": False},
        "tax_exempt_interest": {
            "form": "f1040",
            "line": "2a",
            "regex": _TAX_EXEMPT_INTEREST_REGEX,
            "optional": True,
        },
        "qualified_dividends": {
            "form": "f1040",
            "line": "3a",
            "regex": _QUALIFIED_DIVIDENDS_REGEX,
            "optional": True,
        },
        "ordinary_dividends": {
            "form": "f1040",
            "line": "3b",
            "regex": _ORDINARY_DIVIDENDS_REGEX,
            "optional": True,
        },
        "taxable_ss": {
            "form": "f1040",
            "line": "6b",
            "regex": _TAXABLE_SS_REGEX,
            "optional": True,
        },
        "feie": {"form": "sch1", "line": "8d", "regex": _FEIE_REGEX, "optional": True},
        "total_federal_tax": {
            "form": "f1040_p2",
            "line": "24",
            "regex": _TOTAL_TAX_REGEX,
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
    parser_version: str = "1.1.0"
    provenance: dict[str, Any] = field(default_factory=dict)
    # Appended last, with a default, deliberately: keeping it after every
    # pre-existing field means no positional construction call site shifts.
    total_federal_tax: float = 0.0

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
            "total_federal_tax": self.total_federal_tax,
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
            # Fallback stays "1.0.0" deliberately: a cached record with NO
            # parser_version key was by definition written before that field
            # was bumped -- i.e. by the 1.0.0 parser, which did not capture
            # Line 24. Defaulting it to the current version would relabel
            # legacy records as new ones and defeat any future migration
            # keyed off this field.
            parser_version=str(data.get("parser_version", "1.0.0")),
            provenance=dict(data.get("provenance", {})),
            total_federal_tax=float(data.get("total_federal_tax") or 0.0),
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

    Scope note — this MAGI matches Roth/ACA MAGI (IRC §408A / §36B). IRMAA
    MAGI (42 U.S.C. §1395r(i)(4)) also adds the FEIE back; use
    ``compute_irmaa_magi`` for it.

    The FEIE is added back as a magnitude: Schedule 1 line 8d prints the
    exclusion as a negative adjustment, which the parser may read as a
    negative number.
    """
    return agi + tax_exempt_interest + abs(feie)


def compute_irmaa_magi(agi: float, tax_exempt_interest: float, feie: float = 0.0) -> float:
    """Compute MAGI for IRMAA purposes per 42 U.S.C. §1395r(i)(4)(A).

    IRMAA MAGI is AGI "determined without regard to sections 135, 911, 931,
    and 933" of the Code, "increased by" tax-exempt interest. Section 911 is
    the foreign earned income exclusion, so the FEIE IS added back:
    AGI $200,000 + FEIE $20,000 (MFJ) is IRMAA MAGI $220,000, over the 2026
    Tier-1 threshold. An earlier fix wrongly dropped this add-back; audit-1008
    F-59 restored it. (§135 savings-bond interest and the §931/§933
    possession exclusions are not parsed and are treated as 0.)

    This is the source for any value that lands in
    ``Household.prior_year_magi`` (the IRMAA 2-year lookback slot). FEIE is
    added back as a magnitude, as in ``compute_magi``.
    """
    return agi + tax_exempt_interest + abs(feie)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_currency(raw: str) -> float:
    """Strip commas, dollar signs, and trailing dots; handle negative/parenthesized values.

    Supports: ``5,000`` → 5000.0, ``-5,000`` → -5000.0, ``(5,000)`` → -5000.0.
    """
    s = raw.strip().replace("$", "").replace(",", "").rstrip(".")
    if s.startswith("(") and s.endswith(")"):
        return -float(s[1:-1])
    return float(s)


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


def is_form_1040(pages: Sequence[str]) -> bool:
    """True if any page carries the IRS "Form 1040 (YYYY)" footer.

    Content-based detection independent of filename. Note a full TurboTax export
    can also list 1099 broker payer names, so the document-level classifier
    (engine/pdf_import.py) runs broker detection only AFTER this check.

    ``pages`` accepts a plain ``list[str]`` or a lazily-extracting ``Sequence``
    (see ``engine/pdf_page_text.py``) -- only iteration is used here."""
    return any(re.search(r"Form 1040\s*\((\d{4})\)", page or "", re.IGNORECASE) for page in pages)


def parse_form_1040_text(
    pages: Sequence[str],
    *,
    pdf_creator: str | None = None,
) -> Form1040Record:
    """Parse Form 1040 fields from a sequence of per-page text strings.

    Pure function — no I/O, no pdfplumber. ``pages`` mirrors pdfplumber's
    ``page.extract_text()`` output (one string per PDF page); it may be a
    plain ``list[str]`` or a lazily-extracting ``Sequence`` (see
    ``engine/pdf_page_text.py``) — this function only ever indexes,
    enumerates, and takes ``len()``, all of which lazy sequences support.

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
    # re.IGNORECASE: TurboTax PDFs render "SCHEDULE 1 (Form 1040)" uppercase
    sch1_page_index: int | None = None
    for idx, text in enumerate(pages):
        if re.search(r"Schedule 1\s*\(Form 1040\)", text, re.IGNORECASE):
            sch1_page_index = idx
            break

    # Step 2b: locate 1040 page 2 (holds Line 24 / total tax) — optional.
    # Its own footer carries an explicit "Page 2", which is a more direct
    # signal than "the 2nd match of the generic footer regex". Bounded to a
    # small window immediately after page 1 (NOT an unbounded scan of the
    # whole document, unlike steps 1/2 above): page 2 is physically the very
    # next page after page 1 in a TurboTax export, and an unbounded
    # scan would defeat the LazyPageTexts optimization this module depends on
    # (see TestLazyPageAccess) — a multi-hundred-page bundle with no page-2
    # marker at all would have every remaining page's text extracted just to
    # fail. A window of 2 pages tolerates one stray interleaved page (e.g. a
    # worksheet) without paying that cost.
    f1040_p2_index: int | None = None
    _p2_window_end = min(resolved_page + 3, len(pages))
    for idx in range(resolved_page + 1, _p2_window_end):
        if re.search(r"Form 1040\s*\((\d{4})\)\s*Page\s*2", pages[idx], re.IGNORECASE):
            f1040_p2_index = idx
            break

    # Step 3: apply anchors
    anchors = ANCHORS[resolved_year]
    f1040_text = pages[resolved_page]
    sch1_text = pages[sch1_page_index] if sch1_page_index is not None else ""

    def _apply(field_name: str) -> float:
        anchor = anchors[field_name]
        form = anchor["form"]
        if form == "sch1":
            # Schedule 1 absent -> feie always 0.0 regardless of optional flag
            if sch1_page_index is None:
                return 0.0
            page_text = sch1_text
        elif form == "f1040_p2":
            # Page 2 absent -> total_federal_tax 0.0; the panel distinguishes
            # "no 1040 imported" from "imported but Line 24 not found".
            if f1040_p2_index is None:
                return 0.0
            page_text = pages[f1040_p2_index]
        else:
            page_text = f1040_text
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
    total_federal_tax = _apply("total_federal_tax")

    magi = compute_magi(agi, tax_exempt_interest, feie)

    provenance: dict[str, Any] = {
        "f1040_page_index": resolved_page,
        "sch1_page_index": sch1_page_index,
        "f1040_p2_page_index": f1040_p2_index,
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
        total_federal_tax=total_federal_tax,
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

    Pages are wrapped in ``LazyPageTexts`` so only the pages
    ``parse_form_1040_text`` actually reads (the Form 1040 and Schedule 1
    footer pages — typically 2 of a document that can run to hundreds) are
    ever sent through pdfplumber's per-page text extraction. Parsing runs
    INSIDE the ``with`` block, since a page's text can only be extracted
    while its parent PDF is still open.
    """
    import io

    # Deferred: pdfplumber unavailable in Pyodide
    import pdfplumber

    from engine.pdf_page_text import LazyPageTexts

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = LazyPageTexts(pdf.pages)
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
        raw: dict[str, Any] = read_pii_json(_PDF_TAX_CACHE_PATH)
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


def scan_1040_folder(folder: Path) -> tuple[dict[int, Form1040Record], list[str]]:
    """Parse every Form 1040 PDF in *folder*, keyed by tax year, identifying them
    by *content* rather than filename (site-exported PDFs have unreliable names).
    Returns (records_by_year, errors).

    Delegates to the shared content-based router (engine.pdf_import), so non-1040
    PDFs in the same folder (brokerage statements, Koinly reports, extensions) are
    recognized and skipped -- never reported as parse failures. A single malformed
    1040 does not abort the scan; when two 1040s share a tax year the later one in
    sorted order wins. The import is deferred to avoid a circular import at module
    load (engine.pdf_import imports this module at top level)."""
    from engine.pdf_import import scan_pdf_folder

    result = scan_pdf_folder(folder)
    errors = [f"{name}: {msg}" for name, msg in result.errors]
    return result.form_1040_records, errors
