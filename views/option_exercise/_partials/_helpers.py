from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import streamlit as st

from config.loader import load_defaults
from engine.data_sources.paths import CANDIDATE_STORE_PATH
from engine.data_sources.record import record_stock_quote_candidate
from engine.market_quote import QuoteResult, fetch_stock_quote

_SHARES_STATE_KEY = "_oe_shares_state"
_GRID_EDITOR_KEY = "oe_grid_editor"
# Lot keys + years the grid state above was built for; a different set (import
# or sync changed the grants) invalidates it (audit-1008 F-29).
_GRID_LAYOUT_STATE_KEY = "_oe_grid_layout"


def _price_key(year: int) -> str:
    return f"oe_price_{year}"


def _clear_widget_state() -> None:
    """Drop all per-page session_state so widgets re-seed from hh on rerun."""
    st.session_state.pop(_SHARES_STATE_KEY, None)
    st.session_state.pop(_GRID_EDITOR_KEY, None)
    st.session_state.pop(_GRID_LAYOUT_STATE_KEY, None)
    for k in [k for k in st.session_state if k.startswith("oe_price_")]:
        st.session_state.pop(k, None)


def configured_ticker() -> str:
    """The household's stock ticker — the same source Profile › Assumptions labels with."""
    return str(st.session_state.get("_stock_ticker") or load_defaults()["stock_ticker"])


def handle_stock_quote_fetch(
    *,
    store_path: str | Path = CANDIDATE_STORE_PATH,
    fetcher: Callable[[str], QuoteResult] = fetch_stock_quote,
) -> QuoteResult:
    """Fetch a live quote for the configured ticker and record it as a pending
    Command Center candidate.

    The quote changes nothing until it is confirmed in Command Center, which
    writes the one stock price (``stock_price``) every page uses. It is never
    stashed in session state: a page-local override made the Option Exercise
    Planner disagree with the rest of the app (redesign PR 2).

    Never raises: ``fetcher`` (matching ``fetch_stock_quote``) always returns a
    ``QuoteResult`` rather than raising, so this helper does too.
    """
    result = fetcher(configured_ticker())
    if result.ok and result.price is not None:
        record_stock_quote_candidate(result.price, store_path=store_path)
    return result
