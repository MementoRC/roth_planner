"""Pins the render order of the Setup Data tab's sections.

``views/shells/domains_shell.py``'s ``with tab_data:`` block used to render
Command Center -> Stock Price -> PDF Statements -> Import previous data ->
1040 Import. It was reordered (2026-09) so the two import sections come
FIRST -- getting data IN is the tab's primary job -- giving:
PDF Statements -> Import previous data -> Command Center -> Stock Price ->
1040 Import.

No prior test pinned this order (see domains_shell.py's PR A commit message:
"Known gap, not addressed here: no test pins the Data tab's section
ORDER."). This closes that gap.

Uses the same ``AppTest.from_function`` + minimal session-state seed idiom
as ``tests/test_shells.py``'s ``_render_shell``/``_run_shell`` (rather than
driving the real ``app.py``, which needs more monkeypatching than this
ordering assertion requires).
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

# The 5 subheaders authored directly in domains_shell.py's tab_data block
# (excludes "Account attribution", which render_command_center renders as a
# NESTED subheader inside the "Command Center" section -- filtering to just
# this set keeps the assertion independent of subheaders any partial might
# add or remove internally).
_DATA_TAB_SECTION_LABELS = frozenset(
    {"PDF Statements", "Import previous data", "Command Center", "Stock Price", "1040 Import"}
)

EXPECTED_ORDER = [
    "PDF Statements",
    "Import previous data",
    "Command Center",
    "Stock Price",
    "1040 Import",
]


def _render_shell() -> None:
    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from views.shells import render_setup

    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("txn_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    render_setup(Household())


def _run_shell(monkeypatch) -> AppTest:
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_function(_render_shell)
    at.run()
    return at


def test_data_tab_sections_render_in_new_order(clean_command_center_caches, monkeypatch) -> None:
    at = _run_shell(monkeypatch)
    assert not at.exception

    rendered = [s.value for s in at.subheader if s.value in _DATA_TAB_SECTION_LABELS]
    assert rendered == EXPECTED_ORDER
