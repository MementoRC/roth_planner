"""Per-owner PDF contribution ledger -- the source of truth for Koinly and
brokerage-statement-derived YTD figures, keyed by owner.

Fixes the override bug (docs/superpowers/specs/2026-07-13-spouse-pdf-owner-
attribution-design.md): today, scanning a Koinly report direct-assigns
crypto_stcg_ytd/crypto_ltcg_ytd/crypto_income_ytd on the snapshot, so a second
owner's report silently overwrites the first. This ledger stores one slot per
(doc_type, owner) and the snapshot value becomes SUM(slot) across owners --
one owner behaves identically to today; two owners add.

Brokerage contributions already dedup by account_number (see
engine.brokerage_statement_pdf.pick_latest_per_account); this ledger adds the
owner dimension on top so two owners' distinct accounts both survive a
re-scan, while re-scanning the SAME owner's SAME account still replaces (not
duplicates) that slot -- identical to today's pick_latest_per_account
semantics, just owner-scoped.

Pure functions + a small JSON cache. No Streamlit import (engine/ purity rule).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.koinly_report_pdf import KoinlyReport
from engine.secure_io import read_pii_json, write_pii_json

# Ledger shape:
# {
#   "koinly": {"<owner>": {"stcg": float, "ltcg": float, "income": float,
#                            "captured_at": str, "source": str}},
#   "brokerage": {"<owner>": {"<account_number>": {...record.to_dict()...}}},
# }
PdfLedger = dict[str, dict[str, Any]]

_EMPTY_LEDGER: PdfLedger = {"koinly": {}, "brokerage": {}}


def write_koinly_contribution(ledger: PdfLedger, owner: str, report: KoinlyReport) -> PdfLedger:
    """Return a NEW ledger with *owner*'s Koinly slot set to *report*'s figures.

    Re-writing the same owner with the SAME or a NEWER tax_year replaces that
    owner's prior contribution (idempotent re-scan); a report from an OLDER
    tax_year than what's already stored is skipped (C15 audit-0721) -- a
    multi-year folder scan must keep the latest tax_year's figures rather
    than silently collapsing to whichever Koinly PDF happens to be processed
    last. A different owner's slot is untouched (additive across owners).
    """
    updated: PdfLedger = {
        "koinly": dict(ledger.get("koinly", {})),
        "brokerage": dict(ledger.get("brokerage", {})),
    }
    existing = updated["koinly"].get(owner)
    if existing is not None and int(existing.get("tax_year", report.tax_year)) > report.tax_year:
        return updated
    updated["koinly"][owner] = {
        "tax_year": report.tax_year,
        "stcg": float(report.crypto_stcg),
        "ltcg": float(report.crypto_ltcg),
        "income": float(report.crypto_income),
        "captured_at": report.captured_at,
        "source": report.source,
    }
    return updated


def write_brokerage_contribution(
    ledger: PdfLedger, owner: str, record: BrokerageStatementRecord
) -> PdfLedger:
    """Return a NEW ledger with *record* written into *owner*'s brokerage
    slot, keyed by account_number.

    Re-writing the same (owner, account_number) pair replaces that slot only
    when *record*'s statement_period_end is the same or newer than the
    stored slot's (mirrors pick_latest_per_account's comparison, C14
    audit-0721) -- an out-of-order scan (e.g. Dec statement processed after
    Jan) leaves the newer stored record untouched. A different owner or a
    different account_number is a separate, additive slot.
    """
    updated: PdfLedger = {
        "koinly": dict(ledger.get("koinly", {})),
        "brokerage": {k: dict(v) for k, v in ledger.get("brokerage", {}).items()},
    }
    owner_accounts = dict(updated["brokerage"].get(owner, {}))
    existing = owner_accounts.get(record.account_number)
    if existing is not None and str(existing.get("statement_period_end", "")) > (
        record.statement_period_end
    ):
        return updated
    owner_accounts[record.account_number] = record.to_dict()
    updated["brokerage"][owner] = owner_accounts
    return updated


def derive_koinly_totals(ledger: PdfLedger) -> dict[str, float]:
    """Sum Koinly stcg/ltcg/income across every owner slot in the ledger.

    Empty ledger -> all zeros. Single owner -> identical to that owner's raw
    report values (non-regression). Multiple owners -> summed (the fix).
    """
    by_owner = ledger.get("koinly", {})
    return {
        "stcg": sum(float(v.get("stcg", 0.0)) for v in by_owner.values()),
        "ltcg": sum(float(v.get("ltcg", 0.0)) for v in by_owner.values()),
        "income": sum(float(v.get("income", 0.0)) for v in by_owner.values()),
    }


def derive_brokerage_totals(ledger: PdfLedger) -> dict[str, float]:
    """Sum brokerage-derived YTD fields across every (owner, account_number)
    slot in the ledger, using the same field mapping as
    engine.brokerage_statement_pdf.aggregate_to_ytd_fields.

    Deliberately re-implemented here (not delegating to
    aggregate_to_ytd_fields) because that function takes a flat
    dict[account_number, record]; the ledger is owner-scoped
    dict[owner, dict[account_number, record_dict]], so the flattening step
    (across owners) belongs in this module.
    """
    totals = {
        "interest_ytd": 0.0,
        "tax_exempt_interest_ytd": 0.0,
        "ordinary_dividends_ytd": 0.0,
        "stcg_ytd": 0.0,
        "ltcg_ytd": 0.0,
    }
    for owner_accounts in ledger.get("brokerage", {}).values():
        for rec_dict in owner_accounts.values():
            totals["interest_ytd"] += float(rec_dict.get("interest_taxable_ytd", 0.0))
            totals["tax_exempt_interest_ytd"] += float(
                rec_dict.get("interest_tax_exempt_ytd", 0.0)
            ) + float(rec_dict.get("dividends_tax_exempt_ytd", 0.0))
            totals["ordinary_dividends_ytd"] += float(rec_dict.get("dividends_taxable_ytd", 0.0))
            totals["stcg_ytd"] += float(rec_dict.get("stcg_net_ytd", 0.0))
            totals["ltcg_ytd"] += float(rec_dict.get("ltcg_net_ytd", 0.0))
    return totals


def extract_owner(ledger: PdfLedger, owner: str) -> dict:
    """Return the exporter's owner-agnostic ledger slice: the inner values under `owner`."""
    koinly = copy.deepcopy(ledger.get("koinly", {}).get(owner, {}))
    brokerage = copy.deepcopy(ledger.get("brokerage", {}).get(owner, {}))
    return {"koinly": koinly, "brokerage": brokerage}


def replace_owner(ledger: PdfLedger, owner: str, slice_: dict) -> PdfLedger:
    """Return a new ledger with `owner`'s koinly/brokerage entries replaced by `slice_`.
    An empty section in `slice_` removes that owner from that section (full reset)."""
    out = copy.deepcopy(ledger)
    out.setdefault("koinly", {})
    out.setdefault("brokerage", {})
    for section in ("koinly", "brokerage"):
        payload = slice_.get(section) or {}
        if payload:
            out[section][owner] = copy.deepcopy(payload)
        else:
            out[section].pop(owner, None)
    return out


# ---------------------------------------------------------------------------
# JSON cache
# ---------------------------------------------------------------------------

_LEDGER_PATH = Path(__file__).resolve().parent.parent / ".pdf_import_ledger.json"


def save_ledger(ledger: PdfLedger) -> None:
    write_pii_json(_LEDGER_PATH, ledger)


def load_ledger() -> PdfLedger:
    """Load the ledger, defaulting missing doc-type keys to {} for forward
    compatibility with partially-migrated or hand-edited cache files (mirrors
    the field-default pattern in engine.portfolio_sync.ytd.load_ytd_snapshot
    and engine.tax_return_pdf.load_pdf_tax_records)."""
    if not _LEDGER_PATH.exists():
        return dict(_EMPTY_LEDGER)
    try:
        raw = read_pii_json(_LEDGER_PATH)
    except (json.JSONDecodeError, OSError):
        return dict(_EMPTY_LEDGER)
    if not isinstance(raw, dict):
        return dict(_EMPTY_LEDGER)
    return {
        "koinly": dict(raw.get("koinly", {})),
        "brokerage": {k: dict(v) for k, v in raw.get("brokerage", {}).items()},
    }
