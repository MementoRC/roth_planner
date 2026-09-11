"""audit-0823 models-views/M5: Classic/Contextual autosave must run LAST.

``views/setup/__init__.py:render`` composes four tabs in source order:

    1. Command Center   2. Parameters   3. Portfolio   4. Data bridge

Streamlit executes EVERY tab body on every script run, in source order.
Pre-fix, the autosave lived at the end of
``views/setup/parameters.py:render_parameters_tab`` -- i.e. the last
statement of *tab 2* -- so it ran before tabs 3 and 4 had instantiated
their widgets.

That ordering matters because the Portfolio tab's stock-price widget is an
UNKEYED controlled widget (``views/setup/_partials/_options.py``)::

    st.session_state.txn_price = container.number_input(...)

With no ``key=``, Streamlit never restores the user's new value into
``session_state`` at script start; the value reaches ``session_state`` ONLY
via that explicit assignment, at instantiation time, in tab 3. So on the
rerun the edit itself triggers, a tab-2 autosave reads the PRE-EDIT value
and persists it. The edit is flushed one rerun late -- it self-heals on any
further interaction, so the real loss window is "user edits the stock price,
then immediately navigates away".

The other three shells (Domains/Hub/Wizard) already call
``autosave_user_defaults()`` as the last statement of their ``render()``
(audit-0823 M2, PR #462). Classic and Contextual -- which both wrap
``views.setup.render`` -- were the two that did not.

The stub below stands in for the real unkeyed widget by performing the same
``st.session_state.txn_price = <new value>`` assignment the real one does at
instantiation, which keeps the test about ORDERING rather than about
Streamlit's widget-value plumbing.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

_EDITED_TXN_PRICE = 987.65


def _seed_and_render_classic() -> None:
    """Seed the session the way the Setup shells expect, then render Classic.

    Deliberately does NOT set ``_suppress_snapshot_autoload`` -- that guard
    makes ``autosave_user_defaults()`` return early, which would make this
    test vacuous (see tests/test_shells.py's identical note).
    """
    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from views.shells import render_setup

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

    render_setup(Household(), "Classic")


@pytest.fixture
def _neutralize_disk_sources(monkeypatch):
    """Same disk-source neutralization tests/test_shells.py's shell runners use."""
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


@pytest.mark.usefixtures("_neutralize_disk_sources")
def test_classic_autosave_sees_portfolio_tab_edits(
    clean_command_center_caches, monkeypatch
) -> None:
    """audit-0823 M5: the Classic autosave must observe tab-3 session writes.

    RED while ``autosave_user_defaults()`` is the last statement of
    ``render_parameters_tab`` (tab 2): the payload carries the pre-edit
    stock price. GREEN once the call moves to the last statement of
    ``views.setup.render``, after all four tabs have rendered.
    """
    import streamlit as st

    import views.setup._state as state_mod
    import views.setup.portfolio as portfolio_mod

    saved_payloads: list[dict] = []
    monkeypatch.setattr(
        state_mod, "save_user_defaults", lambda payload: saved_payloads.append(dict(payload))
    )

    def _fake_options_partial(hh, container) -> None:
        """Stand-in for the real unkeyed controlled widget in tab 3."""
        st.session_state.txn_price = _EDITED_TXN_PRICE

    monkeypatch.setattr(portfolio_mod, "render_options_partial", _fake_options_partial)

    at = AppTest.from_function(_seed_and_render_classic).run()
    assert not at.exception

    assert saved_payloads, "Classic shell never reached save_user_defaults"
    assert saved_payloads[-1]["stock_price_now"] == _EDITED_TXN_PRICE, (
        "autosave ran before the Portfolio tab wrote session_state.txn_price — "
        "the edit is persisted one rerun late"
    )


@pytest.mark.usefixtures("_neutralize_disk_sources")
def test_classic_autosave_runs_exactly_once_per_render(
    clean_command_center_caches, monkeypatch
) -> None:
    """Moving the call must not leave a second autosave behind in tab 2."""
    import views.setup._state as state_mod

    calls: list[dict] = []
    monkeypatch.setattr(
        state_mod, "save_user_defaults", lambda payload: calls.append(dict(payload))
    )

    at = AppTest.from_function(_seed_and_render_classic).run()
    assert not at.exception

    assert len(calls) == 1, f"expected exactly one autosave per render, got {len(calls)}"
