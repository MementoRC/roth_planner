"""Central CSS injection point for the app.

This module owns the app's custom CSS. The palette itself is not here: it
lives in ``.streamlit/config.toml`` (``[theme.light]`` / ``[theme.dark]``, which
follow the viewer's system setting; ``deploy/build_stlite.py`` bundles the same
file). The CSS below stays theme-neutral (``currentColor``, no hex). All app-wide cosmetic CSS
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

/* Redesign PR 1: sidebar nav buttons read as a list, not as centred buttons. */
[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"],
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
    justify-content: flex-start;
    text-align: left;
}
/* The button's inner wrapper centres the label on its own. */
[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"] > div,
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] > div {
    justify-content: flex-start;
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


def page_title_text(page_id: str, detail: str | None = None) -> str:
    """Return the page title: the registry label (icon + name), plus an optional detail.

    The emoji comes from ``views.navigation.PAGES`` so a page's title and its
    nav button can never drift apart.
    """
    from views.navigation import label_of  # lazy: navigation imports view helpers

    label = label_of(page_id)
    return f"{label} — {detail}" if detail else label


def render_page_header(page_id: str, detail: str | None = None) -> None:
    """Emit the page's ``st.title`` (see ``page_title_text``)."""
    st.title(page_title_text(page_id, detail))


__all__ = ["inject_app_css", "page_title_text", "render_page_header"]
