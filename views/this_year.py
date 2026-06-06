"""v2 nav preview — combines YTD Income & Headroom + Roth Eligibility into one journey stop."""

import streamlit as st

from models.household import Household
from views import roth_eligibility, ytd_income


def render(hh: Household) -> None:
    """Render the This Year journey stop."""
    st.title("📅 This Year")
    st.caption(
        "Current-year operational view: track realized income against headroom thresholds"
        " and confirm Roth contribution eligibility."
    )
    tab_ytd, tab_roth = st.tabs(["📊 YTD Income & Headroom", "✅ Roth Eligibility"])
    with tab_ytd:
        ytd_income.render(hh)
    with tab_roth:
        roth_eligibility.render(hh)
