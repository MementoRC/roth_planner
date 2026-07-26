"""Wizard shell -- step-by-step Setup flow over the shared partials.

Reuses the standard shell contract render(hh) -> None. The current step index
lives in st.session_state["wizard_step"]; the shell itself is stateless.
Real per-step content + navigation are added in the following task.
"""

from __future__ import annotations

import streamlit as st

from engine.data_status import SETUP_STEP_GROUPS
from models.household import Household

_STEP_KEY = "wizard_step"


def render(hh: Household) -> None:
    st.title("Setup -- Wizard")
    n_steps = len(SETUP_STEP_GROUPS)
    step = int(st.session_state.get(_STEP_KEY, 0))
    step = max(0, min(step, n_steps - 1))
    st.session_state[_STEP_KEY] = step
    key, label, _fields = SETUP_STEP_GROUPS[step]
    st.caption(f"Step {step + 1} of {n_steps} -- {label}")
    _render_step(hh, key)


def _render_step(hh: Household, key: str) -> None:
    _ = hh
    st.info(f"Step {key!r} content is implemented in the next task.")
