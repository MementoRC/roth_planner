"""Central CSS injection point for the app.

This module owns the ONLY styling in the repo (there was previously zero CSS,
no ``unsafe_allow_html`` usage, and no ``.streamlit/config.toml`` — see the
2026-09 "make the Setup tabs more visible" task). All app-wide cosmetic CSS
should be added HERE, as one ``<style>`` block emitted via
``st.markdown(..., unsafe_allow_html=True)``, rather than scattered ad-hoc
``unsafe_allow_html`` calls in individual views.

IMPORTANT — these selectors target Streamlit's INTERNAL DOM
(``data-testid``/``data-baseweb`` attributes emitted by Streamlit's own
React components), which are undocumented implementation details, not a
public API. A future Streamlit upgrade can rename or restructure this markup
at any time. If that happens, this CSS silently stops applying -- it does
NOT raise an exception or fail a test, the page just quietly reverts to
default Streamlit styling. Treat everything in this module as COSMETIC ONLY;
nothing in the app's behavior may ever depend on it taking effect.
"""

from __future__ import annotations

import streamlit as st

_APP_CSS = """
<style>
/* Make the Streamlit tab strip (used by Setup's tabs, among others) more
   readable: a moderately larger label and a visible contour separating the
   tab strip from the page content below it. Colors intentionally avoid any
   hardcoded background/text hex so this works on both light and dark
   Streamlit themes -- borders/weights only, no fixed palette. */
[data-testid="stTabs"] {
    border-bottom: 1px solid currentColor;
}

[data-testid="stTabs"] button[data-baseweb="tab"] {
    font-size: 1.1rem;
    font-weight: 600;
}

[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] {
    border-bottom: 3px solid currentColor;
}
</style>
"""


def inject_app_css() -> None:
    """Emit the app's single global ``<style>`` block.

    Call once, early in ``app.py``, immediately after ``st.set_page_config``
    and before any page dispatch. Safe to call more than once (Streamlit
    will simply render the same style tag again), but the app only calls it
    once by design.
    """
    st.markdown(_APP_CSS, unsafe_allow_html=True)


__all__ = ["inject_app_css"]
