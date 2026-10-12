"""Session keys of the Conversion Plan grid, shared by every writer of ``conv_plan_*``.

Anything that replaces the plan (Auto-Fill, Clear All, the optimizer's Apply)
must drop these keys, or a stale editor delta masks the new plan.
"""

import streamlit as st

GRID_KEY = "conv_grid_editor"
_WIDGET_PREFIXES = ("yc_", "sc_", "qcd_", "sp_qcd_")  # pre-grid per-year widgets


def clear_conversion_widgets() -> None:
    """Drop the grid editor and old per-year widget keys so the grid re-seeds from ``conv_plan_*``."""
    for key in list(st.session_state):
        if key == GRID_KEY or key.startswith(_WIDGET_PREFIXES):
            del st.session_state[key]
