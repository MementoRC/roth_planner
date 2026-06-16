"""Brokerage holdings fetch + dividend derivation + forecast positions + snapshot merge."""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

if TYPE_CHECKING:
    pass

from .client import BASE_URL, _flatten_query_rows, _headers
from .shapes import AccountSummary, Holding, PortfolioSnapshot


def merge_snapshots(
    existing: PortfolioSnapshot | None,
    incoming: PortfolioSnapshot,
    *,
    as_spouse: bool,
) -> PortfolioSnapshot:
    """Merge ``incoming`` into ``existing``, preserving the other party's data.

    Pure function — no I/O, no session state. Used by the upload widget when a
    spouse uploads their own planner export into the receiver's session.

    Args:
        existing: The receiver's current snapshot (``None`` means an empty
            starting state).
        incoming: The freshly-parsed snapshot from the uploaded file.
        as_spouse: When ``True``, ``incoming`` represents the SPOUSE's data
            (their FinExtract export from their own perspective). All incoming
            accounts have their ``owner`` rewritten from ``"you"`` to
            ``"spouse"``, the existing your-owned accounts and grants are
            preserved, and incoming ``equity_grants`` / ``txn_shares_*`` are
            DROPPED (spouse has no grants in this household model).
            When ``False``, ``incoming`` represents the receiver's own data;
            existing spouse-owned accounts are preserved while the receiver's
            own accounts + grants + TXN are replaced.

    Returns:
        A new ``PortfolioSnapshot`` with the merged accounts and metadata.
    """
    if as_spouse:
        # Rewrite incoming account ownership; ignore incoming grants/TXN.
        for acc in incoming.accounts:
            acc.owner = "spouse"
        spouse_accounts = incoming.accounts
        if existing is not None:
            your_accounts = [a for a in existing.accounts if a.owner == "you"]
            grants = existing.equity_grants
            txn_held = existing.txn_shares_held
            txn_val = existing.txn_shares_value
            server_available = existing.server_available
            error = existing.error
        else:
            your_accounts = []
            grants = []
            txn_held = 0
            txn_val = 0.0
            server_available = False
            error = None
    else:
        # Receiver's own data — replace your-accounts + grants + TXN; keep spouse accounts.
        your_accounts = incoming.accounts
        grants = incoming.equity_grants
        txn_held = incoming.txn_shares_held
        txn_val = incoming.txn_shares_value
        server_available = incoming.server_available
        error = incoming.error
        if existing is not None:
            spouse_accounts = [a for a in existing.accounts if a.owner == "spouse"]
        else:
            spouse_accounts = []
    return PortfolioSnapshot(
        accounts=list(your_accounts) + list(spouse_accounts),
        equity_grants=grants,
        txn_shares_held=txn_held,
        txn_shares_value=txn_val,
        server_available=server_available,
        error=error,
    )


def _derive_ttm_dividends(h: Holding) -> float:
    """Derive trailing-12-month dividends for a Holding from FinExtract data.

    Strategy:
    - dividends_by_year + dividends_window present: window-actualized
      ttm = sum(values) * 365 / window_days
    - dividends_by_year only: most-recent prior-year value
    - No data: 0.0 (pre-H2 behavior)
    """
    if not h.dividends_by_year:
        return 0.0

    if h.dividends_window:
        try:
            start = date.fromisoformat(h.dividends_window["start"])
            end = date.fromisoformat(h.dividends_window["end"])
            days = (end - start).days
            if days > 0:
                return sum(h.dividends_by_year.values()) * 365.0 / days
        except (KeyError, ValueError):
            pass

    # Audit E-5: use most-recent prior year, not the highest value across all years.
    # max(prior.values()) overstates yield for declining dividend streams; we want
    # the value from the highest year-key (i.e. most recent completed year).
    current_year = str(date.today().year)
    prior = {k: v for k, v in h.dividends_by_year.items() if k < current_year}
    return prior[max(prior.keys())] if prior else 0.0


def positions_for_forecast(brok_snapshot: AccountSummary) -> list:
    """Convert brokerage holdings into Position records for dividend forecast.

    Args:
        brok_snapshot: an AccountSummary for a brokerage account (from
            PortfolioSnapshot.account_by_type("brokerage")).

    Returns a list of engine.dividend_forecast.Position, one per Holding.
    Positions with zero market_value are skipped.
    """
    from engine.dividend_forecast import Position

    positions = []
    for h in brok_snapshot.holdings:
        if h.market_value <= 0:
            continue
        if h.dividends_is_stale and h.dividends_by_year:
            warnings.warn(
                f"{h.symbol}: dividend data is stale (window {h.dividends_window})",
                UserWarning,
                stacklevel=2,
            )
        positions.append(
            Position(
                ticker=h.symbol,
                shares=h.quantity,
                balance=h.market_value,
                ttm_dividends=_derive_ttm_dividends(h),
            )
        )
    return positions


def positions_for_forecast_multi(accounts: Iterable[AccountSummary]) -> list:
    """Flatten positions across multiple brokerage accounts for the forecast engine.

    Reuses positions_for_forecast per account and concatenates. Used by app.py
    to feed forecast_portfolio() positions from all brokerage accounts (both
    owners) rather than just one.
    """
    result: list = []
    for acct in accounts:
        result.extend(positions_for_forecast(acct))
    return result


def fetch_holdings() -> list[dict[str, Any]]:
    """Fetch brokerage holdings from the ingestion server."""
    try:
        resp = requests.get(
            f"{BASE_URL}/query/brokerage",
            params={"data_type": "holdings"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return _flatten_query_rows(data)
    except (requests.RequestException, ValueError):
        return []
