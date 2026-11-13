"""views/plan_source.py: the planner's plan/YTD/NII building, shared with Income Control."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_planner_state_present_builds_plan_from_session_keys() -> None:
    def _app() -> None:
        import streamlit as st

        from models.household import Household
        from views.plan_source import active_plan

        st.session_state["conv_plan_your"] = {2027: 50_000.0}
        st.session_state["conv_plan_spouse"] = {2028: 10_000.0}
        st.session_state["conv_plan_qcd"] = {2030: 5_000.0}
        st.session_state["conv_plan_spouse_qcd"] = {}
        st.session_state["net_inv_income"] = 1234
        plan, ytd, nii, label = active_plan(Household())
        st.session_state["out"] = (
            plan.your_conversions,
            plan.spouse_conversions,
            plan.qcds,
            plan.spouse_qcds,
            ytd,
            nii,
            label,
        )

    at = AppTest.from_function(_app, default_timeout=60)
    at.run()
    assert not at.exception
    assert at.session_state["out"] == (
        {2027: 50_000.0},
        {2028: 10_000.0},
        {2030: 5_000.0},
        {},
        None,
        1234,
        "Conversion Plan",
    )


def test_missing_planner_state_falls_back_to_fill_12() -> None:
    def _app() -> None:
        import streamlit as st

        from models.household import Household
        from views.plan_source import active_plan

        plan, _ytd, _nii, label = active_plan(Household())
        st.session_state["label"] = label
        st.session_state["plan_type"] = type(plan).__name__

    at = AppTest.from_function(_app, default_timeout=60)
    at.run()
    assert not at.exception
    assert at.session_state["label"] == "Fill 12%"
    assert at.session_state["plan_type"] == "ConversionPlan"


def test_ytd_is_used_only_when_the_toggle_is_on() -> None:
    def _app() -> None:
        import streamlit as st

        from views.plan_source import scenario_ytd_and_nii

        st.session_state["ytd_snapshot"] = "SNAP"
        st.session_state["apply_ytd_to_projection"] = False
        off = scenario_ytd_and_nii()
        st.session_state["apply_ytd_to_projection"] = True
        on = scenario_ytd_and_nii()
        st.session_state["out"] = (off[0], on[0])

    at = AppTest.from_function(_app, default_timeout=60)
    at.run()
    assert not at.exception
    assert at.session_state["out"] == (None, "SNAP")


def test_missing_nii_uses_scalar_default_and_ytd_toggle_on() -> None:
    def _app() -> None:
        import streamlit as st

        from config.scalar_registry import scalar_default
        from models.household import Household
        from views.plan_source import active_plan

        st.session_state["ytd_snapshot"] = "SNAP"
        st.session_state["apply_ytd_to_projection"] = True
        st.session_state["conv_plan_your"] = {}
        st.session_state["conv_plan_spouse"] = {}
        st.session_state["conv_plan_qcd"] = {}
        st.session_state["conv_plan_spouse_qcd"] = {}
        _plan, ytd, nii, _label = active_plan(Household())
        st.session_state["out"] = (ytd, nii, int(scalar_default("net_inv_income")))

    at = AppTest.from_function(_app, default_timeout=60)
    at.run()
    assert not at.exception
    ytd, nii, expected = at.session_state["out"]
    assert ytd == "SNAP"
    assert nii == expected


def test_fallback_passes_computed_ytd_and_nii_to_auto_fill_12() -> None:
    def _app() -> None:
        import streamlit as st

        import views.plan_source as ps
        from engine.scenario_types import ConversionPlan
        from models.household import Household

        calls: list[tuple] = []

        def _spy(hh, ytd, nii):  # type: ignore[no-untyped-def]
            calls.append((ytd, nii))
            return ConversionPlan()

        st.session_state["ytd_snapshot"] = "SNAP"
        st.session_state["apply_ytd_to_projection"] = True
        st.session_state["net_inv_income"] = 4321
        original = ps.auto_fill_12
        ps.auto_fill_12 = _spy
        try:
            _plan, _ytd, _nii, label = ps.active_plan(Household())
        finally:
            ps.auto_fill_12 = original
        st.session_state["out"] = (calls, label)

    at = AppTest.from_function(_app, default_timeout=60)
    at.run()
    assert not at.exception
    assert at.session_state["out"] == ([("SNAP", 4321)], "Fill 12%")


def test_partially_seeded_planner_state_falls_back_to_fill_12() -> None:
    def _app() -> None:
        import streamlit as st

        from models.household import Household
        from views.plan_source import active_plan

        st.session_state["conv_plan_your"] = {2027: 50_000.0}
        st.session_state["conv_plan_qcd"] = {2030: 5_000.0}
        plan, _ytd, _nii, label = active_plan(Household())
        st.session_state["out"] = (label, plan.your_conversions == {2027: 50_000.0})

    at = AppTest.from_function(_app, default_timeout=60)
    at.run()
    assert not at.exception
    label, used_partial_state = at.session_state["out"]
    assert label == "Fill 12%"
    assert not used_partial_state
