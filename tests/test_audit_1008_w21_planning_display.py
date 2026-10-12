"""audit-1008 W21 -- planning-page display (synthetic data only).

F-60: the optimizer's "current plan" baseline (Auto-Optimizer panel and Room
This Year) dropped the plan's QCDs, and its cache signature ignored them, so
the baseline was not the plan the Conversion Plan page runs. Candidates were
scored with no QCDs either, although Apply keeps the plan's QCDs.

F-62: the Conversion Plan bracket chart stacked only options, your taxable
RMD, taxable SS and conversions, so a spouse already taking RMDs (or any other
ordinary income in combined_gross) vanished from the bars.

F-66: Sweet Spot took max(your, spouse) conversion window with no
filing-status gate; a Single filer (spouse_age zeroed by apply_single_filer)
got a 75-year window with phantom spouse ages.

F-67: the marginal-cost chart stacked tax/IRMAA/ACA/NIIT but never the
LTCG-stacking component that find_sweet_spots counts in all_in.

F-78: Dashboard, Comparator and RMD Squeeze ran their scenarios without the
manual net-investment-income input that Conversion Plan and Sweet Spot use.
"""

from dataclasses import replace

import pytest
from streamlit.testing.v1 import AppTest

from engine.scenario import run_no_conversion, run_scenario
from engine.scenario_types import ConversionPlan
from models.household import Household

# ---------------------------------------------------------------------------
# F-60
# ---------------------------------------------------------------------------


def _qcd_years(start: int, end: int, amount: float = 20_000.0) -> dict[int, float]:
    return dict.fromkeys(range(start, end), amount)


def _qcd_household() -> Household:
    # Old enough that QCDs apply from the base year.
    return Household(your_age=72, spouse_age=72, your_ira=900_000, spouse_ira=600_000)


def _probe_view_plan(module_name):
    import importlib

    import streamlit as st

    from engine.scenario_types import ConversionPlan
    from models.household import Household

    mod = importlib.import_module(module_name)
    plan = mod._current_plan()
    st.session_state["_probe_qcds"] = (dict(plan.qcds), dict(plan.spouse_qcds))
    hh = Household()
    sig = mod._signature(hh, None, plan)
    doubled = ConversionPlan(
        your_conversions=plan.your_conversions,
        spouse_conversions=plan.spouse_conversions,
        qcds={y: v * 2 for y, v in plan.qcds.items()},
        spouse_qcds={y: v * 2 for y, v in plan.spouse_qcds.items()},
    )
    st.session_state["_probe_sig_moves"] = sig != mod._signature(hh, None, doubled)


class TestF60OptimizerBaselineKeepsQcds:
    def test_baseline_cost_includes_plan_qcds(self):
        from engine.exercise_optimizer import _lifetime_total_cost, optimize_exercises

        hh = _qcd_household()
        plan = ConversionPlan(
            your_conversions={hh.base_year: 30_000.0},
            qcds=_qcd_years(hh.base_year, hh.base_year + 20),
            spouse_qcds=_qcd_years(hh.base_year, hh.base_year + 20),
        )
        result = optimize_exercises(hh, current_plan=plan, end_age=90)
        expected = _lifetime_total_cost(run_scenario(hh, plan, end_age=90).years)
        assert result.baseline_cost == pytest.approx(expected)

    def test_candidates_are_scored_with_plan_qcds(self):
        from engine.exercise_optimizer import optimize_exercises

        hh = _qcd_household()
        qcds = _qcd_years(hh.base_year, hh.base_year + 20)
        sp_qcds = _qcd_years(hh.base_year, hh.base_year + 20, 10_000.0)
        plan = ConversionPlan(qcds=qcds, spouse_qcds=sp_qcds)
        result = optimize_exercises(hh, current_plan=plan, end_age=90)
        for cand in result.candidates:
            assert cand.conversions.qcds == qcds, cand.ceiling_label
            assert cand.conversions.spouse_qcds == sp_qcds, cand.ceiling_label

    @pytest.mark.parametrize("module_name", ["views.optimizer_panel", "views.room_this_year"])
    def test_view_current_plan_and_signature_include_qcds(self, module_name):
        at = AppTest.from_function(_probe_view_plan, args=(module_name,), default_timeout=30)
        at.session_state["conv_plan_your"] = {2026: 10_000.0}
        at.session_state["conv_plan_spouse"] = {}
        at.session_state["conv_plan_qcd"] = {2030: 5_000.0}
        at.session_state["conv_plan_spouse_qcd"] = {2031: 7_000.0}
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["_probe_qcds"] == ({2030: 5_000.0}, {2031: 7_000.0})
        assert at.session_state["_probe_sig_moves"] is True


# ---------------------------------------------------------------------------
# F-62
# ---------------------------------------------------------------------------


class TestF62BracketChartSumsToGross:
    def test_segments_sum_to_combined_gross_with_spouse_rmds(self):
        from views.planner import bracket_segments

        hh = Household(your_age=60, spouse_age=75, your_ira=500_000, spouse_ira=2_500_000)
        result = run_no_conversion(hh, end_age=70)
        window = result.years[:6]
        assert any(yr.spouse_taxable_rmd > 0 for yr in window)
        segments = bracket_segments(window)
        income = [vals for name, vals, _ in segments if name != "Room (12%)"]
        for i, yr in enumerate(window):
            stacked = sum(vals[i] for vals in income)
            assert stacked == pytest.approx(yr.combined_gross, abs=1.0), yr.year

    def test_spouse_rmd_has_its_own_segment(self):
        from views.planner import bracket_segments

        hh = Household(your_age=60, spouse_age=75, your_ira=500_000, spouse_ira=2_500_000)
        window = run_no_conversion(hh, end_age=62).years
        names = [name for name, _, _ in bracket_segments(window)]
        assert "Sp Taxable RMD" in names


# ---------------------------------------------------------------------------
# F-66
# ---------------------------------------------------------------------------


def _single_household() -> Household:
    from views.setup.parameters import apply_single_filer

    hh = Household(
        your_age=66,
        spouse_age=50,
        your_ira=300_000,
        spouse_ira=500_000,
        filing_status="Single",
    )
    return apply_single_filer(hh)


class TestF66SingleFilerWindow:
    def test_window_is_your_window_for_single(self):
        from engine.sweet_spot_compute import sweet_spot_conversion_window

        hh = _single_household()
        assert sweet_spot_conversion_window(hh) == hh.your_conv_window

    def test_window_is_max_for_mfj(self):
        from engine.sweet_spot_compute import sweet_spot_conversion_window

        hh = Household(your_age=66, spouse_age=50, filing_status="MFJ")
        assert sweet_spot_conversion_window(hh) == max(hh.your_conv_window, hh.spouse_conv_window)

    def test_multi_year_summary_stops_at_your_window(self):
        from engine.sweet_spot_compute import compute_multi_year_summary

        hh = _single_household()
        rows = compute_multi_year_summary(hh)
        assert len(rows) == hh.your_conv_window

    def test_page_selector_has_no_phantom_spouse_years(self):
        def app():
            from models.household import Household
            from views.setup.parameters import apply_single_filer
            from views.sweet_spot import render

            hh = apply_single_filer(
                Household(
                    your_age=66,
                    spouse_age=50,
                    your_ira=300_000,
                    spouse_ira=500_000,
                    filing_status="Single",
                )
            )
            render(hh)

        at = AppTest.from_function(app, default_timeout=60)
        at.run()
        assert not at.exception, at.exception
        hh = _single_household()
        options = at.selectbox[0].options
        assert len(options) == hh.your_conv_window
        assert "/" not in options[0]


# ---------------------------------------------------------------------------
# F-67
# ---------------------------------------------------------------------------


class TestF67MarginalChartIncludesLtcg:
    def test_ltcg_jump_is_classified(self):
        from engine.sweet_spot_compute import ConversionResult, classify_jump

        before = ConversionResult.__new__(ConversionResult)
        after = ConversionResult.__new__(ConversionResult)
        for r, ltcg in ((before, 0.0), (after, 150.0)):
            r.irmaa_delta = 0.0
            r.aca_loss = 0.0
            r.niit_delta = 0.0
            r.ltcg_delta = ltcg
        assert "LTCG stacking" in classify_jump(before, after)

    def test_steady_ltcg_stacking_is_not_blamed_for_a_jump(self):
        from engine.sweet_spot_compute import ConversionResult, classify_jump

        points = []
        for ltcg in (0.0, 150.0, 300.0):
            r = ConversionResult.__new__(ConversionResult)
            r.irmaa_delta = 0.0
            r.aca_loss = 0.0
            r.niit_delta = 0.0
            r.ltcg_delta = ltcg
            points.append(r)
        prior, before, after = points
        assert classify_jump(before, after, prior) == "bracket change"

    def test_chart_has_ltcg_trace(self):
        def app():
            import streamlit as st

            from models.household import Household
            from views.sweet_spot import render

            st.session_state["apply_ytd_to_projection"] = False
            render(Household(brokerage_start=2_000_000))

        at = AppTest.from_function(app, default_timeout=60)
        at.run()
        assert not at.exception, at.exception
        import json

        # The first chart on the page is "Marginal All-In Cost per $1,000".
        marginal = json.loads(at.get("plotly_chart")[0].proto.spec)
        names = {t.get("name") for t in marginal.get("data", [])}
        assert {"Fed Tax", "IRMAA"} <= names
        assert "LTCG Stacking" in names


# ---------------------------------------------------------------------------
# F-78
# ---------------------------------------------------------------------------


def _render_page_with_nii(module_name):
    import importlib

    import streamlit as st

    from models.household import Household

    st.session_state["net_inv_income"] = 50_000
    importlib.import_module(module_name).render(Household())


class TestF78ManualNiiOnEveryPage:
    def test_build_scenario_threads_net_inv_income(self):
        from engine.scenario_compare import build_scenario

        hh = Household(your_age=62, spouse_age=60, your_ira=2_000_000, spouse_ira=1_333_333)
        for key in ("no_conv", "fill_12", "fill_22"):
            base = build_scenario(hh, key)
            with_nii = build_scenario(hh, key, net_inv_income=200_000)
            assert with_nii.total_niit > base.total_niit, key

    def test_run_no_conversion_threads_net_inv_income(self):
        hh = Household(your_age=62, spouse_age=60, your_ira=2_000_000, spouse_ira=1_333_333)
        assert (
            run_no_conversion(hh, net_inv_income=200_000).total_niit
            > run_no_conversion(hh).total_niit
        )

    @pytest.mark.parametrize(
        "module_name", ["views.dashboard", "views.comparator", "views.rmd_squeeze"]
    )
    def test_pages_pass_session_nii(self, module_name, monkeypatch):
        """Each page must hand the session's net_inv_income to the engine."""
        import importlib

        mod = importlib.import_module(module_name)
        seen: list[float] = []

        def spy_factory(real):
            def spy(*args, **kwargs):
                seen.append(kwargs.get("net_inv_income", 0.0))
                return real(*args, **kwargs)

            return spy

        for name in ("run_scenario", "run_no_conversion", "build_scenario"):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, spy_factory(getattr(mod, name)))

        at = AppTest.from_function(_render_page_with_nii, args=(module_name,), default_timeout=60)
        at.run()
        assert not at.exception, at.exception
        assert seen, f"{module_name} ran no scenario"
        assert all(v == 50_000 for v in seen), seen


def test_single_household_helper_zeroes_spouse():
    hh = _single_household()
    assert hh.spouse_age == 0
    assert replace(hh).filing_status == "Single"
