"""YTD income snapshot fetch/parse/cache."""

from __future__ import annotations

import json
import warnings
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

from engine.secure_io import read_pii_json, write_pii_json
from models.ytd_income import RealizedGainEvent, YTDSnapshot

if TYPE_CHECKING:
    pass

from .client import _flatten_query_rows, _get


def fetch_ytd_snapshot() -> YTDSnapshot:
    """Fetch year-to-date income data from FinExtract.

    Queries the brokerage realized_gains endpoint and tax_return ytd_income
    endpoint.  Returns an empty snapshot if FinExtract is unavailable (the UI
    then falls back to manual entry).
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
    #
    # Endpoint ownership contract (prevents double-count):
    #   investment_income  → SOLE owner of ordinary_dividends_ytd and interest_ytd.
    #                        These fields are written here and never touched again below.
    #   ytd_income         → SOLE owner of wages_ytd, nec_income_ytd, qualified_dividends_ytd,
    #                        ira_conversions_ytd, ira_distributions_ytd.
    #                        It does NOT write ordinary_dividends_ytd or interest_ytd even
    #                        though 1099-DIV/INT data appears in its rows — those are the
    #                        same underlying transactions already captured here.
    #
    # Background: both endpoints can return dividend and interest figures simultaneously
    # (investment_income from live brokerage transactions; ytd_income from 1099 tax forms
    # covering the same period).  Accumulating with += into the same fields silently 2x'd
    # those values → wrong total_ordinary_income → wrong MAGI → wrong IRMAA tier.
    # (Surfaced by math audit 2026-06-12, finding #4.)
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

    # YTD income summary (tax return endpoint — wages, conversions, etc.).
    # Owns: wages_ytd, nec_income_ytd, qualified_dividends_ytd,
    #       ira_conversions_ytd, ira_distributions_ytd.
    # Does NOT touch ordinary_dividends_ytd or interest_ytd (see contract above).
    try:
        resp = _get(
            "/query/tax_return",
            params={"data_type": "ytd_income"},
            timeout=5,
        )
        resp.raise_for_status()
        rows = _flatten_query_rows(resp.json())
        parsed = _parse_ytd_income_rows(rows)
        ytd.wages_ytd = parsed.get("wages", 0.0)
        ytd.nec_income_ytd = parsed.get("nec_income", 0.0)
        # qualified_dividends_ytd from 1099-DIV box 1b (tax rate, not volume).
        # ordinary_dividends_ytd is intentionally excluded here — owned by
        # investment_income above to prevent double-count.
        ytd.qualified_dividends_ytd = parsed.get("qualified_dividends", 0.0)
        ytd.ira_conversions_ytd = parsed.get("ira_conversions", 0.0)
        # spouse_ira_conversions_ytd: FinExtract ytd_income endpoint provides no
        # per-owner split; leave at default 0.0 (entered manually via YTD view).
        ytd.ira_distributions_ytd = parsed.get("ira_distributions", 0.0)
        # Best-effort: FinExtract may not separately label muni interest; defaults to 0.0
        # if the label doesn't match "tax-exempt"/"municipal"/"muni".
        ytd.tax_exempt_interest_ytd = parsed.get("tax_exempt_interest", 0.0)
        # Audit D-4: investment_income endpoint is the preferred owner of
        # ordinary_dividends_ytd; only fall back to 1099-DIV box 1a here when
        # that endpoint was unavailable (field still zero after its try-block).
        total_div = parsed.get("total_dividends", 0.0)
        if total_div and ytd.ordinary_dividends_ytd == 0.0:
            ytd.ordinary_dividends_ytd = max(total_div - ytd.qualified_dividends_ytd, 0.0)
            warnings.warn(
                "Falling back to 1099-DIV box 1a minus qualified_dividends for "
                "ordinary_dividends_ytd; investment_income endpoint preferred",
                UserWarning,
                stacklevel=2,
            )
        # psync-income-1/3: wire the interest bucket parsed above.
        # investment_income endpoint is the preferred owner of interest_ytd; only
        # fall back to the ytd_income row value when that endpoint was unavailable
        # (field still zero after its try-block).
        interest_fallback = parsed.get("interest", 0.0)
        if interest_fallback and ytd.interest_ytd == 0.0:
            ytd.interest_ytd = interest_fallback
            warnings.warn(
                "Falling back to ytd_income interest rows for interest_ytd; "
                "investment_income endpoint preferred",
                UserWarning,
                stacklevel=2,
            )
    except (requests.RequestException, ValueError):
        pass

    ytd.with_snapshot_date()
    return ytd


def _parse_ytd_income_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Parse partial-year income rows from FinExtract."""
    result: dict[str, float] = {}
    for row in rows:
        label = row.get("label", "").lower()
        amount = row.get("amount", 0) or 0
        if not amount:
            continue
        if "wage" in label or "w-2" in label:
            result["wages"] = result.get("wages", 0) + amount
        elif "qualified" in label and "non-qualified" not in label and "dividend" in label:
            # 1099-DIV box 1b — qualified dividends (subset of total ordinary).
            # Guard "non-qualified" prefix to avoid substring trap.
            result["qualified_dividends"] = result.get("qualified_dividends", 0) + amount
        elif "dividend" in label or "1099-div" in label:
            # 1099-DIV box 1a — total ordinary dividends (includes qualified)
            result["total_dividends"] = result.get("total_dividends", 0) + amount
        elif "tax-exempt" in label or "tax exempt" in label or "municipal" in label or "muni" in label:
            # Tax-exempt (muni bond) interest: in MAGI/IRMAA but NOT in ordinary brackets.
            result["tax_exempt_interest"] = result.get("tax_exempt_interest", 0) + amount
        elif "interest" in label:
            result["interest"] = result.get("interest", 0) + amount
        elif "conversion" in label:
            result["ira_conversions"] = result.get("ira_conversions", 0) + amount
        elif "distribution" in label or "1099-r" in label:
            result["ira_distributions"] = result.get("ira_distributions", 0) + amount
        elif "nec" in label or "self-employment" in label:
            result["nec_income"] = result.get("nec_income", 0) + amount
    return result


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
    return YTDSnapshot(**data, gain_events=events)
