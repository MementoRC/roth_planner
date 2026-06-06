"""v2 nav preview — combines Sweet Spot Finder + ACA + IRMAA Explorer into one journey stop."""

import streamlit as st

from models.household import Household
from views import aca_irmaa, sweet_spot


def render(hh: Household) -> None:
    """Render the Know the Cliffs journey stop."""
    st.title("🚧 Know the Cliffs")
    st.caption(
        "Educational threshold tools: where conversion dollars get expensive and where"
        " Medicare / ACA hit cliffs."
    )
    tab_sweet, tab_aca = st.tabs(["🎯 Sweet Spot Finder", "🏥 ACA + IRMAA Explorer"])
    with tab_sweet:
        sweet_spot.render(hh)
    with tab_aca:
        aca_irmaa.render(hh)
