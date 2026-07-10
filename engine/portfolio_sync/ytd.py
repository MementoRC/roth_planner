"""YTD income snapshot fetch/parse/cache."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import requests  # type: ignore[import-untyped]

from engine.secure_io import read_pii_json, write_pii_json
from models.ytd_income import IncomeEvent, RealizedGainEvent, YTDSnapshot

if TYPE_CHECKING:
    pass

from .client import _flatten_query_rows, _get


def fetch_ytd_snapshot() -> YTDSnapshot:
    """Fetch year-to-date income data from FinExtract.

    Queries the brokerage realized_gains and investment_income endpoints.
    Returns an empty snapshot if FinExtract is unavailable (the UI then falls
    back to manual entry).

    wages_ytd, nec_income_ytd, qualified_dividends_ytd, ira_conversions_ytd,
    ira_distributions_ytd, and tax_exempt_interest_ytd are manual-entry-only
    (see models.ytd_income.IncomeEvent / views/ytd_income.py) — FinExtract's
    tax_return/ytd_income endpoint that used to supply them was TurboTax-derived
    (stale mid-year data) and has been retired.
    """
    ytd = YTDSnapshot()

    # Check server availability; skip the early-return so individual endpoint
    # try/except blocks can still succeed even when /status is unreachable.
    try:
        resp = _get("/status", timeout=3)
        resp.raise_for_status()
        ytd.manually_entered = False
    except requests.RequestException:
        pass

    # Realized gains
    try:
        resp = _get(
            "/query/brokerage",
            params={"data_type": "realized_gains"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        institution = data.get("institution", "")
        captured_at = data.get("captured_at", "")
        # Extract date portion from ISO timestamp (e.g. "2026-03-17T16:47:58.063Z" → "2026-03-17")
        captured_date = captured_at[:10] if captured_at else ""
        rows = _flatten_query_rows(data)
        for row in rows:
            if "long_term_gain" in row or "short_term_gain" in row:
                # Schwab aggregated summary format (schwab-realized-gains-v2)
                ltcg = row.get("long_term_gain", 0.0) or 0.0
                stcg = row.get("short_term_gain", 0.0) or 0.0
                ytd.ltcg_ytd += ltcg
                ytd.stcg_ytd += stcg
                if ltcg:
                    ytd.gain_events.append(
                        RealizedGainEvent(
                            date=captured_date,
                            description=f"{institution.title()} realized gains (YTD)",
                            proceeds=0.0,
                            cost_basis=0.0,
                            holding_period="long",
                            account_name=institution.title(),
                        )
                    )
                if stcg:
                    ytd.gain_events.append(
                        RealizedGainEvent(
                            date=captured_date,
                            description=f"{institution.title()} realized gains (YTD)",
                            proceeds=0.0,
                            cost_basis=0.0,
                            holding_period="short",
                            account_name=institution.title(),
                        )
                    )
            else:
                # Per-event format (date, description, proceeds, cost_basis)
                event = RealizedGainEvent(
                    date=row.get("date", ""),
                    description=row.get("description", ""),
                    proceeds=row.get("proceeds", 0.0),
                    cost_basis=row.get("cost_basis", 0.0),
                    holding_period=row.get("holding_period", "long"),
                    account_name=row.get("account", ""),
                )
                ytd.gain_events.append(event)
                if event.is_ltcg:
                    ytd.ltcg_ytd += event.gain_loss
                else:
                    ytd.stcg_ytd += event.gain_loss
    except (requests.RequestException, ValueError):
        pass

    # Investment income (dividends + interest from brokerage).
    # SOLE owner of ordinary_dividends_ytd and interest_ytd.
    #
    # wages_ytd, nec_income_ytd, qualified_dividends_ytd, ira_conversions_ytd,
    # spouse_ira_conversions_ytd, ira_distributions_ytd, and tax_exempt_interest_ytd
    # are manual-entry-only — FinExtract's tax_return/ytd_income endpoint that used
    # to supply them (TurboTax-derived, stale mid-year data) has been retired; this
    # function no longer queries it. The caller (views/ytd_income.py) is responsible
    # for preserving those fields across a sync since this function does not set them.
    try:
        resp = _get(
            "/query/brokerage",
            params={"data_type": "investment_income"},
            timeout=5,
        )
        resp.raise_for_status()
        rows = _flatten_query_rows(resp.json())
        for row in rows:
            ytd.ordinary_dividends_ytd += row.get("received_dividends", 0.0) or 0.0
            ytd.interest_ytd += row.get("received_interest", 0.0) or 0.0
    except (requests.RequestException, ValueError):
        pass

    ytd.with_snapshot_date()
    return ytd


_YTD_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".ytd_cache.json"


def save_ytd_snapshot(ytd: YTDSnapshot) -> None:
    """Save YTD snapshot to disk as JSON."""
    data = {
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
        "gain_events": [asdict(e) for e in ytd.gain_events],
        "income_events": [asdict(e) for e in ytd.income_events],
        "manually_entered": ytd.manually_entered,
    }
    write_pii_json(_YTD_CACHE_PATH, data)


def load_ytd_snapshot() -> YTDSnapshot | None:
    """Load cached YTD snapshot from disk, or None if not available."""
    if not _YTD_CACHE_PATH.exists():
        return None
    try:
        data = read_pii_json(_YTD_CACHE_PATH)
    except (json.JSONDecodeError, OSError):
        return None

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
    return YTDSnapshot(**data, gain_events=events, income_events=income_events)
