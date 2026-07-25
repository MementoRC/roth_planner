"""Tests for ``views/setup/_partials.py:render_accounts_partial`` — Task 4 of
the ui-shell-theme-toggle plan.

IRA/Roth/SS-FRA balance widgets plus their inline trust/manual/confirm
governance card, extracted out of Command Center's old generic
per-pending-field loop (see ``views/setup/command_center.py``'s module
docstring for why that loop was removed entirely rather than filtered) so
each of these six governed fields' card now co-locates with its own balance
widget instead. Uses ``streamlit.testing.v1.AppTest.from_function`` (mirrors
``tests/test_command_center_view.py``'s pattern this supersedes for
``your_ira``).
"""

from __future__ import annotations

import json
from datetime import datetime

from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from models.sourced import Provenance, Source, SourcedValue

_RECORDED_AT = datetime(2026, 7, 24, 12, 0, 0)


def _seed_pending_your_ira() -> None:
    """Committed your_ira=1.7M/UNKNOWN + a FINEXTRACT_LIVE your_ira=2.0M candidate."""
    committed_json = {
        "your_ira": SourcedValue(1_700_000.0, Provenance(Source.UNKNOWN, _RECORDED_AT)).to_json()
    }
    COMMITTED_PATH.write_text(json.dumps(committed_json))

    store = CandidateStore()
    store.record_candidate(
        "your_ira", 2_000_000.0, Provenance(Source.FINEXTRACT_LIVE, _RECORDED_AT, "live sync")
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)


def _render_your_accounts_with_pending_ira() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_accounts_partial

    # your_ira/your_ss_fra are read unconditionally (st.session_state.<attr>,
    # not .get()) by render_accounts_partial's own balance widgets -- unlike
    # the old command_center.py card (which never touched these keys), this
    # partial ALSO renders the balance number_input itself, so both must be
    # pre-seeded (matching the committed baseline used below) or Streamlit
    # raises AttributeError before the card ever renders. setdefault (not a
    # plain assignment) so a later confirm-button click's rerun of this same
    # function doesn't clobber the just-confirmed value back to the seed.
    st.session_state.setdefault("your_ira", 1_700_000)
    st.session_state.setdefault("your_ss_fra", 2_000)
    st.session_state["_pending_review"] = {"your_ira"}
    render_accounts_partial(Household(), st, "your")


def test_accounts_partial_renders_and_shows_pending_candidate(
    clean_command_center_caches,
) -> None:
    """Was ``test_command_center_renders_and_shows_pending_candidate`` against
    ``views/setup/command_center.py``'s generic loop (deleted in Task 4) —
    the card renders inline inside ``render_accounts_partial`` now."""
    _seed_pending_your_ira()

    at = AppTest.from_function(_render_your_accounts_with_pending_ira)
    at.run()

    assert not at.exception
    rendered_text = "\n".join(m.value for m in at.markdown) + "\n".join(
        c.value for c in at.caption
    )
    assert "2,000,000" in rendered_text  # the FINEXTRACT_LIVE candidate value
    assert "1,700,000" in rendered_text  # the currently-committed value


def test_accounts_partial_confirm_button_commits_chosen_source_and_syncs_session(
    clean_command_center_caches,
) -> None:
    """Was ``test_confirm_button_commits_chosen_source_and_syncs_session``
    against ``command_center.py`` (deleted in Task 4) — same behavior, now
    exercised through ``render_accounts_partial`` directly."""
    _seed_pending_your_ira()

    at = AppTest.from_function(_render_your_accounts_with_pending_ira)
    at.run()
    assert not at.exception

    at.button(key="confirm_your_ira").click().run()

    assert not at.exception
    assert at.session_state["your_ira"] == 2_000_000.0

    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert committed_json["your_ira"]["value"] == 2_000_000.0
    assert committed_json["your_ira"]["source"] == "FINEXTRACT_LIVE"

    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    choice = choices.get("your_ira")
    assert choice is not None
    assert choice.source == Source.FINEXTRACT_LIVE
