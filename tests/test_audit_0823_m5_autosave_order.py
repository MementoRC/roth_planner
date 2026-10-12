"""audit-0823 models-views/M5: a page's autosave must run LAST.

Each Profile/Data page ends with exactly one ``autosave_user_defaults()``
(redesign PR 3b; before that, the six-tab Setup shell had one at the foot of
``render()``).

The order matters because the stock-price widget is an UNKEYED controlled
widget (``views/setup/_partials/_options.py``)::

    st.session_state.stock_price = container.number_input(...)

With no ``key=``, Streamlit never restores the user's new value into
``session_state`` at script start; the value reaches ``session_state`` ONLY via
that explicit assignment, at instantiation time. An autosave that ran before the
widget rendered would read the PRE-EDIT value and persist it, flushing the edit
one rerun late (the real loss window is "edit the stock price, then immediately
navigate away").

Two guarantees, both parametrized over the six pages where meaningful:

1. Every page saves exactly once per render (no mid-render second autosave).
2. On Assumptions, which hosts the stock-price widget, the last save sees a
   same-run write. The probe patches ``render_stock_price_widget`` with a stub
   performing the same ``st.session_state.stock_price = <new value>`` assignment
   the real widget does at instantiation, which keeps the test about ORDERING
   rather than Streamlit's widget plumbing. The other pages have no cheap
   probe, so they only get guarantee 1.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

_EDITED_STOCK_PRICE = 987.65

PAGE_FUNCS = {
    "household": "views.profile_pages:render_household",
    "assumptions": "views.profile_pages:render_assumptions",
    "holdings": "views.profile_pages:render_holdings",
    "import": "views.data_pages:render_import",
    "review": "views.data_pages:render_review",
    "backup": "views.data_pages:render_backup",
}


def _seed_and_render_page(target: str) -> None:
    """Seed the session the way the pages expect, then render one.

    Deliberately does NOT set ``_suppress_snapshot_autoload`` -- that guard makes
    ``autosave_user_defaults()`` return early, which would make this test
    vacuous.
    """
    import importlib

    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.household import Household

    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("stock_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    mod, attr = target.split(":")
    getattr(importlib.import_module(mod), attr)(Household())


@pytest.fixture
def _neutralize_disk_sources(monkeypatch):
    """Isolate the pages from a developer's real V2 pubkey / PDF-tax cache / SSA file."""
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


@pytest.mark.usefixtures("_neutralize_disk_sources")
def test_assumptions_autosave_sees_same_run_stock_price_write(
    clean_command_center_caches, monkeypatch
) -> None:
    """audit-0823 M5: the page's autosave must observe a same-run write.

    RED while ``autosave_user_defaults()`` runs before the stock-price widget has
    rendered: the payload would carry the pre-edit price. GREEN once the call is
    the last statement of ``render_assumptions``.
    """
    import streamlit as st

    import views.profile_pages as profile_pages_mod
    import views.setup._state as state_mod

    saved_payloads: list[dict] = []
    monkeypatch.setattr(
        state_mod, "save_user_defaults", lambda payload: saved_payloads.append(dict(payload))
    )

    def _fake_stock_price_widget(container) -> None:
        st.session_state.stock_price = _EDITED_STOCK_PRICE

    monkeypatch.setattr(profile_pages_mod, "render_stock_price_widget", _fake_stock_price_widget)

    at = AppTest.from_function(
        _seed_and_render_page, kwargs={"target": PAGE_FUNCS["assumptions"]}
    ).run()
    assert not at.exception

    assert saved_payloads, "Assumptions never reached save_user_defaults"
    assert saved_payloads[-1]["stock_price_now"] == _EDITED_STOCK_PRICE, (
        "autosave ran before the page wrote session_state.stock_price — "
        "the edit is persisted one rerun late"
    )


@pytest.mark.usefixtures("_neutralize_disk_sources")
@pytest.mark.parametrize("page_id", list(PAGE_FUNCS))
def test_page_autosave_runs_exactly_once_per_render(
    page_id, clean_command_center_caches, monkeypatch
) -> None:
    """Moving the call must not leave a second autosave behind mid-render."""
    import views.setup._state as state_mod

    calls: list[dict] = []
    monkeypatch.setattr(
        state_mod, "save_user_defaults", lambda payload: calls.append(dict(payload))
    )

    at = AppTest.from_function(_seed_and_render_page, kwargs={"target": PAGE_FUNCS[page_id]}).run()
    assert not at.exception

    assert len(calls) == 1, f"{page_id}: expected exactly one autosave per render, got {len(calls)}"
