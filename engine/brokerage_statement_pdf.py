"""Brokerage monthly-statement PDF parser (Schwab, Vanguard, IBKR, Fidelity, UBS)
— extracts YTD income directly from each statement's own Income Summary /
Gain-Loss Summary tables, and the account's stated tax treatment.

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

IBKR's consolidated "Activity Statement" PDF is fundamentally different in
shape: ONE PDF contains MULTIPLE accounts, each with its own "Account
Information" table stating a "Customer Type" explicitly (Individual,
IRA-Traditional Rollover, IRA-Roth New) — so IBKR accounts, like Vanguard's,
never need the manual "unknown" confirmation step Schwab requires. IBKR's
Cash Report gives no tax-exempt/taxable or qualified/ordinary dividend split
at all (same limitation as Schwab/Vanguard) — both `*_tax_exempt_ytd` fields
are always 0.0 for IBKR records, UNVERIFIED against a muni-holding IBKR
account since neither sampled account holds one.

Fidelity's consolidated statement is also multi-account (like IBKR), but its
sampled accounts (a Rollover IRA and an HSA) are both already excluded from
taxable YTD income by account_type, so its Income Summary total is mapped
into dividends_taxable_ytd purely for informational/display completeness —
it is NOT actually taxable, and there is no "Taxable" column in this
statement's Income Summary at all (only "Tax-deferred"/"Tax-free").

UBS statements are single-account, self-described in the header as
"Account type: Company Sponsored Stock Plan" -- not one of ACCOUNT_TYPES,
and almost certainly taxable (a stock-plan account is definitionally not an
IRA or HSA), but the statement text never uses the word "taxable" about the
account itself, so UBS records use account_type="unknown" pending UI
confirmation -- same rule as Schwab. Don't guess what the source doesn't say.
UBS gives NO tax-exempt split and NO qualified/ordinary dividend split
anywhere in the document (verified across all 10 pages of a real statement),
so both `*_tax_exempt_ytd` fields are always 0.0 for UBS -- same limitation
as IBKR. CRITICAL LAYOUT TRAP: UBS's page 3 renders FOUR side-by-side panels
that pdfplumber's extract_text() interleaves line-by-line with no column
awareness, producing rows like "transferred in 0.00 50,125.82 Short term
0.00 24,365.65 0.00" where the "Summary of gains and losses" panel is glued
onto the "Change in account value" panel -- every UBS regex below MUST
anchor on its own row label and must NOT rely on line ordering or on a row
occupying its own line. Unlike Schwab, UBS does NOT collapse spaces in
multi-word labels (verified: "Common stock" extracts with its space intact)
-- UBS regexes match the spaced label form.

pdfplumber import is DEFERRED into parse_statement_pdf to stay Pyodide-safe
(same rationale as tax_return_pdf.py, PR #49 lesson).
"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.secure_io import read_pii_json, write_pii_json

# Account types a record can carry. "unknown" means the statement gave no
# reliable signal — MUST be excluded from YTD sums until a human confirms it.
# "hsa" is excluded from taxable YTD income the same way IRA accounts are --
# dividends/interest earned *inside* an HSA are never currently-taxable
# events. This does NOT model HSA *distribution* taxation (qualified medical
# withdrawals are tax-free; non-qualified withdrawals before age 65 are
# ordinary income plus a 20% penalty; after 65, ordinary income with no
# penalty) -- a future feature would need to track HSA distributions as their
# own income category once withdrawal modeling exists. Not built here.
ACCOUNT_TYPES = frozenset({"taxable", "traditional_ira", "roth_ira", "hsa", "unknown"})


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
    owner_key: str | None = None

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
            "owner_key": self.owner_key,
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
            owner_key=data.get("owner_key"),
        )


# ---------------------------------------------------------------------------
# Text parsers (pure -- no pdfplumber dependency)
# ---------------------------------------------------------------------------

_MONEY = r"\$?\(?-?[\d,]+\.\d{2}\)?"


def _parse_currency(raw: str) -> float:
    """Strip commas/dollar signs; handle parenthesized negatives.

    UBS uses a leading minus instead of parens (e.g. "-50,041.81") -- verified
    this already round-trips correctly with no extension needed: after the
    strip above, `float("-50041.81")` parses the sign natively.
    """
    s = raw.strip().replace("$", "").replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        return -float(s[1:-1])
    return float(s)


def detect_broker(text: str) -> str | None:
    """Return the broker key for *text*, or None if no recognized broker.

    Content-based (filenames are unreliable). UBS is checked FIRST, on its
    full legal name "UBS Financial Services Inc." -- the most specific anchor
    of the five, so a UBS statement that happens to name another fund family
    (e.g. Vanguard) among its holdings is never misrouted to that broker's
    parser before UBS gets a chance to match. Schwab and IBKR are checked
    next, before Vanguard: both Schwab's and IBKR's holdings can include
    Vanguard-branded funds (confirmed for IBKR: the sampled Roth account
    holds Vanguard Mid-Cap Index Fund Admiral / VIMAX), so "Vanguard" alone
    is not a reliable signal. Fidelity is checked last, on a bare "Fidelity"
    match (no more specific single distinguishing phrase found in a real
    sample) -- safe regardless of ordering here since a UBS/Schwab/IBKR/
    Vanguard statement that happens to mention a Fidelity-branded fund in its
    holdings already matches its own broker check above and returns before
    reaching this one.

    Note: a TurboTax Form 1040 export can list these same broker names as 1099
    payers, so the document-level classifier (engine/pdf_import.py) runs its
    1040 check BEFORE calling this, ensuring a tax return is never misread as a
    brokerage statement."""
    if re.search(r"UBS Financial Services Inc\.", text):
        return "ubs"
    if re.search(r"Schwab One", text):
        return "schwab"
    if re.search(r"Interactive Brokers", text):
        return "ibkr"
    if re.search(r"Vanguard", text):
        return "vanguard"
    if re.search(r"Fidelity", text, re.IGNORECASE):
        return "fidelity"
    return None


def _detect_broker(text: str) -> str:
    """Strict variant of :func:`detect_broker` that raises when no broker is
    recognized. Used by the single-document parse path."""
    broker = detect_broker(text)
    if broker is None:
        raise StatementParseError(
            "Could not detect a recognized broker (UBS, Schwab, Vanguard, IBKR, Fidelity) in this PDF's text."
        )
    return broker


# --- Owner-key extraction (account-holder name) -----------------------------
#
# Best-effort per-broker holder-name extraction for the owner-attribution
# ledger (see engine/pdf_owner.py, engine/pdf_ledger.py). Returns None (never
# guesses) when no recognizable holder-name line is found -- the caller falls
# back to manual owner selection in the UI, same safety rule as account_type
# "unknown".

_SCHWAB_HOLDER_NAME_RE = re.compile(
    r"AccountNumber StatementPeriod\s*\n([A-Z]+)\n", re.MULTILINE
)
_VANGUARD_HOLDER_NAME_RE = re.compile(
    r"account—XXXX\d+ Vanguard Personal Investor\s*\n(.+?)\s+\d{3}-\d{3}-\d{4}"
)


def extract_owner_key(full_text: str) -> str | None:
    """Best-effort extraction of the account-holder's name from statement
    text, for owner attribution. Dispatches by detected broker; returns None
    (never guesses) if no recognized broker or no holder-name line matches.

    TODO(verify): only Schwab's and Vanguard's single-holder patterns are
    covered against the real fixtures captured in this repo. IBKR and
    Fidelity's per-account "Account Information" sections were not inspected
    for a holder-name line as part of this plan -- extend
    _detect_ibkr_account / _detect_fidelity_account-style anchors if needed;
    until then, IBKR/Fidelity records fall back to owner_key=None (manual
    confirmation in the UI), which is an acceptable degraded outcome.
    """
    broker = detect_broker(full_text)
    if broker == "schwab":
        m = _SCHWAB_HOLDER_NAME_RE.search(full_text)
        return m.group(1) if m else None
    if broker == "vanguard":
        m = _VANGUARD_HOLDER_NAME_RE.search(full_text)
        return m.group(1).strip() if m else None
    return None


def parse_statement_text(pages: list[str]) -> list[BrokerageStatementRecord]:
    """Detect broker, then dispatch to the broker-specific parser.

    Returns one record per account found in the document. Schwab, Vanguard
    and UBS statements are always single-account (one-element list); IBKR
    and Fidelity's consolidated statements can contain multiple accounts.
    """
    full_text = "\n".join(pages)
    broker = _detect_broker(full_text)
    if broker == "schwab":
        return [_parse_schwab(full_text)]
    if broker == "vanguard":
        return [_parse_vanguard(full_text)]
    if broker == "ubs":
        return [_parse_ubs(full_text)]
    if broker == "fidelity":
        return _parse_fidelity(full_text)
    return _parse_ibkr(full_text)


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
        owner_key=extract_owner_key(full_text),
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
        owner_key=extract_owner_key(full_text),
    )


# --- Interactive Brokers ------------------------------------------------------
#
# IBKR's consolidated "Activity Statement" PDF contains multiple accounts in
# one document -- unlike Schwab/Vanguard, which are always single-account.
# Each account gets its own "Account Information" table (confirmed: the
# string "Account Information" appears exactly once per account, nowhere
# else, in a real 3-account sample) stating a "Customer Type" explicitly --
# IBKR accounts never need the manual "unknown" confirmation step Schwab
# requires.

_IBKR_ACCOUNT_NUMBER_RE = re.compile(r"Account ([A-Z]\d{8})\b")

# Order doesn't matter here (patterns are mutually exclusive), but each must
# anchor on the literal "Customer Type" prefix specifically -- every IBKR
# account's Information table also has an unrelated "Account Type Individual"
# line (account structure, not tax treatment) -- a bare "Type Individual"
# substring match would misfire on all three sampled accounts.
_IBKR_CUSTOMER_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Customer Type:?\s*IRA-Roth"), "roth_ira"),
    (re.compile(r"Customer Type:?\s*IRA-Traditional"), "traditional_ira"),
    (re.compile(r"Customer Type:?\s*Individual"), "taxable"),
]


def _detect_ibkr_account(section_text: str) -> tuple[str, str]:
    """Return (account_number, account_type) for one IBKR account section.

    Falls back to account_type "unknown" (never guessed) if the section
    states a Customer Type this parser doesn't recognize -- same safety
    rule as Vanguard's _detect_vanguard_account fallback.
    """
    m = _IBKR_ACCOUNT_NUMBER_RE.search(section_text)
    if not m:
        raise StatementParseError("No account number found in IBKR account section.")
    account_number = m.group(1)
    for pattern, account_type in _IBKR_CUSTOMER_TYPE_PATTERNS:
        if pattern.search(section_text):
            return account_number, account_type
    return account_number, "unknown"


_IBKR_SECTION_START_RE = re.compile(r"Account Information")


def _split_ibkr_sections(full_text: str) -> list[str]:
    """Split a consolidated IBKR statement's full text into one chunk per
    account, each chunk starting at its "Account Information" table.

    The roster/summary page (listing all accounts before any per-account
    detail) has no "Account Information" table of its own (confirmed against
    a real 3-account sample), so it is never treated as a bogus section here.
    """
    starts = [m.start() for m in _IBKR_SECTION_START_RE.finditer(full_text)]
    if not starts:
        raise StatementParseError("No IBKR account sections found (no 'Account Information' tables detected).")
    sections = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(full_text)
        sections.append(full_text[start:end])
    return sections


def _ibkr_row_ytd(label: str, section_text: str) -> float:
    """Extract the Year-to-Date figure from an IBKR Cash Report row.

    Every row in this table renders as the label followed by 5 numeric
    columns (Total, Securities, Futures, Month to Date, Year to Date), with
    YTD always the LAST whitespace-separated token on the line -- confirmed
    against a real dump. Matching just "the first number after the label"
    would capture the Total column (always 0.00 in the sampled accounts)
    instead, silently under-reporting every account's real YTD figure.
    """
    m = re.search(rf"^{re.escape(label)}((?:\s+{_MONEY})+)\s*$", section_text, re.MULTILINE)
    if not m:
        return 0.0
    return _parse_currency(m.group(1).split()[-1])


def _extract_ibkr_cash_report(section_text: str) -> tuple[float, float]:
    """Extract (dividends_ytd, interest_ytd) from one account section's Cash
    Report table. A row is entirely absent when that account had zero cash
    flow of that type this period (confirmed: the sampled Traditional IRA
    account has no Dividends or Broker Interest row at all) -- absence
    correctly falls back to 0.0, it is not a parse error.
    """
    dividends = _ibkr_row_ytd("Dividends", section_text)
    interest = _ibkr_row_ytd("Broker Interest Paid and Received", section_text)
    return dividends, interest


_IBKR_PERFORMANCE_SUMMARY_HEADER_RE = re.compile(r"Month & Year to Date Performance Summary")
_IBKR_TOTAL_ASSET_CLASS_ROW_RE = re.compile(rf"^Total \D+?((?:\s+{_MONEY}){{6}})\s*$", re.MULTILINE)


def _extract_ibkr_gains(section_text: str) -> tuple[float, float]:
    """Extract (stcg_net_ytd, ltcg_net_ytd) from the "Month & Year to Date
    Performance Summary" table, summed across every asset class present.

    This statement has THREE differently-titled "...Performance Summary"
    tables; only this one carries realized S/T and L/T YTD columns. There is
    no single "Total (All Assets)" row in this table (confirmed against a
    real dump) -- only one "Total <AssetClass>" row per asset class (e.g.
    "Total Stocks", "Total Mutual Funds") -- so every such row between this
    header and the account's "Cash Report" (which always follows it,
    confirmed ordering) must be summed, not just the first one matched.
    Each row's 6 numeric columns are
    [MTM_MTD, MTM_YTD, RealizedST_MTD, RealizedST_YTD, RealizedLT_MTD, RealizedLT_YTD].
    """
    header_m = _IBKR_PERFORMANCE_SUMMARY_HEADER_RE.search(section_text)
    if not header_m:
        return 0.0, 0.0
    table_text = section_text[header_m.end() :]
    cash_report_idx = table_text.find("Cash Report")
    if cash_report_idx != -1:
        table_text = table_text[:cash_report_idx]
    stcg = 0.0
    ltcg = 0.0
    for row_m in _IBKR_TOTAL_ASSET_CLASS_ROW_RE.finditer(table_text):
        tokens = row_m.group(1).split()
        stcg += _parse_currency(tokens[3])
        ltcg += _parse_currency(tokens[5])
    return stcg, ltcg


_IBKR_STATEMENT_DATE_RE = re.compile(r"Activity Statement[ \n-]+([A-Za-z]+ \d{1,2}, \d{4})")


def _extract_ibkr_period_end(full_text: str) -> str:
    """The statement's as-of date is document-wide, not per-account --
    identical across every account section (confirmed: 3 identical matches
    in a real 3-account sample)."""
    m = _IBKR_STATEMENT_DATE_RE.search(full_text)
    if not m:
        raise StatementParseError("No statement date found in IBKR statement text.")
    return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()


def _parse_ibkr(full_text: str) -> list[BrokerageStatementRecord]:
    sections = _split_ibkr_sections(full_text)
    period_end = _extract_ibkr_period_end(full_text)
    records = []
    for section in sections:
        account_number, account_type = _detect_ibkr_account(section)
        dividends, interest = _extract_ibkr_cash_report(section)
        stcg, ltcg = _extract_ibkr_gains(section)
        records.append(
            BrokerageStatementRecord(
                account_number=account_number,
                broker="ibkr",
                account_type=account_type,
                statement_period_end=period_end,
                interest_taxable_ytd=interest,
                # IBKR's Cash Report gives no tax-exempt/taxable split at all
                # (unlike Schwab/Vanguard) -- UNVERIFIED against a muni-holding
                # IBKR account, since neither sampled account holds one.
                interest_tax_exempt_ytd=0.0,
                dividends_taxable_ytd=dividends,
                dividends_tax_exempt_ytd=0.0,
                stcg_net_ytd=stcg,
                ltcg_net_ytd=ltcg,
                captured_at=datetime.now(UTC).isoformat(),
                provenance={"pdf_pages_total": full_text.count("--- page")},
                owner_key=None,  # IBKR owner extraction out of scope -- see extract_owner_key TODO(verify)
            )
        )
    return records


# --- Fidelity -----------------------------------------------------------------
#
# Fidelity's consolidated statement PDF is also multi-account (like IBKR),
# but each account's section-start marker is shaped differently: a bare
# "Account # <number>" repeats on EVERY page of that account's section
# (Account Summary, Holdings, Activity, Cash Flow all restate it -- confirmed
# against a real dump), so it is not usable alone as a splitter anchor. The
# 2-line combo "Account # <number>\nAccount Summary" occurs exactly once per
# account and is the correct anchor. Kept as its own function rather than
# sharing IBKR's splitter -- the marker shapes are meaningfully different.

_FIDELITY_ACCOUNT_NUMBER_RE = re.compile(r"\b(\d{3}-\d{6})\b")

# Order doesn't matter -- both phrases are disjoint and non-overlapping.
_FIDELITY_ACCOUNT_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"HEALTH SAVINGS ACCOUNT", re.IGNORECASE), "hsa"),
    (re.compile(r"ROLLOVER IRA", re.IGNORECASE), "traditional_ira"),
]


def _detect_fidelity_account(section_text: str) -> tuple[str, str]:
    """Return (account_number, account_type) for one Fidelity account section.
    Falls back to "unknown" (never guessed) for any account type this parser
    doesn't recognize -- same safety rule as Vanguard/IBKR fallbacks."""
    m = _FIDELITY_ACCOUNT_NUMBER_RE.search(section_text)
    if not m:
        raise StatementParseError("No account number found in Fidelity account section.")
    account_number = m.group(1)
    # Restrict the account-type search to a window near the section start --
    # the account-name header line (e.g. "CLAUDE R CIRBA - ROLLOVER IRA")
    # always appears right after "Account Summary" (confirmed against a real
    # dump). Searching the WHOLE section risks a false match against
    # unrelated boilerplate or cross-references to other accounts appearing
    # later in that account's own pages.
    header_window = section_text[:200]
    for pattern, account_type in _FIDELITY_ACCOUNT_TYPE_PATTERNS:
        if pattern.search(header_window):
            return account_number, account_type
    return account_number, "unknown"


_FIDELITY_SECTION_START_RE = re.compile(r"Account # \d{3}-\d{6}\nAccount Summary")


def _split_fidelity_sections(full_text: str) -> list[str]:
    """Split a consolidated Fidelity statement's full text into one chunk per
    account, each chunk starting at its "Account # <number>\nAccount Summary"
    header (confirmed: occurs exactly once per account in a real 2-account
    sample). A portfolio-level combined summary page precedes the first
    account's Account Summary and has no such marker of its own, so it is
    naturally excluded, same as IBKR's roster page.
    """
    starts = [m.start() for m in _FIDELITY_SECTION_START_RE.finditer(full_text)]
    if not starts:
        raise StatementParseError(
            "No Fidelity account sections found (no 'Account # ... Account Summary' header detected)."
        )
    sections = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(full_text)
        sections.append(full_text[start:end])
    return sections


_FIDELITY_INCOME_SUMMARY_HEADER_RE = re.compile(r"Income Summary")
_FIDELITY_TOTAL_INCOME_ROW_RE = re.compile(rf"^Total\s+({_MONEY})\s+({_MONEY})\s*$", re.MULTILINE)


def _extract_fidelity_income_ytd(section_text: str) -> float:
    """Extract the Income Summary's "Total" row Year-to-Date figure.

    Fidelity's Income Summary gives only ONE combined YTD total per account
    -- a "Tax-deferred" row for the Rollover IRA, a "Tax-free" row for the
    HSA, never both on the same account, and no "Taxable" column exists at
    all (confirmed against a real dump). Mapped into dividends_taxable_ytd
    purely for informational/display completeness; this is NOT actually
    taxable income -- the record's account_type ("traditional_ira"/"hsa")
    already excludes the whole record from taxable YTD sums via
    partition_by_account_type.
    """
    header_m = _FIDELITY_INCOME_SUMMARY_HEADER_RE.search(section_text)
    if not header_m:
        return 0.0
    row_m = _FIDELITY_TOTAL_INCOME_ROW_RE.search(section_text, header_m.end())
    if not row_m:
        return 0.0
    return _parse_currency(row_m.group(2))


_FIDELITY_PERIOD_RE = re.compile(r"[A-Za-z]+ \d{1,2}, \d{4} - ([A-Za-z]+ \d{1,2}, \d{4})")


def _extract_fidelity_period_end(full_text: str) -> str:
    """The statement period renders as a repeating "<start> - <end>" range
    header on every page (confirmed: identical across a real 18-page sample)
    -- the period end is the second date in the range."""
    m = _FIDELITY_PERIOD_RE.search(full_text)
    if not m:
        raise StatementParseError("No statement period found in Fidelity statement text.")
    return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()


def _parse_fidelity(full_text: str) -> list[BrokerageStatementRecord]:
    sections = _split_fidelity_sections(full_text)
    period_end = _extract_fidelity_period_end(full_text)
    records = []
    for section in sections:
        account_number, account_type = _detect_fidelity_account(section)
        income_ytd = _extract_fidelity_income_ytd(section)
        records.append(
            BrokerageStatementRecord(
                account_number=account_number,
                broker="fidelity",
                account_type=account_type,
                statement_period_end=period_end,
                interest_taxable_ytd=0.0,
                interest_tax_exempt_ytd=0.0,
                dividends_taxable_ytd=income_ytd,
                dividends_tax_exempt_ytd=0.0,
                stcg_net_ytd=0.0,
                ltcg_net_ytd=0.0,
                captured_at=datetime.now(UTC).isoformat(),
                provenance={"pdf_pages_total": full_text.count("--- page")},
                owner_key=None,  # Fidelity owner extraction out of scope -- see extract_owner_key TODO(verify)
            )
        )
    return records


# --- UBS -----------------------------------------------------------------
#
# UBS's account identity anchors ("Account number:", "Account type:") appear
# verbatim and identically on every page, unlike Schwab's registration-type
# indirection. UBS's page 3 interleaves FOUR side-by-side panels line-by-line
# (see module docstring's CRITICAL LAYOUT TRAP paragraph) -- every regex here
# anchors strictly on its own row label, never on line position or on a row
# occupying its own line, so the glued-together panel text below is parsed
# correctly:
#   "transferred in 0.00 50,125.82 Short term 0.00 24,365.65 0.00"
#   "Withdrawals and fees, Long term 0.00 0.00 406,840.68"

_UBS_ACCOUNT_RE = re.compile(r"Account number:\s*([A-Z0-9][A-Z0-9 \-]*?)\s*$", re.MULTILINE)

# UBS has no "Statement Period:" phrase anywhere -- the only recurring period
# marker is the "<Month> <YYYY> ($) Year to date" column header on the
# Dividend and interest income table, which is verbatim and appears on every
# relevant page.
_UBS_PERIOD_RE = re.compile(r"([A-Z][a-z]+)\s+(\d{4})\s+\(\$\)\s+Year to date")

_UBS_TAXABLE_DIVIDENDS_RE = re.compile(rf"Taxable dividends\s+({_MONEY})\s+({_MONEY})")
_UBS_TAXABLE_INTEREST_RE = re.compile(rf"Taxable interest\s+({_MONEY})\s+({_MONEY})")
# "Short term"/"Long term" rows carry THREE columns after the label: this-period
# realized, YTD realized, and unrealized gain/loss (from the interleaved
# "Summary of gains and losses" panel) -- group(2) is YTD REALIZED. group(3)
# (unrealized) must NEVER be read as a realized YTD figure: on the sampled
# statement, long-term UNREALIZED is $406,840.68 while long-term REALIZED YTD
# is $0.00 -- confusing the two columns overstates realized gains by six figures.
_UBS_SHORT_TERM_RE = re.compile(rf"Short term\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})")
_UBS_LONG_TERM_RE = re.compile(rf"Long term\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})")


def _extract_ubs_account_number(text: str) -> str:
    m = _UBS_ACCOUNT_RE.search(text)
    if not m:
        raise StatementParseError("No account number found. Ensure this is a complete UBS monthly statement export.")
    return m.group(1)


def _extract_ubs_period_end(text: str) -> str:
    """UBS gives no explicit period-end date -- only a "<Month> <YYYY>"
    column header. The period end is the LAST calendar day of that month."""
    m = _UBS_PERIOD_RE.search(text)
    if not m:
        raise StatementParseError("No statement period found in UBS statement text.")
    month_name, year_str = m.groups()
    year = int(year_str)
    month = datetime.strptime(month_name, "%B").month
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, last_day).date().isoformat()


def _ubs_row_ytd(pattern: re.Pattern[str], text: str, group: int = 2) -> float:
    """Extract one UBS row's YTD figure, degrading to 0.0 (not a parse error)
    when the row is absent -- follows _row_ytd_pair's existing convention."""
    m = pattern.search(text)
    if not m:
        return 0.0
    return _parse_currency(m.group(group))


def _parse_ubs(full_text: str) -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number=_extract_ubs_account_number(full_text),
        broker="ubs",
        # UBS never states "taxable" about the account itself -- see module
        # docstring. "Company Sponsored Stock Plan" is almost certainly
        # taxable, but this parser does not guess -- same rule as Schwab.
        account_type="unknown",
        statement_period_end=_extract_ubs_period_end(full_text),
        interest_taxable_ytd=_ubs_row_ytd(_UBS_TAXABLE_INTEREST_RE, full_text),
        # UBS gives no tax-exempt split anywhere in the document (verified
        # across all 10 pages of a real statement) -- same limitation as IBKR.
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=_ubs_row_ytd(_UBS_TAXABLE_DIVIDENDS_RE, full_text),
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=_ubs_row_ytd(_UBS_SHORT_TERM_RE, full_text),
        ltcg_net_ytd=_ubs_row_ytd(_UBS_LONG_TERM_RE, full_text),
        captured_at=datetime.now(UTC).isoformat(),
        provenance={"pdf_pages_total": full_text.count("--- page")},
        owner_key=None,  # UBS owner extraction out of scope -- see extract_owner_key TODO(verify)
    )


# ---------------------------------------------------------------------------
# PDF wrapper (pdfplumber deferred -- Pyodide-safe)
# ---------------------------------------------------------------------------


def parse_statement_pdf(data: bytes) -> list[BrokerageStatementRecord]:
    """Parse a brokerage statement PDF from raw bytes. pdfplumber import
    deferred for Pyodide safety -- only local installs call this.

    Returns one record per account found (see parse_statement_text)."""
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]

    return parse_statement_text(pages)


# ---------------------------------------------------------------------------
# Folder scanner
# ---------------------------------------------------------------------------


def scan_statement_folder(folder: Path) -> tuple[list[BrokerageStatementRecord], list[str]]:
    """Parse every PDF in *folder*. Returns (records, error_messages) -- a
    single malformed PDF does not abort the whole scan."""
    records: list[BrokerageStatementRecord] = []
    errors: list[str] = []
    for pdf_path in sorted(folder.glob("*.[pP][dD][fF]")):
        try:
            records.extend(parse_statement_pdf(pdf_path.read_bytes()))
        except Exception as exc:  # noqa: BLE001 -- one bad file must not kill the scan
            errors.append(f"{pdf_path.name}: {exc}")
    return records, errors


# ---------------------------------------------------------------------------
# Account-type partitioning + latest-per-account aggregation
# ---------------------------------------------------------------------------


def pick_latest_per_account(
    records: list[BrokerageStatementRecord],
) -> dict[str, BrokerageStatementRecord]:
    """Keep only the latest (by statement_period_end) record per account_number."""
    latest: dict[str, BrokerageStatementRecord] = {}
    for rec in records:
        current = latest.get(rec.account_number)
        if current is None or rec.statement_period_end > current.statement_period_end:
            latest[rec.account_number] = rec
    return latest


def partition_by_account_type(
    by_account: dict[str, BrokerageStatementRecord],
) -> tuple[
    dict[str, BrokerageStatementRecord],
    dict[str, BrokerageStatementRecord],
    dict[str, BrokerageStatementRecord],
]:
    """Split accounts into (taxable, excluded_retirement, needs_confirmation).

    - taxable: account_type == "taxable" -- safe to sum into YTD income.
    - excluded_retirement: "traditional_ira", "roth_ira", or "hsa" -- never
      counted, but returned (not silently dropped) so the UI can show them
      explicitly. HSA accounts are excluded here because dividends/interest
      earned *inside* the account are never currently-taxable events; this
      does NOT model HSA *distribution* taxation (see ACCOUNT_TYPES docstring).
    - needs_confirmation: "unknown" -- excluded from sums by default until a
      human confirms the type (see views/ytd_income.py).
    """
    taxable: dict[str, BrokerageStatementRecord] = {}
    excluded: dict[str, BrokerageStatementRecord] = {}
    unknown: dict[str, BrokerageStatementRecord] = {}
    for account_number, rec in by_account.items():
        if rec.account_type == "taxable":
            taxable[account_number] = rec
        elif rec.account_type in ("traditional_ira", "roth_ira", "hsa"):
            excluded[account_number] = rec
        else:
            unknown[account_number] = rec
    return taxable, excluded, unknown


def aggregate_to_ytd_fields(taxable_by_account: dict[str, BrokerageStatementRecord]) -> dict[str, float]:
    """Sum CONFIRMED-TAXABLE records into models.ytd_income.YTDSnapshot field names.

    Callers must pass only the "taxable" partition from partition_by_account_type
    -- this function does not re-check account_type, by design, so the filtering
    decision is made in exactly one place.

    dividends_tax_exempt_ytd and interest_tax_exempt_ytd both fold into
    tax_exempt_interest_ytd -- IRS 1040 line 2a treats exempt-interest
    dividends from muni funds the same as tax-exempt interest.
    """
    records = taxable_by_account.values()
    return {
        "interest_ytd": sum(r.interest_taxable_ytd for r in records),
        "tax_exempt_interest_ytd": sum(r.interest_tax_exempt_ytd + r.dividends_tax_exempt_ytd for r in records),
        "ordinary_dividends_ytd": sum(r.dividends_taxable_ytd for r in records),
        "stcg_ytd": sum(r.stcg_net_ytd for r in records),
        "ltcg_ytd": sum(r.ltcg_net_ytd for r in records),
    }


# ---------------------------------------------------------------------------
# JSON caches -- statement records, folder path, account-type overrides
# ---------------------------------------------------------------------------

_STATEMENT_CACHE_PATH = Path(__file__).resolve().parent.parent / ".brokerage_statement_cache.json"
_FOLDER_CONFIG_PATH = Path(__file__).resolve().parent.parent / ".statement_folder_config.json"
_ACCOUNT_TYPE_OVERRIDES_PATH = Path(__file__).resolve().parent.parent / ".statement_account_overrides.json"


def save_statement_records(records: dict[str, BrokerageStatementRecord]) -> None:
    """Merge *records* onto the existing on-disk cache (per-account slot), then persist.

    Mirrors engine.pdf_ledger.write_brokerage_contribution's per-slot merge
    semantics -- a full-file overwrite here meant scanning a folder with no
    brokerage PDFs (records={}) silently wiped every previously-confirmed
    account (audit-0805 C101). An empty *records* is therefore a no-op;
    a non-empty *records* updates only the account_number keys it contains,
    leaving every other cached account untouched.
    """
    merged = {**load_statement_records(), **records}
    write_pii_json(_STATEMENT_CACHE_PATH, {k: v.to_dict() for k, v in merged.items()})


def load_statement_records() -> dict[str, BrokerageStatementRecord]:
    if not _STATEMENT_CACHE_PATH.exists():
        return {}
    try:
        raw = read_pii_json(_STATEMENT_CACHE_PATH)
    except (json.JSONDecodeError, OSError):
        return {}
    result: dict[str, BrokerageStatementRecord] = {}
    for k, v in raw.items():
        try:
            result[k] = BrokerageStatementRecord.from_dict(v)
        except (KeyError, ValueError, TypeError):
            continue
    return result


def save_statement_folder_path(folder: str) -> None:
    write_pii_json(_FOLDER_CONFIG_PATH, {"folder": folder})


def load_statement_folder_path() -> str | None:
    if not _FOLDER_CONFIG_PATH.exists():
        return None
    try:
        raw = read_pii_json(_FOLDER_CONFIG_PATH)
    except (json.JSONDecodeError, OSError):
        return None
    folder = raw.get("folder")
    return str(folder) if folder is not None else None


def validate_local_folder(raw: str) -> tuple[Path | None, str | None]:
    """Validate a user-supplied local folder path for statement/report scanning.

    Returns (resolved_path, None) on success or (None, error_message) on failure.
    Single-user desktop tool: the owner supplies a folder on their own trusted
    machine, so this is normalization + containment (reject blank, control chars,
    '..'; resolve; require the path under $HOME), not a sandbox against a hostile
    actor. Shared by the brokerage, Koinly, and 1040 folder-scan blocks so the
    rule lives in exactly one place.
    """
    if not raw or not raw.strip():
        return None, "Folder path cannot be empty."
    stripped = raw.strip()
    if any(ord(ch) < 32 for ch in stripped):
        return None, "Folder path contains invalid characters."
    if ".." in stripped:
        return None, "Folder path may not contain '..'."
    candidate = Path(stripped).expanduser().resolve()
    if not candidate.is_relative_to(Path.home()):
        return None, f"Folder must be under your home directory ({Path.home()}): {candidate}"
    if not candidate.is_dir():
        return None, f"Not a folder: {candidate}"
    return candidate, None


def save_account_type_override(account_number: str, account_type: str) -> None:
    if account_type not in ACCOUNT_TYPES:
        raise ValueError(f"Invalid account_type {account_type!r}")
    overrides = load_account_type_overrides()
    overrides[account_number] = account_type
    write_pii_json(_ACCOUNT_TYPE_OVERRIDES_PATH, overrides)


def load_account_type_overrides() -> dict[str, str]:
    if not _ACCOUNT_TYPE_OVERRIDES_PATH.exists():
        return {}
    try:
        raw = read_pii_json(_ACCOUNT_TYPE_OVERRIDES_PATH)
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def apply_account_type_overrides(
    by_account: dict[str, BrokerageStatementRecord],
    overrides: dict[str, str],
) -> dict[str, BrokerageStatementRecord]:
    """Return a new dict with each record's account_type replaced per *overrides*.

    Overrides apply regardless of the parser's original detection -- this lets
    a user correct a wrong Vanguard detection too, not just fill in Schwab's
    permanent "unknown". Records with no matching override pass through unchanged.

    C16 (audit-0721): save_account_type_override validates against
    ACCOUNT_TYPES, but a stale/hand-edited cache entry could still contain an
    invalid value. Such an entry is skipped here (record passes through
    unchanged) rather than reaching BrokerageStatementRecord.__post_init__ and
    crashing the "Scan folder" handler with a ValueError.
    """
    result: dict[str, BrokerageStatementRecord] = {}
    for account_number, rec in by_account.items():
        override = overrides.get(account_number)
        if override is not None and override not in ACCOUNT_TYPES:
            result[account_number] = rec
            continue
        if override is not None and override != rec.account_type:
            from dataclasses import replace

            result[account_number] = replace(rec, account_type=override)
        else:
            result[account_number] = rec
    return result
