"""Tests for ``views/setup/_partials.py:render_options_partial`` — Task 5 of
the ui-shell-theme-toggle plan.

Equity-grants table + ``txn_price_now`` stock-price widget, plus each
field's inline trust/manual/confirm governance card, extracted out of
Command Center's old generic per-pending-field loop (see
``views/setup/command_center.py``'s module docstring for why that loop was
removed entirely rather than filtered) so both fields' cards now co-locate
with their owning widgets instead. Uses
``streamlit.testing.v1.AppTest.from_function`` (mirrors
``tests/test_setup_accounts_partial.py``'s pattern this supersedes for
``txn_price_now``/``grants``).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime

from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from engine.data_sources.resolver import GRANTS_KEY
from models.grants import StockGrant
from models.sourced import Provenance, Source, SourcedValue

_RECORDED_AT = datetime(2026, 7, 24, 12, 0, 0)


def _seed_pending_txn_price_now() -> None:
    """Committed txn_price_now=100/UNKNOWN + a FINEXTRACT_LIVE=250 candidate."""
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


def test_options_partial_shows_pending_txn_price_now_candidate(
    clean_command_center_caches,
) -> None:
    _seed_pending_txn_price_now()

    at = AppTest.from_function(
        _render_options_with_pending, kwargs={"pending": {"txn_price_now"}}
    )
    at.run()

    assert not at.exception
    rendered_text = "\n".join(m.value for m in at.markdown) + "\n".join(
        c.value for c in at.caption
    )
    assert "250" in rendered_text  # the FINEXTRACT_LIVE candidate value
    assert "100" in rendered_text  # the currently-committed value


def test_options_partial_confirm_txn_price_now_syncs_the_aliased_session_key(
    clean_command_center_caches,
) -> None:
    """Bug 2 regression: the Household attr is txn_price_now, but the Setup
    number_input widget reads/writes session_state["txn_price"] (alias). The
    confirm handler must write the SAME aliased key, or the next
    reconcile_manual_edits sees session_state.txn_price still stale and
    reverts the confirm.
    """
    _seed_pending_txn_price_now()

    at = AppTest.from_function(
        _render_options_with_pending, kwargs={"pending": {"txn_price_now"}}
    )
    at.run()
    assert not at.exception

    at.button(key="confirm_txn_price_now").click().run()

    assert not at.exception
    assert at.session_state["txn_price"] == 250.0
    assert "txn_price_now" not in at.session_state

    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert committed_json["txn_price_now"]["value"] == 250.0
    assert committed_json["txn_price_now"]["source"] == "FINEXTRACT_LIVE"


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


def test_options_partial_shows_pending_grants_candidate(clean_command_center_caches) -> None:
    _seed_pending_grants()

    at = AppTest.from_function(_render_options_with_pending, kwargs={"pending": {GRANTS_KEY}})
    at.run()

    assert not at.exception
    rendered_text = "\n".join(m.value for m in at.markdown) + "\n".join(
        c.value for c in at.caption
    )
    assert "2 grants" in rendered_text  # the FINEXTRACT_LIVE candidate value
    assert "1 grants" in rendered_text  # the currently-committed value


def test_options_partial_confirm_grants_commits_candidate_list(
    clean_command_center_caches,
) -> None:
    _seed_pending_grants()

    at = AppTest.from_function(_render_options_with_pending, kwargs={"pending": {GRANTS_KEY}})
    at.run()
    assert not at.exception

    at.button(key="confirm_grants").click().run()

    assert not at.exception
    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert len(committed_json[GRANTS_KEY]["data"]) == 2
    assert {g["year"] for g in committed_json[GRANTS_KEY]["data"]} == {2019, 2021}

    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    choice = choices.get(GRANTS_KEY)
    assert choice is not None
    assert choice.source == Source.FINEXTRACT_LIVE
