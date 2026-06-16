"""Dividends rollup snapshot fetch + apply to holdings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

if TYPE_CHECKING:
    pass

from .client import BASE_URL, _headers
from .shapes import DividendsRollupSnapshot, PortfolioSnapshot


def fetch_dividends_rollup() -> DividendsRollupSnapshot:
    """Fetch dividends rollup from FinExtract /query/brokerage endpoint.

    Returns a DividendsRollupSnapshot with server_available=False on any
    failure (timeout, non-200, malformed JSON). Caller decides whether to
    apply or skip.

    NOTE: dividends_rollup returns {rollup: {...}}, NOT a rows[] shape.
    _flatten_query_rows is for rows-shaped endpoints and does NOT apply here.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/query/brokerage",
            params={"data_type": "dividends_rollup"},
            headers=_headers(),
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

    For each holding whose symbol appears in rollup.by_symbol:
      - dividends_by_year <- {year: yeardata['total']} (drops the count field)
      - dividends_window  <- {'start': window['from'], 'end': window['to']}
      - dividends_is_stale <- bool(rollup.freshness.get('is_stale', False))

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

    for acct in snap.accounts:
        for holding in acct.holdings:
            sym = (holding.symbol or "").upper()
            if not sym:
                continue
            sym_data = by_symbol_upper.get(sym, {})
            year_map: dict[str, Any] = sym_data.get("by_year", {})
            if not year_map:
                continue
            # Extract .total from {total, count} per-year objects; drop counts
            holding.dividends_by_year = {
                year: float(yd.get("total", 0)) for year, yd in year_map.items()
            }
            if window_translated:
                holding.dividends_window = dict(window_translated)
            holding.dividends_is_stale = is_stale

    return snap
