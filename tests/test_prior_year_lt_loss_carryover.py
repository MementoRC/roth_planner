"""Issue #137 slice 1: the hand-entered prior-year long-term capital loss carryover."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from engine.scenario import ProjectionState
from models.household import Household

_LABEL = "Capital loss carryover from last year's return (long-term)"


def test_projection_state_seeds_carryover_from_household() -> None:
    hh = Household(prior_year_lt_loss_carryover=10_000.0)
    assert ProjectionState.initial(hh).capital_loss_carryover == 10_000.0


def test_default_carryover_is_zero() -> None:
    assert ProjectionState.initial(Household()).capital_loss_carryover == 0.0


def test_negative_carryover_is_clamped_to_zero() -> None:
    hh = Household(prior_year_lt_loss_carryover=-5_000.0)
    assert hh.prior_year_lt_loss_carryover == 0.0
    hh.prior_year_lt_loss_carryover = -1.0
    assert ProjectionState.initial(hh).capital_loss_carryover == 0.0


def _render() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_assumptions_partial

    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", 60_000)
    st.session_state["_pending_review"] = set()
    render_assumptions_partial(Household(), st)


def test_assumptions_input_round_trips_to_session_state() -> None:
    at = AppTest.from_function(_render, default_timeout=60)
    at.run()
    assert not at.exception
    widget = next(w for w in at.number_input if w.label == _LABEL)
    assert widget.value == 0
    widget.set_value(12_000).run()
    assert at.session_state["prior_year_lt_loss_carryover"] == 12_000


def _render_negative() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_assumptions_partial

    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", 60_000)
    st.session_state["_pending_review"] = set()
    st.session_state.setdefault("prior_year_lt_loss_carryover", -3_000)
    render_assumptions_partial(Household(), st)


def test_assumptions_input_clamps_stored_negative() -> None:
    at = AppTest.from_function(_render_negative, default_timeout=60)
    at.run()
    assert not at.exception
    assert at.session_state["prior_year_lt_loss_carryover"] == 0
