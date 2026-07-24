"""Hub Setup shell — the same 5 data-domain groups as ``domains_shell.py``,
rendered as collapsible ``st.expander(...)`` accordions on one page instead
of tabs (Task 8 of the ui-shell-theme-toggle plan).

Composes the SAME five partials Tasks 3-7 extracted
(``views/setup/_partials/``) plus
``views/setup/data_bridge.py:render_data_bridge_tab`` unchanged — identical
partial calls to ``domains_shell.py``, only the outer layout primitive
(``st.expander`` instead of ``st.tabs``) differs. Data bridge gets its own
expander too — the plan leaves this choice to implementer judgment ("stays
a separate small section or its own expander"); an expander is the
simplest, most consistent choice alongside the other 5 groups.

Nesting note: ``render_portfolio_partial``'s own helpers nest a sub-``st.tabs``
(Me/Spouse/All) and a sub-``st.expander`` (Account Type Overrides) inside
whatever ``container`` they're given (see
``views/setup/_partials/_portfolio.py``'s module docstring for the
code-quality fix that made this safe). Nesting an expander inside another
expander is expander-inside-expander — Streamlit's docs discourage this for
readability but it does not raise; verified empirically via this module's
AppTest smoke test in ``tests/test_shells.py``.
"""

from __future__ import annotations

import streamlit as st

from models.household import Household
from views.setup._partials import (
    render_accounts_partial,
    render_assumptions_partial,
    render_household_partial,
    render_options_partial,
    render_portfolio_partial,
)
from views.setup.data_bridge import render_data_bridge_tab


def render(hh: Household) -> None:
    """Render the Hub Setup layout: 6 expanders grouped by data domain, one page."""
    st.title("⚙️ Setup — Hub")

    exp_household = st.expander("🏠 Household", expanded=True)
    exp_household.subheader("Filing status")
    _is_single = bool(render_household_partial(hh, exp_household, "joint"))
    col_you, col_spouse = exp_household.columns(2)
    col_you.subheader("Me")
    render_household_partial(hh, col_you, "your")
    col_spouse.subheader("Spouse")
    if _is_single:
        col_spouse.info(
            "Single filer — spouse inputs are disabled and treated as zero. "
            "Switch Filing status to Married filing jointly to re-enable."
        )
    render_household_partial(hh, col_spouse, "spouse")

    exp_accounts = st.expander("💰 Accounts")
    col_you, col_spouse = exp_accounts.columns(2)
    col_you.subheader("Me")
    render_accounts_partial(hh, col_you, "your")
    col_spouse.subheader("Spouse")
    render_accounts_partial(hh, col_spouse, "spouse")

    exp_options = st.expander("📈 Options")
    render_options_partial(hh, exp_options)

    exp_assumptions = st.expander("🧮 Assumptions")
    render_assumptions_partial(hh, exp_assumptions)

    exp_portfolio = st.expander("💼 Portfolio")
    render_portfolio_partial(hh, exp_portfolio)

    with st.expander("🔗 Data bridge"):
        # render_data_bridge_tab(hh) takes no container arg — see
        # domains_shell.py's identical note on this same call.
        render_data_bridge_tab(hh)


__all__ = ["render"]
