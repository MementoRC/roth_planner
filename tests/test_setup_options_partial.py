"""Tests for ``views/setup/_partials.py:render_options_partial`` — the
equity-grants table + ``txn_price_now`` stock-price widget.

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

    # txn_price is read unconditionally (st.session_state.txn_price, not
    # .get()) by render_options_partial's own price widget -- must be
    # pre-seeded (setdefault, not a plain assignment, so a confirm button's
    # rerun of this same function doesn't clobber the just-confirmed value).
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

    at = AppTest.from_function(
        _render_options_with_pending, kwargs={"pending": {"txn_price_now"}}
    )
    at.run()

    assert not at.exception
    assert not any(w.key == "confirm_txn_price_now" for w in at.button)


def test_options_partial_txn_price_widget_round_trip(clean_command_center_caches) -> None:
    """Unkeyed-widget safety net (Owner decision 5): drive a distinct sentinel
    value through the plain (not-pending) txn_price number_input and confirm
    session_state.txn_price reflects it, catching a typo'd attribute name.
    """
    at = AppTest.from_function(_render_options_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    widget = next(w for w in at.number_input if w.label == "Stock Current Price")
    widget.set_value(321).run()
    assert at.session_state["txn_price"] == 321


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


def test_txn_price_widget_renders_under_portfolio_not_parameters_joint(
    clean_command_center_caches, monkeypatch
) -> None:
    """Owner decision 6: the Stock Price widget lives under Setup -> Portfolio
    (co-located with the stock-grants table via ``render_options_partial``),
    NOT under Setup -> Parameters -> Joint (its pre-Task-5 location). Pins
    the deliberate cross-tab move so a future refactor can't silently drop
    or relocate it without a test failing.
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
    # render_options_partial's own isolated-call fallback ("Stock", used by
    # this file's other tests, which never go through app.py's
    # _seed_session_state()). Read the actual seeded ticker rather than
    # hardcoding either.
    expected_label = f"{at.session_state['_stock_ticker']} Current Price"

    portfolio_tab = _find_tab_block(at.main, "💼 Portfolio")
    joint_tab = _find_tab_block(at.main, "Joint")
    assert portfolio_tab is not None, "Portfolio tab not found in rendered Setup page"
    assert joint_tab is not None, "Parameters -> Joint sub-tab not found in rendered Setup page"

    assert any(w.label == expected_label for w in portfolio_tab.number_input)
    assert not any(w.label == expected_label for w in joint_tab.number_input)
