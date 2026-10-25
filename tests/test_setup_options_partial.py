"""Tests for ``views/setup/_partials/_options.py``: ``render_options_partial``
(the equity-grants table) and ``render_stock_price_widget`` (the
``stock_price_now`` stock-price widget, PR B, 2026-09 — split out of
``render_options_partial`` and called from the Assumptions page instead of
Options; see that function's docstring).

Task-5 reversal: this partial's inline trust/manual/confirm governance
cards (``stock_price_now``, grants) were removed — those cards render
exclusively in ``views/setup/command_center.py``'s generic per-pending-field
loop again (see that module's docstring; the behavioral tests moved to
``tests/test_command_center_view.py``). This file keeps negative regression
tests guarding against a silent re-introduction of the inline cards, which
would raise ``DuplicateWidgetID`` once Command Center's loop renders the
same fields too.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.resolver import GRANTS_KEY
from engine.store import get_store
from engine.store.namespaces import COMMITTED
from models.grants import StockGrant
from models.sourced import Provenance, Source, SourcedValue

_RECORDED_AT = datetime(2026, 7, 24, 12, 0, 0)


def _seed_pending_stock_price_now() -> None:
    """Committed stock_price_now=100/UNKNOWN + a FINEXTRACT_LIVE=250 candidate."""
    committed_json = {
        "stock_price_now": SourcedValue(100.0, Provenance(Source.UNKNOWN, _RECORDED_AT)).to_json()
    }
    get_store().put(COMMITTED, committed_json)

    store = CandidateStore()
    store.record_candidate(
        "stock_price_now", 250.0, Provenance(Source.FINEXTRACT_LIVE, _RECORDED_AT, "live sync")
    )
    store.save()
    ChoiceMap().save()


def _seed_pending_grants() -> None:
    """Committed 1 grant/UNKNOWN + a FINEXTRACT_LIVE 2-grant candidate list."""
    committed_grant = StockGrant(year=2020, strike=124.0, shares=100, expiry_year=2030)
    committed_json = {
        GRANTS_KEY: {
            "data": [dataclasses.asdict(committed_grant)],
            "prov": [Provenance(Source.UNKNOWN, _RECORDED_AT).to_json()],
        }
    }
    get_store().put(COMMITTED, committed_json)

    candidate_grants = [
        StockGrant(year=2019, strike=98.0, shares=200, expiry_year=2029),
        StockGrant(year=2021, strike=163.0, shares=150, expiry_year=2031),
    ]
    store = CandidateStore()
    store.record_candidate(
        GRANTS_KEY,
        candidate_grants,
        Provenance(Source.FINEXTRACT_LIVE, _RECORDED_AT, "live sync"),
    )
    store.save()
    ChoiceMap().save()


def _render_options_with_pending(pending: set[str]) -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_options_partial

    # PR B (2026-09) moved the stock_price widget out of render_options_partial
    # (see _render_stock_price_widget below) -- this seed is now defensive
    # only, kept in case some future field this partial owns reads it too.
    st.session_state.setdefault("stock_price", 100)
    st.session_state["_pending_review"] = pending
    render_options_partial(Household(), st)


def test_options_partial_renders_without_exception_when_nothing_pending() -> None:
    at = AppTest.from_function(_render_options_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception
    assert any("No grants loaded" in i.value for i in at.info)


def test_options_partial_does_not_render_stock_price_now_card_even_when_pending() -> None:
    """Task-5 reversal regression: render_options_partial must NOT render
    the stock_price_now trust/manual/confirm governance card, even though it
    is pending review. That card renders exclusively in
    views/setup/command_center.py's generic per-pending-field loop now.
    """
    _seed_pending_stock_price_now()

    at = AppTest.from_function(
        _render_options_with_pending, kwargs={"pending": {"stock_price_now"}}
    )
    at.run()

    assert not at.exception
    assert not any(w.key == "confirm_stock_price_now" for w in at.button)


def _render_stock_price_widget(stock_price: float = 100) -> None:
    """PR B (2026-09): the stock_price widget moved out of
    render_options_partial into render_stock_price_widget (now called from
    the Assumptions page, not Options) -- these two tests below drive it directly at
    its new home instead of through render_options_partial.
    """
    import streamlit as st

    from views.setup._partials import render_stock_price_widget

    st.session_state["stock_price"] = stock_price
    render_stock_price_widget(st)


def test_stock_price_widget_stock_price_round_trip() -> None:
    """Unkeyed-widget safety net (Owner decision 5): drive a distinct sentinel
    value through the plain stock_price number_input and confirm
    session_state.stock_price reflects it, catching a typo'd attribute name.
    """
    at = AppTest.from_function(_render_stock_price_widget, kwargs={"stock_price": 100})
    at.run()
    assert not at.exception

    widget = next(w for w in at.number_input if w.label == "Stock Current Price")
    widget.set_value(321).run()
    assert at.session_state["stock_price"] == 321


# --- audit-0823 M3: unclamped value= regression (min-only, StreamlitAPIException) --
#
# stock_price reads value=st.session_state.stock_price directly (not through
# _clamp), min_value=0 -- a negative persisted value crashes the render with
# no user interaction. Must FAIL on unmodified source.


def test_stock_price_negative_persisted_value_does_not_crash() -> None:
    at = AppTest.from_function(_render_stock_price_widget, kwargs={"stock_price": -50})
    at.run()
    assert not at.exception
    widget = next(w for w in at.number_input if w.label.endswith("Current Price"))
    assert widget.value == 0


def test_options_partial_does_not_render_grants_card_even_when_pending() -> None:
    """Task-5 reversal regression: render_options_partial must NOT render
    the grants trust/manual/confirm governance card, even though it is
    pending review. That card renders exclusively in
    views/setup/command_center.py's generic per-pending-field loop now.
    """
    _seed_pending_grants()

    at = AppTest.from_function(_render_options_with_pending, kwargs={"pending": {GRANTS_KEY}})
    at.run()

    assert not at.exception
    assert not any(w.key == "confirm_grants" for w in at.button)


# --- Owner decision 6 (2026-07-24, post-hoc) regression pin -----------------
#
# A spec-compliance review of commit 19e04f69 (this file's originating
# commit) found that nothing pinned WHICH tab the Stock Price widget renders
# under after its Parameters -> Joint to Portfolio move — a future change
# could silently relocate it again with nothing catching it. Drives the
# real, fully-assembled ``app.py`` (mirrors
# ``tests/test_setup_shell_characterization.py``'s ``setup_app_test``
# fixture) rather than an isolated ``render_options_partial`` call, since the
# whole point is to verify tab PLACEMENT, which only exists once the partial
# is composed into the real ``st.tabs()`` nesting.
#
# Domains-only shell update (refactor/domains-only-shell): the widget moved
# to the "Options" tab (``views/shells/domains_shell.py`` co-located it with
# the stock-grants table via ``render_options_partial``).
#
# PR B (2026-09): the widget moved a THIRD time, out of Options entirely,
# into the Data tab via the new ``render_stock_price_widget`` (called
# directly from the shell, not through ``render_options_partial``
# anymore) — it is a market-sourced value already refreshed by "Sync
# everything"'s Yahoo-quote leg. It has since moved again, to Profile ›
# Assumptions (redesign PR 3b).
# ``views/setup/portfolio.py:render_portfolio_tab``, the function that used
# to co-locate options+portfolio under one "Portfolio" tab, was deleted in
# the same PR (zero callers left, verified by grep across app.py and
# views/). Search for "Assumptions" below, not "Options".

_APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def test_stock_price_widget_renders_once_under_data_tab(monkeypatch) -> None:
    """Owner decision 6 (as amended by PR B, 2026-09): the Stock Price widget
    lives on Profile › Assumptions now, rendered by
    ``render_stock_price_widget`` — no longer co-located with the
    stock-grants table under Options (see this file's module-level comment
    block above for the full move history).

    Rather than re-pin a tab name that a future shell refactor could just as
    easily invalidate again, the guard also checks that the widget's label
    appears EXACTLY ONCE across the entire rendered page, regardless of
    which tab(s) exist — this is strictly stronger than "under Data": it
    also catches duplication into a *new* rival tab that never existed at
    all when this test was written.
    """
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_file(str(_APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "assumptions"
    at.run()
    assert not at.exception

    # The widget label is f"{ticker} Current Price" — app.py's real seeding
    # path (config/defaults.py's synthetic "ACME" ticker) differs from
    # render_stock_price_widget's own isolated-call fallback ("Stock", used
    # by this file's other tests, which never go through app.py's
    # _seed_session_state()). Read the actual seeded ticker rather than
    # hardcoding either.
    expected_label = f"{at.session_state['_stock_ticker']} Current Price"

    # Redesign PR 3b: the widget lives on the Profile › Assumptions page (no
    # tabs), so presence is checked page-wide.
    assert any(w.label == expected_label for w in at.number_input)

    # Structure-independent uniqueness guard: the widget must appear exactly
    # once anywhere on the page, not merely "under Data" (see docstring).
    match_count = sum(1 for w in at.number_input if w.label == expected_label)
    assert match_count == 1, (
        f"expected exactly one {expected_label!r} number_input on the page, found {match_count}"
    )
