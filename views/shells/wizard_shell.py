"""Wizard shell -- step-by-step Setup flow over the shared partials.

Reuses the standard shell contract render(hh) -> None. The current step
index lives in st.session_state["wizard_step"]; the shell itself is
stateless. Each step body dispatches to the same
``views/setup/_partials/`` partials the other shells (Classic/Domains/Hub/
Contextual) compose, using the identical owner-composition pattern
``views/shells/domains_shell.py`` established (owner strings are literally
"joint"/"your"/"spouse" for Household, "your"/"spouse" for Accounts).
Back/Next navigation mutates the step index, clamped to
``[0, len(SETUP_STEP_GROUPS) - 1]``.
"""

from __future__ import annotations

import streamlit as st

from engine.data_status import SETUP_STEP_GROUPS
from models.household import Household
from views.setup._partials import (
    render_accounts_partial,
    render_assumptions_partial,
    render_household_partial,
    render_options_partial,
    render_portfolio_partial,
)

_STEP_KEY = "wizard_step"


def render(hh: Household) -> None:
    st.title("Setup -- Wizard")
    n_steps = len(SETUP_STEP_GROUPS)
    step = int(st.session_state.get(_STEP_KEY, 0))
    step = max(0, min(step, n_steps - 1))
    st.session_state[_STEP_KEY] = step
    key, label, _fields = SETUP_STEP_GROUPS[step]
    st.caption("Step " + str(step + 1) + " of " + str(n_steps) + " -- " + label)
    _render_step(hh, key)
    _render_nav(n_steps)


def _render_step(hh: Household, key: str) -> None:
    body = st.container()
    if key == "household":
        body.subheader("Filing status")
        is_single = bool(render_household_partial(hh, body, "joint"))
        col_you, col_spouse = body.columns(2)
        col_you.subheader("Me")
        render_household_partial(hh, col_you, "your")
        col_spouse.subheader("Spouse")
        if is_single:
            col_spouse.info("Single filer -- spouse fields disabled.")
        render_household_partial(hh, col_spouse, "spouse")
    elif key == "accounts":
        col_you, col_spouse = body.columns(2)
        col_you.subheader("Me")
        render_accounts_partial(hh, col_you, "your")
        col_spouse.subheader("Spouse")
        render_accounts_partial(hh, col_spouse, "spouse")
    elif key == "options":
        render_options_partial(hh, body)
    elif key == "portfolio":
        render_portfolio_partial(hh, body)
    elif key == "assumptions":
        render_assumptions_partial(hh, body)
    else:
        raise ValueError("Unknown wizard step key: " + repr(key))


def _render_nav(n_steps: int) -> None:
    step = int(st.session_state[_STEP_KEY])
    col_back, col_next = st.columns(2)
    if col_back.button("Back", disabled=step <= 0):
        st.session_state[_STEP_KEY] = max(0, step - 1)
    if col_next.button("Next", disabled=step >= n_steps - 1):
        st.session_state[_STEP_KEY] = min(n_steps - 1, step + 1)


__all__ = ["render"]
