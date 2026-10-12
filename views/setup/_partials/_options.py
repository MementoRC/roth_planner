"""Options (Stock Grants) Setup-domain partial (originally Task 5 of the
ui-shell-theme-toggle plan).

Split out of the original flat ``views/setup/_partials.py`` when that module
grew to ~980 lines (pure mechanical reorganization, no behavior change).

Per-field sourced-value governance cards do NOT render here — they render
exclusively in ``views/setup/command_center.py``'s generic per-pending-field
loop (one owner only, to avoid ``DuplicateWidgetID`` from ``st.tabs()``
executing every tab body every run; see that module's docstring).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config.scalar_registry import scalar_default
from models.household import Household
from views.portfolio import render_grants_pending_caption

from ._clamp import clamp as _clamp


def render_options_partial(hh: Household, container) -> None:
    """Render the Options (Stock Grants) partial: the read-only equity-grants
    table. Its trust/manual-override/confirm governance card does NOT render
    here — it renders exclusively in ``views/setup/command_center.py``'s
    generic per-pending-field loop.

    The ``stock_price_now`` stock-price input (previously rendered here too)
    moved out into ``render_stock_price_widget`` (PR B, 2026-09), together
    with the assumed stock growth input (``stock_price_growth_rate``, redesign
    PR 2) that sits beside the price — it now renders from ``views/profile_pages.py``'s Assumptions page
    (redesign PR 3b), rather than under Options. See
    ``render_stock_price_widget``'s docstring for the rationale.

    Note: ``hh`` parameter is unused in this function's body; it is retained
    for interface parity with ``render_household_partial`` and
    ``render_accounts_partial``, which do use their ``hh`` argument. This
    consistency enables uniform ``(hh, container)`` call signatures across
    all Setup-domain partials for Task 8's shell composition.

    Unlike ``render_household_partial``/``render_accounts_partial``, this
    partial takes no ``owner`` argument — grants are household-level, not
    per-person.

    The equity-grants table (moved from ``views/setup/portfolio.py``'s old
    ``_render_grants_section``, formerly rendered once per Me/All Portfolio
    sub-tab) reads ``st.session_state["portfolio_snapshot"]`` directly rather
    than taking it as a parameter — same internal-session_state-read
    convention ``render_accounts_partial`` already uses for its "(synced)"
    badge. Consolidated to render exactly ONCE here instead of twice (Me tab
    + All tab): the grants field's governance card (rendered by Command
    Center, keyed ``trust_grants``/``manual_grants``/``confirm_grants``)
    would raise ``DuplicateWidgetID`` if this table were rendered from two
    call sites in the same script run — this dedup was a deliberate Task 5
    decision (NOT an application of Task 3's reordering exception, which was
    scoped only to minor same-tab cosmetic reordering and does not cover
    this cross-tab move).

    ``stock_price_now``'s earlier history (its Task-5 move here from
    ``views/setup/parameters.py``'s Joint sub-tab, and the 2026-07-24 owner
    approval of that cross-tab relocation) now lives on
    ``render_stock_price_widget``'s docstring, since the widget itself moved
    there.

    The grants field's own governance card (rendered by Command Center) has
    no manual-override ``number_input`` (see ``_render_field_card``'s
    ``field_key != GRANTS_KEY`` branch) and confirming it has no
    session_state mirror to update (see ``_apply_confirm_to_session``'s
    "grants: no direct session_state representation" note) — both
    pre-existing behaviors, unchanged here.
    """
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
        render_grants_pending_caption(container)
        container.caption("Grants are held for the household, not per person — all are shown here.")


def render_stock_price_widget(container) -> None:
    """Render the ``stock_price_now`` stock-price ``number_input`` and the
    assumed stock growth rate (``stock_price_growth_rate``) input.

    Extracted out of ``render_options_partial`` (PR B, 2026-09) so
    ``views/profile_pages.py``'s Assumptions page can render it; the
    "Sync everything" button's Yahoo-quote leg still refreshes it — it is a market-sourced value, not an Options-domain
    input, and the Stock Grants table it used to be co-located with stays
    under Options. Preserves the exact same clamp, ``min_value``/``step``/
    ``format``, dynamic ``_stock_ticker`` label, and
    ``st.session_state.stock_price`` assignment this widget has always used
    (see ``render_options_partial``'s docstring for why it reads/writes
    ``st.session_state.stock_price`` rather than ``hh.stock_price_now``).
    """
    container.subheader("Stock Price")
    st.session_state.stock_price = container.number_input(
        f"{st.session_state.get('_stock_ticker', 'Stock')} Current Price",
        min_value=0,
        value=_clamp(st.session_state.stock_price, 0),
        step=5,
        format="%d",
    )
    st.session_state.stock_price_growth_rate = container.number_input(
        "Assumed stock growth (%/yr)",
        min_value=-50.0,
        max_value=50.0,
        value=_clamp(
            float(
                st.session_state.get(
                    "stock_price_growth_rate", scalar_default("stock_price_growth_rate")
                )
            ),
            -50.0,
            50.0,
        ),
        step=0.5,
        format="%.2f",
        help="Projects the stock price forward on the Option Exercises page.",
    )
