"""YTD income snapshot fetch/parse/cache."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import requests  # type: ignore[import-untyped]

from engine.brokerage_statement_pdf import BrokerageStatementRecord, aggregate_to_ytd_fields
from engine.secure_io import read_pii_json, write_pii_json
from models.ytd_income import IncomeEvent, RealizedGainEvent, YTDSnapshot

if TYPE_CHECKING:
    pass

from .client import _get


def fetch_ytd_snapshot() -> YTDSnapshot:
    """Fetch year-to-date income data from FinExtract.

    Only checks server availability (/status) to report sync freshness in the
    UI. Returns an empty snapshot if FinExtract is unavailable (the UI then
    falls back to manual entry).

    Investment income (interest, dividends, tax-exempt split, realized gains)
    is sourced exclusively from brokerage statement PDFs now, via
    apply_brokerage_statement_records — NOT from FinExtract. FinExtract's
    investment_income/realized_gains endpoints were retired from this function
    because they structurally cannot distinguish tax-exempt income from
    taxable income, or separate taxable accounts from Roth/IRA accounts
    (confirmed against the live FinExtract server 2026-07-10) — see
    docs/superpowers/plans/2026-07-10-brokerage-statement-pdf-ytd.md.

    wages_ytd, nec_income_ytd, qualified_dividends_ytd, ira_conversions_ytd,
    spouse_ira_conversions_ytd, and ira_distributions_ytd remain
    manual-entry-only (see models.ytd_income.IncomeEvent / views/ytd_income.py).
    """
    ytd = YTDSnapshot()

    try:
        resp = _get("/status", timeout=3)
        resp.raise_for_status()
        ytd.manually_entered = False
        # Only stamp snapshot_date when the ping actually succeeded (audit-0805
        # C32) -- calling this unconditionally made every caller's
        # `if ytd.snapshot_date:` "was this sync actually reachable?" check
        # always true, even when FinExtract was down.
        ytd.with_snapshot_date()
    except requests.RequestException:
        pass

    return ytd


def apply_brokerage_statement_records(
    ytd: YTDSnapshot, taxable_by_account: dict[str, BrokerageStatementRecord]
) -> YTDSnapshot:
    """Overlay statement-derived interest/dividend/gain YTD figures onto *ytd*.

    *taxable_by_account* MUST already be filtered to account_type == "taxable"
    (via engine.brokerage_statement_pdf.partition_by_account_type) — this
    function does not re-check, so Roth/IRA/unknown accounts must never reach
    here. Fields not covered by brokerage statements (wages, NEC income, IRA
    conversions/distributions, qualified dividends) are left untouched,
    mirroring apply_option_exercises' overlay pattern.
    """
    totals = aggregate_to_ytd_fields(taxable_by_account)
    ytd.interest_ytd = totals["interest_ytd"]
    ytd.tax_exempt_interest_ytd = totals["tax_exempt_interest_ytd"]
    ytd.ordinary_dividends_ytd = totals["ordinary_dividends_ytd"]
    ytd.stcg_ytd = totals["stcg_ytd"]
    ytd.ltcg_ytd = totals["ltcg_ytd"]
    return ytd


_YTD_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".ytd_cache.json"


def ytd_to_dict(ytd: YTDSnapshot) -> dict:
    """Serialize a YTDSnapshot to its explicit JSON-able dict form.

    Extracted from save_ytd_snapshot (audit-0823: "YTD in the data-bridge
    bundle") so both the on-disk cache writer AND the data-bridge bundle
    builder share one serialization -- otherwise the bundle would silently
    drift from the cache format the first time either one gained a field.
    """
    return {
        "tax_year": ytd.tax_year,
        "snapshot_date": ytd.snapshot_date,
        "wages_ytd": ytd.wages_ytd,
        "nec_income_ytd": ytd.nec_income_ytd,
        "ira_conversions_ytd": ytd.ira_conversions_ytd,
        "spouse_ira_conversions_ytd": ytd.spouse_ira_conversions_ytd,
        "ira_distributions_ytd": ytd.ira_distributions_ytd,
        "ltcg_ytd": ytd.ltcg_ytd,
        "stcg_ytd": ytd.stcg_ytd,
        "qualified_dividends_ytd": ytd.qualified_dividends_ytd,
        "ordinary_dividends_ytd": ytd.ordinary_dividends_ytd,
        "interest_ytd": ytd.interest_ytd,
        "tax_exempt_interest_ytd": ytd.tax_exempt_interest_ytd,
        "nqo_exercise_ytd": ytd.nqo_exercise_ytd,
        "federal_withholding_ytd": ytd.federal_withholding_ytd,
        "hsa_contribution_ytd": ytd.hsa_contribution_ytd,
        "deductible_ira_contribution_ytd": ytd.deductible_ira_contribution_ytd,
        "crypto_stcg_ytd": ytd.crypto_stcg_ytd,
        "crypto_ltcg_ytd": ytd.crypto_ltcg_ytd,
        "crypto_income_ytd": ytd.crypto_income_ytd,
        "gain_events": [asdict(e) for e in ytd.gain_events],
        "income_events": [asdict(e) for e in ytd.income_events],
        "manually_entered": ytd.manually_entered,
    }


def ytd_from_dict(data: dict) -> YTDSnapshot:
    """Parse a YTDSnapshot from its JSON dict form, applying legacy migrations.

    Extracted from load_ytd_snapshot (audit-0823) so the data-bridge bundle
    reader can reuse the SAME migration logic -- a bundle built by an older
    peer (pre-nqo_exercise_ytd, pre-dividends_ytd-split, etc.) must still
    load correctly here, exactly as an old on-disk cache file would.
    Pure: operates on a copy, never mutates the caller's dict.
    """
    data = dict(data)
    events = [RealizedGainEvent(**e) for e in data.pop("gain_events", [])]
    income_events = [IncomeEvent(**e) for e in data.pop("income_events", [])]
    # Migrate old cache files that stored a single dividends_ytd key.
    if "dividends_ytd" in data and "ordinary_dividends_ytd" not in data:
        data["ordinary_dividends_ytd"] = data.pop("dividends_ytd")
    else:
        data.pop("dividends_ytd", None)
    # Migrate pre-PR1 caches that lack nqo_exercise_ytd.
    if "nqo_exercise_ytd" not in data:
        data["nqo_exercise_ytd"] = 0.0
    # PU1-M01: migrate old caches that predate federal_withholding_ytd field.
    if "federal_withholding_ytd" not in data:
        data["federal_withholding_ytd"] = 0.0
    # Migrate old caches that predate the above-the-line HSA/IRA adjustment fields.
    if "hsa_contribution_ytd" not in data:
        data["hsa_contribution_ytd"] = 0.0
    if "deductible_ira_contribution_ytd" not in data:
        data["deductible_ira_contribution_ytd"] = 0.0
    # Migrate old caches that predate the crypto (Koinly-sourced) fields.
    if "crypto_stcg_ytd" not in data:
        data["crypto_stcg_ytd"] = 0.0
    if "crypto_ltcg_ytd" not in data:
        data["crypto_ltcg_ytd"] = 0.0
    if "crypto_income_ytd" not in data:
        data["crypto_income_ytd"] = 0.0
    return YTDSnapshot(**data, gain_events=events, income_events=income_events)


def save_ytd_snapshot(ytd: YTDSnapshot) -> None:
    """Save YTD snapshot to disk as JSON."""
    write_pii_json(_YTD_CACHE_PATH, ytd_to_dict(ytd))


def load_ytd_snapshot() -> YTDSnapshot | None:
    """Load cached YTD snapshot from disk, or None if not available."""
    if not _YTD_CACHE_PATH.exists():
        return None
    try:
        data = read_pii_json(_YTD_CACHE_PATH)
    except (json.JSONDecodeError, OSError):
        return None
    return ytd_from_dict(data)
