"""Options (Stock Grants) Setup-domain partial (Task 5 of the
ui-shell-theme-toggle plan).

Split out of the original flat ``views/setup/_partials.py`` when that module
grew to ~980 lines (pure mechanical reorganization, no behavior change).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from engine.data_sources.resolver import GRANTS_KEY
from models.household import Household

from ._governance import _render_field_card


def render_options_partial(hh: Household, container) -> None:
    """Render the Options (Stock Grants) partial: the read-only equity-grants
    table plus the ``txn_price_now`` stock-price input, each with its own
    inline trust/manual-override/confirm governance card when a candidate is
    pending.

    Note: ``hh`` parameter is unused in this function's body; it is retained
    for interface parity with ``render_household_partial`` and
    ``render_accounts_partial``, which do use their ``hh`` argument. This
    consistency enables uniform ``(hh, container)`` call signatures across
    all Setup-domain partials for Task 8's shell composition.

    Unlike ``render_household_partial``/``render_accounts_partial``, this
    partial takes no ``owner`` argument — grants and the stock price are
    household-level, not per-person.

    The equity-grants table (moved from ``views/setup/portfolio.py``'s old
    ``_render_grants_section``, formerly rendered once per Me/All Portfolio
    sub-tab) reads ``st.session_state["portfolio_snapshot"]`` directly rather
    than taking it as a parameter — same internal-session_state-read
    convention ``render_accounts_partial`` already uses for its "(synced)"
    badge. Consolidated to render exactly ONCE here instead of twice (Me tab
    + All tab) since ``GRANTS_KEY``'s governance card below has explicit
    widget keys (``trust_grants``/``manual_grants``/``confirm_grants``) that
    would raise ``DuplicateWidgetID`` if rendered from two call sites in the
    same script run — this dedup, and the txn_price relocation described
    next, are a single deliberate Task 5 decision (NOT an application of
    Task 3's reordering exception, which was scoped only to minor same-tab
    cosmetic reordering and does not cover either of these changes — see
    below).

    ``txn_price_now`` is a Command-Center-governed sourced field (one of
    ``HOUSEHOLD_SCALAR_FIELDS``) aliased to the ``"txn_price"`` session key
    (see ``session_keys_for_writeback``/``_apply_confirm_to_session``'s
    docstring for why) — the widget reads/writes
    ``st.session_state.txn_price`` (not ``hh.txn_price_now``, which is a
    ``SourcedValue`` post-resolve and would raise
    ``StreamlitMixedNumericTypesError``), moved (same unkeyed
    controlled-widget shape) from ``views/setup/parameters.py``'s Joint
    sub-tab to here, co-located with the stock-grants table it prices.

    This is a cross-tab, user-visible Classic-mode layout change (Parameters
    -> Joint to Portfolio), which exceeds Task 5's literal text. A
    2026-07-24 spec-compliance review of commit 19e04f69 flagged it as such
    and flagged this docstring's prior (incorrect) citation of "Task 3's
    accepted-reordering exception" as not actually covering a cross-tab
    move. The project owner reviewed and explicitly APPROVED it the same day
    as a deliberate Task 5 design decision: all Options-domain fields
    (equity grants + the stock price that prices them) consolidate into
    exactly one call site, rendered from the Portfolio tab, going forward.
    See ``tests/test_setup_options_partial.py`` for the regression test that
    pins the Stock Price widget to the Portfolio tab (and asserts its
    absence from Parameters -> Joint).

    ``GRANTS_KEY``'s own governance card has no manual-override
    ``number_input`` (see ``_render_field_card``'s ``field_key != GRANTS_KEY``
    branch) and confirming it has no session_state mirror to update (see
    ``_apply_confirm_to_session``'s "grants: no direct session_state
    representation" note) — both pre-existing behaviors, unchanged here.
    """
    pending: set[str] = st.session_state.get("_pending_review", set())
    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    committed_json = load_committed(COMMITTED_PATH) or {}

    def _maybe_card(field_key: str) -> None:
        if field_key not in pending:
            return
        with container.container(border=True):
            _render_field_card(field_key, committed_json, store, choices)

    container.subheader("Stock Grants")
    snap = st.session_state.get("portfolio_snapshot")
    grants = snap.equity_grants if snap is not None else []
    if not grants:
        container.info("No grants loaded.")
    else:
        rows = [
            {
                "grant_id": g.grant_id,
                "type": g.grant_type,
                "grant_date": g.grant_date,
                "shares_granted": g.shares_granted,
                "outstanding": g.outstanding,
                "current_value": g.current_value,
            }
            for g in grants
        ]
        container.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            column_config={
                "current_value": st.column_config.NumberColumn("Current Value", format="$%,.0f"),
            },
        )
        container.caption(
            "Grant owner attribution is not yet available from FinExtract — "
            "all grants are shown here."
        )
    _maybe_card(GRANTS_KEY)

    container.subheader("Stock Price")
    st.session_state.txn_price = container.number_input(
        f"{st.session_state.get('_stock_ticker', 'Stock')} Current Price",
        min_value=0,
        value=st.session_state.txn_price,
        step=5,
        format="%d",
    )
    _maybe_card("txn_price_now")
