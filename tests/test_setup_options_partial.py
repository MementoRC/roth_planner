"""Tests for ``views/setup/_partials/_options.py``: ``render_options_partial``
(the equity-grants table) and ``render_stock_price_widget`` (the
``txn_price_now`` stock-price widget, PR B, 2026-09 — split out of
``render_options_partial`` and called from the "📥 Data" tab instead of
Options; see that function's docstring).

Task-5 reversal: this partial's inline trust/manual/confirm governance
cards (``txn_price_now``, grants) were removed — those cards render
exclusively in ``views/setup/command_center.py``'s generic per-pending-field
loop again (see that module's docstring; the behavioral tests moved to
``tests/test_command_center_view.py``). This file keeps negative regression
tests guarding against a silent re-introduction of the inline cards, which
would raise ``DuplicateWidgetID`` once Command Center's loop renders the
same fields too.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.resolver import GRANTS_KEY
from models.grants import StockGrant
from models.sourced import Provenance, Source, SourcedValue

_RECORDED_AT = datetime(2026, 7, 24, 12, 0, 0)


def _seed_pending_txn_price_now() -> None:
    """Committed txn_price_now=100/UNKNOWN + a FINEXTRACT_LIVE=250 candidate."""
    # audit-0805 W1: re-import at call time (not the module-level binding
    # frozen at collection) to see tests/conftest.py's per-test redirect.
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH

    committed_json = {
        "txn_price_now": SourcedValue(100.0, Provenance(Source.UNKNOWN, _RECORDED_AT)).to_json()
    }
    COMMITTED_PATH.write_text(json.dumps(committed_json))

    store = CandidateStore()
    store.record_candidate(
        "txn_price_now", 250.0, Provenance(Source.FINEXTRACT_LIVE, _RECORDED_AT, "live sync")
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)


def _seed_pending_grants() -> None:
    """Committed 1 grant/UNKNOWN + a FINEXTRACT_LIVE 2-grant candidate list."""
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH

    committed_grant = StockGrant(year=2020, strike=130.0, shares=100, expiry_year=2030)
    committed_json = {
        GRANTS_KEY: {
            "data": [dataclasses.asdict(committed_grant)],
            "prov": [Provenance(Source.UNKNOWN, _RECORDED_AT).to_json()],
        }
    }
    COMMITTED_PATH.write_text(json.dumps(committed_json))

    candidate_grants = [
        StockGrant(year=2019, strike=104.0, shares=200, expiry_year=2029),
        StockGrant(year=2021, strike=169.0, shares=150, expiry_year=2031),
    ]
    store = CandidateStore()
    store.record_candidate(
        GRANTS_KEY,
        candidate_grants,
        Provenance(Source.FINEXTRACT_LIVE, _RECORDED_AT, "live sync"),
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)


def _render_options_with_pending(pending: set[str]) -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_options_partial

    # PR B (2026-09) moved the txn_price widget out of render_options_partial
    # (see _render_stock_price_widget below) -- this seed is now defensive
    # only, kept in case some future field this partial owns reads it too.
    st.session_state.setdefault("txn_price", 100)
    st.session_state["_pending_review"] = pending
    render_options_partial(Household(), st)


def test_options_partial_renders_without_exception_when_nothing_pending(
    clean_command_center_caches,
) -> None:
    at = AppTest.from_function(_render_options_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception
    assert any("No grants loaded" in i.value for i in at.info)


def test_options_partial_does_not_render_txn_price_now_card_even_when_pending(
    clean_command_center_caches,
) -> None:
    """Task-5 reversal regression: render_options_partial must NOT render
    the txn_price_now trust/manual/confirm governance card, even though it
    is pending review. That card renders exclusively in
    views/setup/command_center.py's generic per-pending-field loop now.
    """
    _seed_pending_txn_price_now()

    at = AppTest.from_function(_render_options_with_pending, kwargs={"pending": {"txn_price_now"}})
    at.run()

    assert not at.exception
    assert not any(w.key == "confirm_txn_price_now" for w in at.button)


def _render_stock_price_widget(txn_price: float = 100) -> None:
    """PR B (2026-09): the txn_price widget moved out of
    render_options_partial into render_stock_price_widget (now called from
    the Data tab, not Options) -- these two tests below drive it directly at
    its new home instead of through render_options_partial.
    """
    import streamlit as st

    from views.setup._partials import render_stock_price_widget

    st.session_state["txn_price"] = txn_price
    render_stock_price_widget(st)


def test_stock_price_widget_txn_price_round_trip(clean_command_center_caches) -> None:
    """Unkeyed-widget safety net (Owner decision 5): drive a distinct sentinel
    value through the plain txn_price number_input and confirm
    session_state.txn_price reflects it, catching a typo'd attribute name.
    """
    at = AppTest.from_function(_render_stock_price_widget, kwargs={"txn_price": 100})
    at.run()
    assert not at.exception

    widget = next(w for w in at.number_input if w.label == "Stock Current Price")
    widget.set_value(321).run()
    assert at.session_state["txn_price"] == 321


# --- audit-0823 M3: unclamped value= regression (min-only, StreamlitAPIException) --
#
# txn_price reads value=st.session_state.txn_price directly (not through
# _clamp), min_value=0 -- a negative persisted value crashes the render with
# no user interaction. Must FAIL on unmodified source.


def test_txn_price_negative_persisted_value_does_not_crash(
    clean_command_center_caches,
) -> None:
    at = AppTest.from_function(_render_stock_price_widget, kwargs={"txn_price": -50})
    at.run()
    assert not at.exception
    widget = next(w for w in at.number_input if w.label.endswith("Current Price"))
    assert widget.value == 0


def test_options_partial_does_not_render_grants_card_even_when_pending(
    clean_command_center_caches,
) -> None:
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
# into the "📥 Data" tab via the new ``render_stock_price_widget`` (called
# directly from ``domains_shell.py``, not through ``render_options_partial``
# anymore) — it is a market-sourced value already refreshed by "Sync
# everything"'s Yahoo-quote leg, so it now renders beside the sync controls.
# ``views/setup/portfolio.py:render_portfolio_tab``, the function that used
# to co-locate options+portfolio under one "Portfolio" tab, was deleted in
# the same PR (zero callers left, verified by grep across app.py and
# views/). Search for "📥 Data" below, not "Options".

_APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _find_tab_block(block, label: str):
    """Recursively locate a nested ``tab`` ``Block`` by its ``label``.

    Streamlit tabs nest arbitrarily deep (top-level Setup tabs contain their
    own Me/Spouse/Joint or Me/Spouse/All sub-tabs), so this walks the whole
    subtree rather than assuming a fixed depth. Only ``Block`` nodes carry a
    ``children`` mapping — leaf ``Element`` nodes (e.g. ``Title``) raise
    ``AttributeError`` on ``.children`` (proto fallback), so ``hasattr``
    guards the recursion into those.
    """
    for child in block.children.values():
        if getattr(child, "type", None) == "tab" and getattr(child, "label", None) == label:
            return child
        if hasattr(child, "children"):
            found = _find_tab_block(child, label)
            if found is not None:
                return found
    return None


def test_txn_price_widget_renders_once_under_data_tab(
    clean_command_center_caches, monkeypatch
) -> None:
    """Owner decision 6 (as amended by PR B, 2026-09): the Stock Price widget
    lives under Setup -> "📥 Data" now, rendered by the new
    ``render_stock_price_widget`` called directly from
    ``views/shells/domains_shell.py`` — no longer co-located with the
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
    at.run()
    assert not at.exception

    # The widget label is f"{ticker} Current Price" — app.py's real seeding
    # path (config/defaults.py's synthetic "ACME" ticker) differs from
    # render_stock_price_widget's own isolated-call fallback ("Stock", used
    # by this file's other tests, which never go through app.py's
    # _seed_session_state()). Read the actual seeded ticker rather than
    # hardcoding either.
    expected_label = f"{at.session_state['_stock_ticker']} Current Price"

    data_tab = _find_tab_block(at.main, "📥 Data")
    assert data_tab is not None, "📥 Data tab not found in rendered Setup page"

    assert any(w.label == expected_label for w in data_tab.number_input)

    # Structure-independent uniqueness guard: the widget must appear exactly
    # once anywhere on the page, not merely "under Data" (see docstring).
    match_count = sum(1 for w in at.number_input if w.label == expected_label)
    assert match_count == 1, (
        f"expected exactly one {expected_label!r} number_input on the page, found {match_count}"
    )
