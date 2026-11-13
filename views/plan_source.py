"""The active conversion plan and its scenario inputs, from session state.

Single home for what the Conversion Planner feeds run_scenario (plan from the
``conv_plan_*`` keys, YTD actuals when the sidebar toggle is on, NIIT estimate),
shared by the planner and Income Control. Falls back to Fill 12% -- as the
Dashboard builds it -- when the planner has never run this session.
"""

from __future__ import annotations

import streamlit as st

from config.scalar_registry import scalar_default
from engine.scenario_autofill import auto_fill_12
from engine.scenario_types import ConversionPlan
from models.household import Household
from models.ytd_income import YTDSnapshot

PLAN_STATE_KEYS = (
    "conv_plan_your",
    "conv_plan_spouse",
    "conv_plan_qcd",
    "conv_plan_spouse_qcd",
)
PLANNER_PLAN_LABEL = "Conversion Plan"
FALLBACK_PLAN_LABEL = "Fill 12%"


def scenario_ytd_and_nii() -> tuple[YTDSnapshot | None, int]:
    """This year's actuals (when the sidebar toggle is on) and the NIIT
    estimate, as the Conversion Plan page feeds them to run_scenario."""
    # C26 (audit-0721): thread base-year YTD actuals through, mirroring the
    # apply_ytd_to_projection gating already used by sweet_spot.py/aca_irmaa.py
    # -- otherwise the sidebar's "Include this year's actuals" toggle has
    # no effect on this page. run_scenario itself narrows ytd to the base year.
    ytd = (
        st.session_state.get("ytd_snapshot")
        if st.session_state.get("apply_ytd_to_projection", False)
        else None
    )
    nii = int(st.session_state.get("net_inv_income", scalar_default("net_inv_income")))
    return ytd, nii


def planner_state_present() -> bool:
    """True once the Conversion Planner has seeded all four ``conv_plan_*`` keys."""
    return all(key in st.session_state for key in PLAN_STATE_KEYS)


def conversion_plan_from_state() -> ConversionPlan:
    """The ConversionPlan the planner builds from its session-state dicts."""
    return ConversionPlan(
        your_conversions=dict(st.session_state.get("conv_plan_your", {})),
        spouse_conversions=dict(st.session_state.get("conv_plan_spouse", {})),
        qcds=dict(st.session_state.get("conv_plan_qcd", {})),
        spouse_qcds=dict(st.session_state.get("conv_plan_spouse_qcd", {})),
    )


def active_plan(hh: Household) -> tuple[ConversionPlan, YTDSnapshot | None, int, str]:
    """``(plan, ytd, nii, plan_label)`` for the plan the user is working on."""
    ytd, nii = scenario_ytd_and_nii()
    if planner_state_present():
        return conversion_plan_from_state(), ytd, nii, PLANNER_PLAN_LABEL
    # audit-1018 F-10: size Fill 12% with the same YTD the projection runs with.
    return auto_fill_12(hh, ytd, nii), ytd, nii, FALLBACK_PLAN_LABEL
