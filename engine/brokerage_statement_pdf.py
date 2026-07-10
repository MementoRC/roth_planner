"""Brokerage monthly-statement PDF parser (Schwab, Vanguard) — extracts YTD
income directly from each statement's own Income Summary / Gain-Loss Summary
tables, and the account's stated tax treatment.

Text-anchor approach, same pattern as engine/tax_return_pdf.py. Statements
already split Tax-Exempt vs. Taxable dividends/interest and are inherently
single-account — this is what FinExtract's scraped brokerage endpoints
cannot provide (no tax-exempt split, no account-type discrimination;
confirmed against the live FinExtract server 2026-07-10).

Vanguard statements state account type explicitly in the header
("Individual brokerage account" vs "Roth IRA brokerage account") — parsed
with high confidence. Schwab statements never state account type anywhere;
Schwab records are always account_type="unknown" and require one-time UI
confirmation (see views/ytd_income.py) before counting toward YTD income.
Mirrors tax_return_pdf.Form1040Record.filing_status, which is deferred to
UI confirmation for the same reason: don't guess what the source doesn't say.

pdfplumber import is DEFERRED into parse_statement_pdf to stay Pyodide-safe
(same rationale as tax_return_pdf.py, PR #49 lesson).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.secure_io import read_pii_json, write_pii_json

# Account types a record can carry. "unknown" means the statement gave no
# reliable signal — MUST be excluded from YTD sums until a human confirms it.
ACCOUNT_TYPES = frozenset({"taxable", "traditional_ira", "roth_ira", "unknown"})


class StatementParseError(Exception):
    """Raised when a brokerage statement PDF cannot be parsed at all (e.g. no
    recognized broker, no account number found)."""


@dataclass
class BrokerageStatementRecord:
    """Structured YTD figures extracted from one broker's monthly/quarterly statement.

    All *_ytd fields are the statement's own "Year to Date" column — already
    cumulative from Jan 1, no accumulation across multiple statements needed.

    ``dividends_tax_exempt_ytd`` and ``interest_tax_exempt_ytd`` both map to
    models.ytd_income.YTDSnapshot.tax_exempt_interest_ytd (IRS 1040 line 2a
    treats exempt-interest dividends from muni funds the same as tax-exempt
    interest) — callers should sum both when populating that field.

    ``account_type`` is one of ACCOUNT_TYPES. Callers MUST NOT include a
    record in taxable YTD totals unless account_type == "taxable".
    """

    account_number: str  # e.g. "3413-3847" or "XXXX9320" — canonical identity, not the filename
    broker: str  # "schwab" | "vanguard"
    account_type: str  # one of ACCOUNT_TYPES
    statement_period_end: str  # ISO date
    interest_taxable_ytd: float
    interest_tax_exempt_ytd: float
    dividends_taxable_ytd: float
    dividends_tax_exempt_ytd: float
    stcg_net_ytd: float
    ltcg_net_ytd: float
    captured_at: str
    source: str = "pdf"
    parser_version: str = "1.0.0"
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.account_type not in ACCOUNT_TYPES:
            raise ValueError(f"Invalid account_type {self.account_type!r}, must be one of {ACCOUNT_TYPES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_number": self.account_number,
            "broker": self.broker,
            "account_type": self.account_type,
            "statement_period_end": self.statement_period_end,
            "interest_taxable_ytd": self.interest_taxable_ytd,
            "interest_tax_exempt_ytd": self.interest_tax_exempt_ytd,
            "dividends_taxable_ytd": self.dividends_taxable_ytd,
            "dividends_tax_exempt_ytd": self.dividends_tax_exempt_ytd,
            "stcg_net_ytd": self.stcg_net_ytd,
            "ltcg_net_ytd": self.ltcg_net_ytd,
            "captured_at": self.captured_at,
            "source": self.source,
            "parser_version": self.parser_version,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BrokerageStatementRecord:
        return cls(
            account_number=str(data["account_number"]),
            broker=str(data["broker"]),
            account_type=str(data.get("account_type", "unknown")),
            statement_period_end=str(data["statement_period_end"]),
            interest_taxable_ytd=float(data["interest_taxable_ytd"]),
            interest_tax_exempt_ytd=float(data["interest_tax_exempt_ytd"]),
            dividends_taxable_ytd=float(data["dividends_taxable_ytd"]),
            dividends_tax_exempt_ytd=float(data["dividends_tax_exempt_ytd"]),
            stcg_net_ytd=float(data["stcg_net_ytd"]),
            ltcg_net_ytd=float(data["ltcg_net_ytd"]),
            captured_at=str(data["captured_at"]),
            source=str(data.get("source", "pdf")),
            parser_version=str(data.get("parser_version", "1.0.0")),
            provenance=dict(data.get("provenance", {})),
        )


# ---------------------------------------------------------------------------
# Text parsers (pure -- no pdfplumber dependency)
# ---------------------------------------------------------------------------

_MONEY = r"\$?\(?-?[\d,]+\.\d{2}\)?"


def _parse_currency(raw: str) -> float:
    """Strip commas/dollar signs; handle parenthesized negatives."""
    s = raw.strip().replace("$", "").replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        return -float(s[1:-1])
    return float(s)


def _detect_broker(text: str) -> str:
    """Check Schwab before Vanguard: a Schwab statement's holdings commonly
    include Vanguard ETFs, so "Vanguard" alone is not a reliable signal."""
    if re.search(r"Schwab One", text):
        return "schwab"
    if re.search(r"Vanguard", text):
        return "vanguard"
    raise StatementParseError("Could not detect a recognized broker (Schwab, Vanguard) in this PDF's text.")


def parse_statement_text(pages: list[str]) -> BrokerageStatementRecord:
    """Detect broker, then dispatch to the broker-specific parser."""
    full_text = "\n".join(pages)
    broker = _detect_broker(full_text)
    if broker == "schwab":
        return _parse_schwab(full_text)
    return _parse_vanguard(full_text)


# --- Schwab -----------------------------------------------------------------
#
# Schwab's pdfplumber extract_text() strips ALL spaces between words in
# labels/headers (e.g. "Bank Sweep Interest" -> "BankSweepInterest",
# "Account Number" -> "AccountNumber") -- verified against a real 2026-06
# statement dump. Regexes below match the no-space form deliberately.

_SCHWAB_ACCOUNT_MASKED_RE = re.compile(r"\*{4}-\*\d{3}")
_SCHWAB_ACCOUNT_UNMASKED_RE = re.compile(r"\b\d{4}-\d{4}\b")
_SCHWAB_PERIOD_RE = re.compile(r"([A-Za-z]+)(\d{1,2})-(\d{1,2}),(\d{4})")


def _extract_schwab_account_number(text: str) -> str:
    """Prefer the masked account number (appears on most pages); fall back
    to the unmasked form (appears once, on the summary page). The account
    number is not adjacent to the "AccountNumber" label -- it sits on a
    registration-type data row a couple of lines below it, and the
    registration text (e.g. "DESIGNATEDBENEPLAN/TOD") varies per account,
    so we anchor on the account number's own fixed digit-hyphen-digit shape
    instead of the surrounding registration text."""
    m = _SCHWAB_ACCOUNT_MASKED_RE.search(text)
    if m:
        return m.group(0)
    m = _SCHWAB_ACCOUNT_UNMASKED_RE.search(text)
    if m:
        return m.group(0)
    raise StatementParseError("No account number found. Ensure this is a complete Schwab monthly statement export.")


def _row_ytd_pair(label: str, text: str) -> tuple[float, float]:
    """Extract (YTD Tax-Exempt, YTD Taxable) for a Schwab Income Summary row.

    Row flattens to label + 4 numbers: This-Period Tax-Exempt, This-Period
    Taxable, YTD Tax-Exempt, YTD Taxable. *label* must already be in Schwab's
    no-space form (e.g. "BankSweepInterest", not "Bank Sweep Interest").
    """
    pattern = rf"{re.escape(label)}\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})"
    m = re.search(pattern, text)
    if not m:
        return 0.0, 0.0
    return _parse_currency(m.group(3)), _parse_currency(m.group(4))


def _extract_schwab_period_end(text: str) -> str:
    """Schwab's period renders as e.g. "June1-30,2026" (no spaces) -- parse
    the LAST day of the range as the statement period end, in ISO form."""
    m = _SCHWAB_PERIOD_RE.search(text)
    if not m:
        raise StatementParseError("No statement period found in Schwab statement text.")
    month, _day1, day2, year = m.groups()
    return datetime.strptime(f"{month} {day2} {year}", "%B %d %Y").date().isoformat()


def _parse_schwab(full_text: str) -> BrokerageStatementRecord:
    account_number = _extract_schwab_account_number(full_text)
    interest_tax_exempt, interest_taxable = _row_ytd_pair("BankSweepInterest", full_text)
    dividends_tax_exempt, dividends_taxable = _row_ytd_pair("CashDividends", full_text)

    stcg_net, ltcg_net = 0.0, 0.0
    gain_loss_m = re.search(rf"YTD\s+({_MONEY})\s+({_MONEY})", full_text)
    if gain_loss_m:
        stcg_net = _parse_currency(gain_loss_m.group(1))
        ltcg_net = _parse_currency(gain_loss_m.group(2))

    return BrokerageStatementRecord(
        account_number=account_number,
        broker="schwab",
        account_type="unknown",  # Schwab statements never state this -- see module docstring
        statement_period_end=_extract_schwab_period_end(full_text),
        interest_taxable_ytd=interest_taxable,
        interest_tax_exempt_ytd=interest_tax_exempt,
        dividends_taxable_ytd=dividends_taxable,
        dividends_tax_exempt_ytd=dividends_tax_exempt,
        stcg_net_ytd=stcg_net,
        ltcg_net_ytd=ltcg_net,
        captured_at=datetime.now(UTC).isoformat(),
        provenance={"pdf_pages_total": full_text.count("--- page")},
    )


# --- Vanguard -----------------------------------------------------------------

# Order matters: "Roth IRA brokerage account" also contains "IRA brokerage
# account", so check the more specific pattern first.
_VANGUARD_ACCOUNT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Roth IRA brokerage account—(XXXX\d+)"), "roth_ira"),
    (re.compile(r"Traditional IRA brokerage account—(XXXX\d+)"), "traditional_ira"),
    (re.compile(r"\bIRA brokerage account—(XXXX\d+)"), "traditional_ira"),  # UNVERIFIED -- see plan Non-goals
    (re.compile(r"Individual brokerage account—(XXXX\d+)"), "taxable"),
]
_VANGUARD_ACCOUNT_FALLBACK_RE = re.compile(r"account—(XXXX\d+)")
_VANGUARD_PERIOD_RE = re.compile(r"([A-Za-z]+) (\d{1,2}), (\d{4}), quarter-to-date statement")


def _detect_vanguard_account(text: str) -> tuple[str, str]:
    """Return (account_number, account_type). Falls back to account_type
    "unknown" (never guessed) if the header states an account number but no
    recognized type pattern matches."""
    for pattern, account_type in _VANGUARD_ACCOUNT_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1), account_type
    m = _VANGUARD_ACCOUNT_FALLBACK_RE.search(text)
    if m:
        return m.group(1), "unknown"
    raise StatementParseError("No account number found. Ensure this is a complete Vanguard statement export.")


def _extract_vanguard_income_row(text: str) -> tuple[float, float, float, float, float, float]:
    """Extract the Income summary table's Year-to-date row:
    (dividends, interest, tax_exempt_interest, stcg, ltcg, other_income).

    Anchoring on "Year-to-date" alone is safe even though a differently
    shaped "Year-to-date income" summary box also exists on an earlier page:
    that box's "Year-to-date" is followed by the word "income", not a money
    token, so this pattern skips past it to the real table row.
    """
    pattern = rf"Year-to-date\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})"
    m = re.search(pattern, text)
    if not m:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    return tuple(_parse_currency(m.group(i)) for i in range(1, 7))  # type: ignore[return-value]


def _extract_vanguard_period_end(text: str) -> str:
    m = _VANGUARD_PERIOD_RE.search(text)
    if not m:
        raise StatementParseError("No statement period found in Vanguard statement text.")
    month, day, year = m.groups()
    return datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date().isoformat()


def _parse_vanguard(full_text: str) -> BrokerageStatementRecord:
    account_number, account_type = _detect_vanguard_account(full_text)
    dividends, interest, tax_exempt_interest, stcg, ltcg, other = _extract_vanguard_income_row(full_text)

    return BrokerageStatementRecord(
        account_number=account_number,
        broker="vanguard",
        account_type=account_type,
        statement_period_end=_extract_vanguard_period_end(full_text),
        interest_taxable_ytd=interest,
        interest_tax_exempt_ytd=tax_exempt_interest,
        dividends_taxable_ytd=dividends,
        # Vanguard's "Dividends" column is assumed to already exclude tax-exempt
        # distributions (which land in the separate "Tax-exempt interest" column)
        # -- UNVERIFIED against a real muni-holding Vanguard account, since
        # neither sampled account holds municipal funds. Revisit if a Vanguard
        # statement with muni holdings becomes available.
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=stcg,
        ltcg_net_ytd=ltcg,
        captured_at=datetime.now(UTC).isoformat(),
        provenance={"other_income_ytd": other, "pdf_pages_total": full_text.count("--- page")},
    )
