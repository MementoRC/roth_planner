"""v2 nav preview — combines RMD Squeeze + Asset Location into one journey stop."""

import streamlit as st

from models.household import Household
from views import asset_location, rmd_squeeze


def render(hh: Household) -> None:
    """Render the Pressure Test journey stop."""
    st.title("🔮 Pressure Test")
    st.caption(
        "End-state stress tests: forced-RMD pressure age 75+ and which buckets to convert"
        " from first."
    )
    tab_rmd, tab_loc = st.tabs(["⚠️ RMD Squeeze", "📍 Asset Location"])
    with tab_rmd:
        rmd_squeeze.render(hh)
    with tab_loc:
        asset_location.render(hh)
