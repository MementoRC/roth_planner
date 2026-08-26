"""Tests for ``views/setup/_partials.py:render_accounts_partial`` — IRA/Roth/
SS-FRA balance widgets.

Task-4 reversal: this partial's inline trust/manual/confirm governance card
was removed — those cards render exclusively in
``views/setup/command_center.py``'s generic per-pending-field loop again
(see that module's docstring; the behavioral tests moved to
``tests/test_command_center_view.py``). This file keeps a negative
regression test guarding against a silent re-introduction of the inline
card, which would raise ``DuplicateWidgetID`` once Command Center's loop
renders the same field too.
"""

from __future__ import annotations

import json
from datetime import datetime

from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from models.sourced import Provenance, Source, SourcedValue

_RECORDED_AT = datetime(2026, 7, 24, 12, 0, 0)


def _seed_pending_your_ira() -> None:
    """Committed your_ira=1.7M/UNKNOWN + a FINEXTRACT_LIVE your_ira=2.0M candidate."""
    # audit-0805 W1: re-import at call time (not the module-level binding
    # frozen at collection) to see tests/conftest.py's per-test redirect.
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH

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
    # not .get()) by render_accounts_partial's own balance widgets, so both
    # must be pre-seeded (matching the committed baseline used below) or
    # Streamlit raises AttributeError before the widgets ever render.
    # setdefault (not a plain assignment) so a rerun of this same function
    # doesn't clobber a since-changed value back to the seed.
    st.session_state.setdefault("your_ira", 1_700_000)
    st.session_state.setdefault("your_ss_fra", 2_000)
    st.session_state["_pending_review"] = {"your_ira"}
    render_accounts_partial(Household(), st, "your")


def test_accounts_partial_does_not_render_governance_card_even_when_pending(
    clean_command_center_caches,
) -> None:
    """Task-4 reversal regression: render_accounts_partial must NOT render
    the your_ira trust/manual/confirm governance card, even though your_ira
    is pending review. That card renders exclusively in
    views/setup/command_center.py's generic per-pending-field loop now (see
    that module's docstring, and
    tests/test_setup_shell_characterization.py's DuplicateWidgetID
    regression test for why two renderers of the same widget key cannot
    coexist).
    """
    _seed_pending_your_ira()

    at = AppTest.from_function(_render_your_accounts_with_pending_ira)
    at.run()

    assert not at.exception
    assert not any(w.key == "confirm_your_ira" for w in at.button)
