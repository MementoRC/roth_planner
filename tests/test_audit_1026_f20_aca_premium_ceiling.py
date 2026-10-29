"""Audit-1026 F-20: ACA benchmark and advance APTC were cut to $60k on render."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render_high_aca_values() -> None:
    import streamlit as st

    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from views.setup._partials import render_assumptions_partial

    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", 60_000)
    st.session_state.setdefault("aca_benchmark_premium_annual", 75_000.0)
    st.session_state.setdefault("advance_aptc_annual", 75_000)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state["_suppress_snapshot_autoload"] = True

    hh = Household(your_age=60, spouse_age=54, base_year=2026, filing_status="MFJ")
    render_assumptions_partial(hh, st)


def test_stored_values_above_60k_are_not_cut_on_render() -> None:
    at = AppTest.from_function(_render_high_aca_values, default_timeout=30)
    at.run()
    assert not at.exception, f"render crashed: {at.exception}"
    assert at.session_state["aca_benchmark_premium_annual"] == 75_000.0
    assert at.session_state["advance_aptc_annual"] == 75_000
