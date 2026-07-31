from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import streamlit as st

from engine.data_sources.paths import CANDIDATE_STORE_PATH
from engine.data_sources.record import record_txn_quote_candidate
from engine.market_quote import QuoteResult, fetch_txn_quote

_SHARES_STATE_KEY = "_oe_shares_state"
_GRID_EDITOR_KEY = "oe_grid_editor"
_QUOTE_PRICE_KEY = "_txn_quote_price"
_GROWTH_RATE_KEY = "txn_price_growth_rate"


def _price_key(year: int) -> str:
    return f"oe_price_{year}"


def _clear_widget_state() -> None:
    """Drop all per-page session_state so widgets re-seed from hh on rerun."""
    st.session_state.pop(_SHARES_STATE_KEY, None)
    st.session_state.pop(_GRID_EDITOR_KEY, None)
    for k in [k for k in st.session_state if k.startswith("oe_price_")]:
        st.session_state.pop(k, None)


def _clear_assumed_price_widgets(explicit_price_years: set[int]) -> None:
    """Drop widget state for years WITHOUT an explicit saved price override.

    Keyed ``st.number_input`` widgets ignore their ``value=`` default once
    session_state already holds an entry for that key (Streamlit's keyed-
    widget retention) — so after a fresh quote fetch changes the projection
    basis, the "assumed" price cells would keep showing the stale price
    unless their widget state is cleared here so they re-seed from the new
    default on this same rerun. Years with an explicit saved override
    (``explicit_price_years``) are left untouched.
    """
    for k in [k for k in st.session_state if k.startswith("oe_price_")]:
        year_str = k[len("oe_price_") :]
        if year_str.isdigit() and int(year_str) not in explicit_price_years:
            st.session_state.pop(k, None)


def handle_txn_quote_fetch(
    *,
    store_path: str | Path = CANDIDATE_STORE_PATH,
    fetcher: Callable[[], QuoteResult] = fetch_txn_quote,
) -> QuoteResult:
    """Fetch a live TXN quote, record it as a pending Command Center candidate,
    and stash it in session_state so it immediately drives this page's price
    projection basis.

    The committed ``hh.txn_price_now`` is left untouched — the fetched price
    only becomes authoritative once confirmed via the Command Center review
    gate (``record_txn_quote_candidate``); until then it lives in
    ``st.session_state[_QUOTE_PRICE_KEY]`` as the page's effective basis.

    Never raises: ``fetcher`` (matching ``fetch_txn_quote``) always returns a
    ``QuoteResult`` rather than raising, so this helper does too — callers
    branch on ``result.ok`` to decide what to show.
    """
    result = fetcher()
    if result.ok and result.price is not None:
        record_txn_quote_candidate(result.price, store_path=store_path)
        st.session_state[_QUOTE_PRICE_KEY] = result.price
    return result
