"""Dividends rollup snapshot fetch + apply to holdings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

if TYPE_CHECKING:
    pass

from .client import _get
from .shapes import DividendsRollupSnapshot, PortfolioSnapshot


def fetch_dividends_rollup() -> DividendsRollupSnapshot:
    """Fetch dividends rollup from FinExtract /query/brokerage endpoint.

    Returns a DividendsRollupSnapshot with server_available=False on any
    failure (timeout, non-200, malformed JSON, or unexpected redirect).
    Caller decides whether to apply or skip.

    NOTE: dividends_rollup returns {rollup: {...}}, NOT a rows[] shape.
    _flatten_query_rows is for rows-shaped endpoints and does NOT apply here.
    """
    try:
        resp = _get(
            "/query/brokerage",
            params={"data_type": "dividends_rollup"},
            timeout=5,
        )
        if resp.status_code != 200:
            return DividendsRollupSnapshot(
                server_available=False,
                error=f"HTTP {resp.status_code}",
            )
        data = resp.json()
        rollup = data.get("rollup") or {}
        return DividendsRollupSnapshot(
            server_available=True,
            by_symbol=rollup.get("by_symbol", {}),
            window=rollup.get("window", {}),
            freshness=rollup.get("freshness", {}),
        )
    except (requests.RequestException, ValueError) as e:
        return DividendsRollupSnapshot(server_available=False, error=str(e))


def apply_dividends_rollup(
    snap: PortfolioSnapshot,
    rollup: DividendsRollupSnapshot,
) -> PortfolioSnapshot:
    """Merge dividends_rollup data into the snapshot's holdings in place.

    SCOPE OF ``rollup.by_symbol`` (audit-0823 PS-2): each entry is the
    HOUSEHOLD-WIDE dividend total for that symbol, summed across every account
    and institution. It carries no account dimension, and it cannot:
    :func:`fetch_dividends_rollup` issues a single request whose only parameter
    is ``data_type``, so there is no way to ask for one account's figures. This
    was undocumented, and stamping the undivided total onto each holding looked
    correct as a result.

    For each holding whose symbol appears in rollup.by_symbol:
      - dividends_by_year <- {year: yeardata['total'] * this holding's share}
      - dividends_window  <- {'start': window['from'], 'end': window['to']}
      - dividends_is_stale <- bool(rollup.freshness.get('is_stale', False))

    ALLOCATION: the symbol's total is split across the holdings of that symbol
    in proportion to SHARE COUNT, so the per-holding figures sum back to the
    household total. Assigning the full total to each holding multiplied a
    symbol's projected income by the number of accounts holding it, because
    ``forecast_portfolio`` deliberately SUMS same-ticker positions across
    accounts (audit-0720 L1) and ``positions_for_forecast_multi`` feeds it every
    brokerage account at once.

    Shares are the right divisor because dividends are paid per share. Using
    CURRENT share counts to apportion a TRAILING window is the same
    approximation ``Position.ttm_per_share`` already makes and documents: exact
    for a position whose share count was stable across the window, approximate
    otherwise. Apportioning by market value instead would be wrong for any
    symbol whose price differs from its dividend-weighted basis.

    A symbol whose holdings have zero total shares is allocated 0.0 rather than
    dividing by zero; such holdings contribute nothing downstream anyway
    (``Position.ttm_per_share`` returns 0.0 when shares <= 0).

    Holdings whose symbol is absent from rollup.by_symbol keep their existing
    dividend fields (typically None).

    Symbol lookup is case-insensitive (both sides uppercased) so that
    FinExtract mixed-case or lowercase symbols match roth_planner Holdings.

    Returns the same snapshot (mutates holdings in place — consistent with
    save_snapshot / load_snapshot round-trip).
    """
    if not rollup.server_available or not rollup.by_symbol:
        return snap

    # Build uppercase index of rollup symbols once for O(1) lookup per holding
    by_symbol_upper: dict[str, dict[str, Any]] = {
        sym.upper(): data for sym, data in rollup.by_symbol.items()
    }

    # Translate window keys from/to -> start/end ONCE per call.
    # Holding.dividends_window expects dict[str, str]; cast to str defensively.
    window_translated: dict[str, str] = {}
    if "from" in rollup.window:
        window_translated["start"] = str(rollup.window["from"])
    if "to" in rollup.window:
        window_translated["end"] = str(rollup.window["to"])

    is_stale = bool(rollup.freshness.get("is_stale", False))

    # Household-wide share count per symbol — the denominator for the pro-rata
    # split below. Negative quantities (short positions) are floored at 0 so
    # they cannot produce a negative allocation or inflate a sibling's share.
    shares_by_symbol: dict[str, float] = {}
    for acct in snap.accounts:
        for holding in acct.holdings:
            sym = (holding.symbol or "").upper()
            if not sym or sym not in by_symbol_upper:
                continue
            shares_by_symbol[sym] = shares_by_symbol.get(sym, 0.0) + max(holding.quantity, 0.0)

    for acct in snap.accounts:
        for holding in acct.holdings:
            sym = (holding.symbol or "").upper()
            if not sym:
                continue
            sym_data = by_symbol_upper.get(sym, {})
            year_map: dict[str, Any] = sym_data.get("by_year", {})
            if not year_map:
                continue
            total_shares = shares_by_symbol.get(sym, 0.0)
            share = (max(holding.quantity, 0.0) / total_shares) if total_shares > 0 else 0.0
            # Extract .total from {total, count} per-year objects; drop counts
            holding.dividends_by_year = {
                year: float(yd.get("total", 0)) * share for year, yd in year_map.items()
            }
            if window_translated:
                holding.dividends_window = dict(window_translated)
            holding.dividends_is_stale = is_stale

    return snap
