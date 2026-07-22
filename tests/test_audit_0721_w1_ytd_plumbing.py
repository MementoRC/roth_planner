"""Regression tests for audit-0721 Wave 1 (5 findings): YTD actuals plumbing.

C7 — engine/scenario.py available_income() omitted ira_distributions_ytd
     beyond the forecast RMD (voluntary/pre-RMD-age withdrawals).
C10 — NOT REPRODUCED (see TestC10NotReproduced below).
C26 — views/planner.py's run_scenario() call omitted ytd=, making the
     "Apply YTD to projections" toggle inert on this page.
C27 — views/dashboard.py's run_no_conversion()/run_scenario() calls omitted
     ytd=, same defect as C26.
C31 — views/planner.py's post-edit session_state comparison masked a
     clamp/zero correction that happened to equal the already-stored value,
     so the data_editor never re-rendered to show the corrected value.
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_scenario
from models.household import Household
from models.ytd_income import YTDSnapshot


class TestC7IraDistributionsYtdInAvailableIncome:
    """C7 — ira_distributions_ytd beyond the forecast RMD must reach
    available_income, mirroring the existing wages/NEC/interest/STCG add-back
    (audit-0720 F4)."""

    def test_c7_available_income_includes_ira_distributions_ytd_excess(self) -> None:
        # Given: a household with $0 IRA balances (no forecast RMD this year,
        # regardless of age) and no other income sources, whose only YTD
        # activity is a $50K non-conversion IRA distribution (e.g. a
        # voluntary/pre-RMD-age withdrawal).
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=30_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, ira_distributions_ytd=50_000.0)
        plan = ConversionPlan()

        # When: the scenario is projected for the base year only.
        result = run_scenario(hh, plan, "c7_ira_dist", end_age=61, ytd=ytd)
        yr = result.years[0]

        # Then: the distribution is already taxed via combined_gross ->
        # federal_tax_amt (pre-existing behavior), and with no forecast RMD to
        # absorb any of it, the FULL $50K must be added back as spendable
        # cash inflow.
        expected_available_income = 50_000.0 - yr.federal_tax_amt
        expected_income_needed = max(hh.living_expenses - expected_available_income, 0.0)
        expected_excess_rmd = max(expected_available_income - hh.living_expenses, 0.0)

        # Pre-fix bug: available_income never added ira_distributions_ytd back,
        # so income_needed was phantom-overstated (or excess_rmd understated).
        assert yr.income_needed == pytest.approx(expected_income_needed)
        assert yr.excess_rmd == pytest.approx(expected_excess_rmd)
        assert expected_income_needed == 0.0

    def test_c7_does_not_double_count_the_rmd_absorbed_portion(self) -> None:
        # Given: a household with an RMD-age owner whose forecast taxable RMD
        # this year exceeds the YTD distributions taken so far -- the entire
        # YTD distribution is absorbed by after_tax_rmd's restore of
        # _rmd_ytd_reduction, so C7's excess add-back must be exactly $0 here
        # (guards against double-counting the RMD-absorbed portion).
        hh = Household(
            your_age=76,
            spouse_age=70,
            base_year=2026,
            your_ira=1_700_000.0,
            spouse_ira=0.0,
            living_expenses=30_000.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
        )
        ytd_small = YTDSnapshot(tax_year=2026, ira_distributions_ytd=10_000.0)
        ytd_none = YTDSnapshot(tax_year=2026)
        plan = ConversionPlan()

        result_small = run_scenario(hh, plan, "c7_small_dist", end_age=76, ytd=ytd_small)
        result_none = run_scenario(hh, plan, "c7_no_dist", end_age=76, ytd=ytd_none)

        yr_small = result_small.years[0]
        yr_none = result_none.years[0]

        assert yr_small.taxable_rmd > 10_000.0, (
            "precondition: forecast RMD must exceed the YTD distribution taken"
        )
        # available_income (and therefore income_needed/excess_rmd) must be
        # identical whether or not the $10K was reported as already taken --
        # it was already going to be withdrawn as part of the RMD either way.
        assert yr_small.income_needed == pytest.approx(yr_none.income_needed)
        assert yr_small.excess_rmd == pytest.approx(yr_none.excess_rmd)


class TestC10NotReproduced:
    """C10 — audit hypothesized estimate_ltcg_eligible()'s LTCG stacking base
    omits YTD ordinary income. NOT REPRODUCED at current HEAD: this exact
    defect (MU8-F1 lineage) was already fixed by prior work. Evidence:
    - engine/sweet_spot_compute.py:298-311 (`ytd_ordinary` folded into
      `base_gross`/`ordinary_addl`, which feeds `taxable_inc` -- the actual
      LTCG stack-walk start passed to `_ltcg_stack_tax` at line 520).
    - tests/test_sweet_spot_scenario_parity.py::TestMU8F1LtcgStackRegression
      already locks this exact behavior (ytd wages shifting the LTCG-stack
      start above the 0%->15% threshold).
    This guard independently re-confirms parity with engine.scenario's own
    stack-walk start (yr.taxable_income, which folds in YTD ordinary via
    combined_gross) using a fresh Household/YTDSnapshot, without touching
    estimate_ltcg_eligible() (which correctly returns only the LTCG/qual-div
    *amount*, not the stack *start* -- the stack start is base_gross/
    ordinary_addl, computed separately in base_income_for_year)."""

    def test_ordinary_addl_matches_scenarios_taxable_income_ytd_fold(self) -> None:
        from engine.sweet_spot_compute import base_income_for_year

        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=30_000.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
        )
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=40_000.0)
        plan = ConversionPlan()

        oracle = run_scenario(hh, plan, "c10_guard", end_age=61, ytd=ytd).years[0]
        base = base_income_for_year(hh, hh.base_year, ytd=ytd)

        # base_gross (which sets the LTCG stack-walk start via taxable_inc)
        # already includes the $40K YTD wages, matching scenario's
        # combined_gross/taxable_income fold -- no fix needed.
        assert base.base_gross == pytest.approx(oracle.combined_gross)
        assert base.ordinary_addl == pytest.approx(40_000.0)


class TestC26PlannerThreadsYtd:
    """C26 — views/planner.py's run_scenario() call must pass ytd= so the
    "Apply YTD to projections" toggle (session_state.apply_ytd_to_projection
    + ytd_snapshot, set on the YTD Income page) is not inert on this page."""

    def test_planner_run_scenario_receives_ytd_when_toggle_on(self) -> None:
        from streamlit.testing.v1 import AppTest

        def _render() -> None:
            import streamlit as st

            import views.planner as planner_mod
            from models.household import Household
            from models.ytd_income import YTDSnapshot

            hh = Household(
                your_age=61,
                spouse_age=55,
                base_year=2026,
                your_ira=1_700_000.0,
                spouse_ira=1_500_000.0,
            )
            ytd = YTDSnapshot(tax_year=2026, wages_ytd=25_000.0)
            st.session_state["apply_ytd_to_projection"] = True
            st.session_state["ytd_snapshot"] = ytd

            captured: dict = {}
            orig_run_scenario = planner_mod.run_scenario

            def _spy(*args: object, **kwargs: object) -> object:
                captured["ytd"] = kwargs.get("ytd")
                return orig_run_scenario(*args, **kwargs)

            planner_mod.run_scenario = _spy
            st.session_state["_captured_ytd_wages"] = None
            planner_mod.render(hh)
            wt = captured.get("ytd")
            st.session_state["_captured_ytd_wages"] = wt.wages_ytd if wt is not None else None

        at = AppTest.from_function(_render)
        at.run()
        assert not at.exception, f"planner page crashed: {at.exception}"
        assert at.session_state["_captured_ytd_wages"] == pytest.approx(25_000.0), (
            "run_scenario() was not called with ytd= while the toggle is on"
        )


class TestC26FollowUpPlannerNoConversionThreadsYtd:
    """C26 follow-up (audit-0721) -- views/planner.py's run_no_conversion()
    call (IRA trajectory chart baseline) must also pass ytd=, mirroring the
    "Custom" run_scenario() fix above. run_no_conversion is imported locally
    inside render() (`from engine.scenario import run_no_conversion`), so the
    spy must patch engine.scenario.run_no_conversion itself rather than a
    views.planner module attribute."""

    def test_planner_run_no_conversion_receives_ytd_when_toggle_on(self) -> None:
        from streamlit.testing.v1 import AppTest

        def _render() -> None:
            import streamlit as st

            import engine.scenario as scenario_mod
            import views.planner as planner_mod
            from models.household import Household
            from models.ytd_income import YTDSnapshot

            hh = Household(
                your_age=61,
                spouse_age=55,
                base_year=2026,
                your_ira=1_700_000.0,
                spouse_ira=1_500_000.0,
            )
            ytd = YTDSnapshot(tax_year=2026, wages_ytd=25_000.0)
            st.session_state["apply_ytd_to_projection"] = True
            st.session_state["ytd_snapshot"] = ytd

            captured: dict = {}
            orig_run_no_conversion = scenario_mod.run_no_conversion

            def _spy(*args: object, **kwargs: object) -> object:
                captured["ytd"] = kwargs.get("ytd")
                return orig_run_no_conversion(*args, **kwargs)

            scenario_mod.run_no_conversion = _spy
            st.session_state["_captured_ytd_wages"] = None
            try:
                planner_mod.render(hh)
            finally:
                scenario_mod.run_no_conversion = orig_run_no_conversion
            wt = captured.get("ytd")
            st.session_state["_captured_ytd_wages"] = wt.wages_ytd if wt is not None else None

        at = AppTest.from_function(_render)
        at.run()
        assert not at.exception, f"planner page crashed: {at.exception}"
        assert at.session_state["_captured_ytd_wages"] == pytest.approx(25_000.0), (
            "run_no_conversion() was not called with ytd= while the toggle is on"
        )


class TestC27DashboardThreadsYtd:
    """C27 — views/dashboard.py's run_no_conversion()/run_scenario() calls
    must pass ytd=, matching C26's fix on the Planner page."""

    def test_dashboard_scenarios_receive_ytd_when_toggle_on(self) -> None:
        from streamlit.testing.v1 import AppTest

        def _render() -> None:
            import streamlit as st

            import views.dashboard as dashboard_mod
            from models.household import Household
            from models.ytd_income import YTDSnapshot

            hh = Household(
                your_age=61,
                spouse_age=55,
                base_year=2026,
                your_ira=1_700_000.0,
                spouse_ira=1_500_000.0,
            )
            ytd = YTDSnapshot(tax_year=2026, wages_ytd=25_000.0)
            st.session_state["apply_ytd_to_projection"] = True
            st.session_state["ytd_snapshot"] = ytd

            captured: dict = {}
            orig_run_scenario = dashboard_mod.run_scenario
            orig_run_no_conversion = dashboard_mod.run_no_conversion

            def _spy_scenario(*args: object, **kwargs: object) -> object:
                captured["run_scenario_ytd"] = kwargs.get("ytd")
                return orig_run_scenario(*args, **kwargs)

            def _spy_no_conversion(*args: object, **kwargs: object) -> object:
                captured["run_no_conversion_ytd"] = kwargs.get("ytd")
                return orig_run_no_conversion(*args, **kwargs)

            dashboard_mod.run_scenario = _spy_scenario
            dashboard_mod.run_no_conversion = _spy_no_conversion
            dashboard_mod.render(hh)

            rs = captured.get("run_scenario_ytd")
            rnc = captured.get("run_no_conversion_ytd")
            st.session_state["_run_scenario_wages"] = rs.wages_ytd if rs is not None else None
            st.session_state["_run_no_conversion_wages"] = (
                rnc.wages_ytd if rnc is not None else None
            )

        at = AppTest.from_function(_render)
        at.run()
        assert not at.exception, f"dashboard page crashed: {at.exception}"
        assert at.session_state["_run_scenario_wages"] == pytest.approx(25_000.0), (
            "run_scenario() was not called with ytd= while the toggle is on"
        )
        assert at.session_state["_run_no_conversion_wages"] == pytest.approx(25_000.0), (
            "run_no_conversion() was not called with ytd= while the toggle is on"
        )


class TestC31GridRefreshNotMaskedByEqualState:
    """C31 — should_refresh_grid() must fire whenever edit_warnings is
    non-empty, independent of whether the resulting dict happens to equal
    what was already in session_state. Pure-function extraction of the
    decision previously buried inside the state_changed branch, verified
    directly (Streamlit-state re-render itself is not unit-testable per
    repo convention -- see views/planner.py's render() usage)."""

    def test_refreshes_on_warnings_even_when_state_unchanged(self) -> None:
        from views.planner import should_refresh_grid

        # Pre-fix bug: a clamp landing back on the already-stored value
        # (state_changed=False) suppressed the grid-key clear + rerun even
        # though edit_warnings fired.
        assert should_refresh_grid(False, ["2030: your conversion clamped..."]) is True

    def test_refreshes_on_state_change_with_no_warnings(self) -> None:
        from views.planner import should_refresh_grid

        assert should_refresh_grid(True, []) is True

    def test_no_refresh_when_nothing_changed_and_no_warnings(self) -> None:
        from views.planner import should_refresh_grid

        assert should_refresh_grid(False, []) is False
