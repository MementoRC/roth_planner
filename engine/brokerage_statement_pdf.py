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
