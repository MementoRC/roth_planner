"""Regression tests for audit-0721 W3 findings C28, C29, C30.

C28: views/rmd_squeeze.py run_no_conversion/run_scenario calls omitted
     ytd=, so the RMD Squeeze base-year tax/IRMAA/NIIT ignored realized
     YTD income.
C29: engine/scenario_compare.py build_scenario never accepted/forwarded a
     ytd parameter, so no Comparator scenario reflected YTD actuals.
C30: build_scenario's 'custom' branch dropped spouse_qcds when
     reconstructing the ConversionPlan from session_state.

View-layer wiring is verified via streamlit.testing.v1.AppTest.from_function
(mirrors tests/test_command_center_w4_nii_shared_key.py /
tests/test_auto_optimizer_view.py — the wrapped function must be fully
self-contained, all imports/object construction inside its body) combined
with unittest.mock.patch.object(..., wraps=<real fn>) spies on the
module-level call sites, since global name lookups happen at call time.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from engine.scenario_compare import build_scenario
from models.household import Household
from models.ytd_income import YTDSnapshot

# ---------------------------------------------------------------------------
# C28 — views/rmd_squeeze.py threads ytd into run_no_conversion/run_scenario
# ---------------------------------------------------------------------------


def _render_rmd_squeeze_with_ytd() -> None:
    import streamlit as st

    from models.household import Household
    from models.ytd_income import YTDSnapshot
    from views.rmd_squeeze import render

    hh = Household(your_age=61, spouse_age=55, base_year=2026)
    st.session_state["apply_ytd_to_projection"] = True
    st.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026, wages_ytd=50_000.0)
    render(hh)


class TestC28RmdSqueezeThreadsYtd:
    def test_run_no_conversion_and_run_scenario_receive_ytd(self) -> None:
        import views.rmd_squeeze as rmd_squeeze_mod
        from engine.scenario import run_no_conversion as real_run_no_conversion
        from engine.scenario import run_scenario as real_run_scenario

        with (
            patch.object(
                rmd_squeeze_mod, "run_no_conversion", wraps=real_run_no_conversion
            ) as mock_no_conv,
            patch.object(rmd_squeeze_mod, "run_scenario", wraps=real_run_scenario) as mock_scenario,
        ):
            at = AppTest.from_function(_render_rmd_squeeze_with_ytd)
            at.run()

        assert not at.exception

        # Line ~73: baseline no_conv call (always executed).
        assert mock_no_conv.call_args.kwargs.get("ytd") is not None
        assert mock_no_conv.call_args.kwargs["ytd"].wages_ytd == pytest.approx(50_000.0)

        # Line ~102: with_conv call in the default (show_qcd=False) branch.
        assert mock_scenario.call_args.kwargs.get("ytd") is not None
        assert mock_scenario.call_args.kwargs["ytd"].wages_ytd == pytest.approx(50_000.0)


# ---------------------------------------------------------------------------
# C29 — engine.scenario_compare.build_scenario forwards ytd for every preset
# ---------------------------------------------------------------------------


class TestC29BuildScenarioThreadsYtd:
    @pytest.mark.parametrize("key", ["no_conv", "fill_12", "fill_12_bf", "fill_22", "irmaa_safe"])
    def test_preset_scenarios_reflect_base_year_ytd_wages(self, key: str) -> None:
        hh = Household(your_age=61, spouse_age=55, base_year=2026)
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=100_000.0)

        result_no_ytd = build_scenario(hh, key)
        result_with_ytd = build_scenario(hh, key, ytd=ytd)

        assert result_no_ytd.years[0].ytd_wages == 0
        assert result_with_ytd.years[0].ytd_wages == pytest.approx(100_000.0)

    def test_default_ytd_is_none_existing_callers_unaffected(self) -> None:
        """build_scenario(hh, key) without ytd= must behave exactly as before."""
        hh = Household(your_age=61, spouse_age=55, base_year=2026)
        result = build_scenario(hh, "fill_12")
        assert result.years[0].ytd_wages == 0


def _render_comparator_with_ytd() -> None:
    import streamlit as st

    from models.household import Household
    from models.ytd_income import YTDSnapshot
    from views.comparator import render

    hh = Household(your_age=61, spouse_age=55, base_year=2026)
    st.session_state["apply_ytd_to_projection"] = True
    st.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026, wages_ytd=75_000.0)
    render(hh)


class TestC29ComparatorViewPassesYtd:
    def test_view_forwards_session_ytd_to_build_scenario(self) -> None:
        import views.comparator as comparator_mod
        from engine.scenario_compare import build_scenario as real_build_scenario

        with patch.object(
            comparator_mod, "build_scenario", wraps=real_build_scenario
        ) as mock_build:
            at = AppTest.from_function(_render_comparator_with_ytd)
            at.run()

        assert not at.exception
        # default_selected == 3 presets ("No Conversion", "Fill to 12%",
        # "Fill 12% + Bracket Fill") -> 3 build_scenario calls, all with ytd.
        assert mock_build.call_count == 3
        for call in mock_build.call_args_list:
            assert call.kwargs.get("ytd") is not None
            assert call.kwargs["ytd"].wages_ytd == pytest.approx(75_000.0)


# ---------------------------------------------------------------------------
# C30 — build_scenario 'custom' branch must include spouse_qcds
# ---------------------------------------------------------------------------


def _build_custom_scenario_with_spouse_qcd() -> None:
    import streamlit as st

    from engine.scenario_compare import build_scenario
    from models.household import Household
    from models.ytd_income import YTDSnapshot

    st.session_state["conv_plan_your"] = {2026: 20_000.0}
    st.session_state["conv_plan_spouse"] = {2026: 10_000.0}
    st.session_state["conv_plan_qcd"] = {}
    st.session_state["conv_plan_spouse_qcd"] = {2026: 15_000.0}

    hh = Household(your_age=76, spouse_age=71, base_year=2026)
    ytd = YTDSnapshot(tax_year=2026, wages_ytd=40_000.0)
    result = build_scenario(hh, "custom", ytd=ytd)

    # Stash results on session_state so the outer test can inspect them —
    # AppTest.from_function re-execs this body as a standalone script.
    st.session_state["_result_spouse_qcd_2026"] = result.plan.spouse_qcds.get(2026, 0.0)
    st.session_state["_result_ytd_wages"] = result.years[0].ytd_wages


class TestC30CustomBranchIncludesSpouseQcds:
    def test_custom_plan_carries_spouse_qcds_and_ytd(self) -> None:
        at = AppTest.from_function(_build_custom_scenario_with_spouse_qcd)
        at.run()

        assert not at.exception
        assert at.session_state["_result_spouse_qcd_2026"] == pytest.approx(15_000.0)
        assert at.session_state["_result_ytd_wages"] == pytest.approx(40_000.0)

    def test_custom_branch_without_session_state_still_defaults_empty(self) -> None:
        """No spurious spouse_qcds when the Planner grid never set the key
        (mirrors qcds' existing dict(...get(..., {})) fallback)."""

        def _render() -> None:
            from engine.scenario_compare import build_scenario
            from models.household import Household

            hh = Household(your_age=61, spouse_age=55, base_year=2026)
            result = build_scenario(hh, "custom")
            import streamlit as st

            st.session_state["_spouse_qcds"] = dict(result.plan.spouse_qcds)

        at = AppTest.from_function(_render)
        at.run()

        assert not at.exception
        assert at.session_state["_spouse_qcds"] == {}
