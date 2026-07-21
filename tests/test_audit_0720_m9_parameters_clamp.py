"""Regression test for audit-0720 finding M9.

views/setup/parameters.py's Joint sub-tab (render_parameters_tab) passes four
session_state-derived values straight into their widgets without the
``_clamp()`` wrapper that ``medicare_part_b_base_monthly`` already uses. A
persisted/uploaded session_state value outside a widget's ``[min, max]``
crashes the entire Setup page with StreamlitValueAboveMaxError. Uses
streamlit.testing.v1.AppTest.from_function (mirrors tests/test_command_center_view.py):
AppTest execs ONLY the wrapped function's own source in an isolated
namespace, so every import/object-construction must live inside each
closure body (no references to module-level sibling helpers).
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render_joint_tab_bad_cpi() -> None:
    import streamlit as st

    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from views.setup.parameters import render_parameters_tab

    # Minimal seed mirroring app.py's _seed_session_state fallback defaults --
    # render_parameters_tab reads several keys via bare attribute access
    # (st.session_state.your_ira etc.) so they must pre-exist.
    st.session_state.setdefault("your_ira", 1_700_000)
    st.session_state.setdefault("your_roth", 0)
    st.session_state.setdefault("your_age", 61)
    st.session_state.setdefault("your_has_workplace_plan", False)
    st.session_state.setdefault("your_ss_fra", 3000)
    st.session_state.setdefault("your_aca", False)
    st.session_state.setdefault("spouse_ira", 1_500_000)
    st.session_state.setdefault("spouse_roth", 0)
    st.session_state.setdefault("spouse_age", 55)
    st.session_state.setdefault("spouse_has_workplace_plan", False)
    st.session_state.setdefault("spouse_ss_fra", 2500)
    st.session_state.setdefault("spouse_aca", False)
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", 60_000)
    st.session_state.setdefault("txn_price", 200)
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state["cpi_assumption"] = 0.08  # above the 0.06 max
    st.session_state["_suppress_snapshot_autoload"] = True

    hh = Household(your_age=61, spouse_age=55, base_year=2026, filing_status="MFJ")
    render_parameters_tab(hh)


def _render_joint_tab_bad_growth_rate() -> None:
    import streamlit as st

    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from views.setup.parameters import render_parameters_tab

    st.session_state.setdefault("your_ira", 1_700_000)
    st.session_state.setdefault("your_roth", 0)
    st.session_state.setdefault("your_age", 61)
    st.session_state.setdefault("your_has_workplace_plan", False)
    st.session_state.setdefault("your_ss_fra", 3000)
    st.session_state.setdefault("your_aca", False)
    st.session_state.setdefault("spouse_ira", 1_500_000)
    st.session_state.setdefault("spouse_roth", 0)
    st.session_state.setdefault("spouse_age", 55)
    st.session_state.setdefault("spouse_has_workplace_plan", False)
    st.session_state.setdefault("spouse_ss_fra", 2500)
    st.session_state.setdefault("spouse_aca", False)
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state["growth_rate"] = 15.0  # above the 12.0 max
    st.session_state.setdefault("living_expenses", 60_000)
    st.session_state.setdefault("txn_price", 200)
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state["_suppress_snapshot_autoload"] = True

    hh = Household(your_age=61, spouse_age=55, base_year=2026, filing_status="MFJ")
    render_parameters_tab(hh)


def test_cpi_assumption_above_max_no_longer_crashes_setup_page() -> None:
    at = AppTest.from_function(_render_joint_tab_bad_cpi)
    at.run()
    assert not at.exception, f"Setup page crashed: {at.exception}"
    # Widget shows the clamped value, not the raw out-of-range 0.08.
    assert at.session_state["cpi_assumption"] == 0.06


def test_growth_rate_above_max_no_longer_crashes_setup_page() -> None:
    at = AppTest.from_function(_render_joint_tab_bad_growth_rate)
    at.run()
    assert not at.exception, f"Setup page crashed: {at.exception}"
    assert at.session_state["growth_rate"] == 12.0
