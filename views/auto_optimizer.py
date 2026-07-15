"""Exercise Auto-Optimizer — sweeps bracket/MAGI ceiling strategies for
option-exercise timing and picks the schedule + conversion plan with the
lowest modeled lifetime all-in cost.

All computation goes through ``engine.exercise_optimizer`` — this module owns
Streamlit only.
"""

import pandas as pd
import streamlit as st

from engine.exercise_optimizer import OptimizedPlan, OptimizerResult, optimize_exercises
from engine.exercise_schedule_store import save_exercise_schedule
from engine.scenario_types import ConversionPlan
from models.household import Household
from views._format import fmt_dollars

_RESULT_KEY = "_auto_opt_result"
_SIG_KEY = "_auto_opt_sig"


def _signature(hh: Household) -> tuple:
    """A cheap fingerprint of the inputs that change the optimizer's answer.

    Used to invalidate a cached ``OptimizerResult`` when the household's
    grants or base year change out from under it (e.g. a fresh FinExtract
    sync) so stale results are never displayed as current.
    """
    return (
        hh.base_year,
        tuple((g.key(), g.shares, g.strike, g.expiry_year) for g in hh.grants),
    )


def _run_optimizer(hh: Household) -> None:
    current_plan = ConversionPlan(
        your_conversions=dict(st.session_state.get("conv_plan_your", {})),
        spouse_conversions=dict(st.session_state.get("conv_plan_spouse", {})),
    )
    result = optimize_exercises(hh, current_plan=current_plan, ytd=None)
    st.session_state[_RESULT_KEY] = result
    st.session_state[_SIG_KEY] = _signature(hh)


def _render_candidates(result: OptimizerResult) -> None:
    st.markdown("### Candidate Strategies")
    ordered = sorted(result.candidates, key=lambda c: c is not result.best)
    rows = []
    for c in ordered:
        over = ", ".join(str(y) for y in c.over_ceiling_years)
        rows.append(
            {
                "Best": "✅" if c is result.best else "",
                "Strategy": c.ceiling_label,
                "Lifetime all-in cost": fmt_dollars(c.lifetime_all_in),
                "Δ vs baseline": fmt_dollars(c.lifetime_all_in - result.baseline_cost, sign=True),
                "⚠ Over ceiling": f"⚠ {over}" if over else "",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_winner(result: OptimizerResult) -> None:
    best = result.best
    st.markdown("### Winning Strategy")
    st.success(
        f"**{best.ceiling_label}** — lifetime all-in cost {fmt_dollars(best.lifetime_all_in)} "
        f"(Δ {fmt_dollars(best.lifetime_all_in - result.baseline_cost, sign=True)} vs current plan)"
    )
    if best.over_ceiling_years:
        years = ", ".join(str(y) for y in best.over_ceiling_years)
        st.warning(f"Ceiling exceeded in: {years} (forced hold-to-expiry lump).")

    st.markdown("#### Exercise Schedule (shares)")
    schedule_rows = [
        {"Grant": key, "Year": year, "Shares": shares}
        for key, years in best.schedule.shares_by_grant_year.items()
        for year, shares in sorted(years.items())
    ]
    if schedule_rows:
        st.dataframe(pd.DataFrame(schedule_rows), hide_index=True, width="stretch")
    else:
        st.caption("No exercises scheduled.")

    st.markdown("#### Winning Conversions")
    conv_rows = [
        {"Year": year, "Who": "You", "Conversion": fmt_dollars(amount)}
        for year, amount in sorted(best.conversions.your_conversions.items())
    ]
    conv_rows += [
        {"Year": year, "Who": "Spouse", "Conversion": fmt_dollars(amount)}
        for year, amount in sorted(best.conversions.spouse_conversions.items())
    ]
    if conv_rows:
        st.dataframe(pd.DataFrame(conv_rows), hide_index=True, width="stretch")
    else:
        st.caption("No conversions in the winning plan.")


def _render_apply_buttons(hh: Household, best: OptimizedPlan) -> None:
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Apply exercises"):
            save_exercise_schedule(best.schedule)
            hh.exercise_schedule = best.schedule
            st.success("Exercise schedule applied and saved.")
    with col2:
        if st.button("Apply conversions"):
            st.session_state["conv_plan_your"] = dict(best.conversions.your_conversions)
            st.session_state["conv_plan_spouse"] = dict(best.conversions.spouse_conversions)
            st.success("Conversions applied to the Conversion Planner.")
            st.caption(
                "Session-only, like the Conversion Planner itself — not persisted to disk."
            )


def render(hh: Household) -> None:
    st.title("🧮 Exercise Auto-Optimizer")
    st.caption(
        "Sweeps bracket and MAGI ceiling strategies for option-exercise timing "
        "and auto-filled conversions, then picks the combination with the "
        "lowest modeled lifetime all-in cost. Never worse than your current "
        "plan — it's included as a baseline candidate."
    )

    if not hh.grants:
        st.info("No option grants loaded.")
        return

    if st.button("Run optimizer", type="primary"):
        _run_optimizer(hh)

    result: OptimizerResult | None = st.session_state.get(_RESULT_KEY)
    sig = st.session_state.get(_SIG_KEY)
    if result is None or sig != _signature(hh):
        st.info("Click **Run optimizer** to compare exercise strategies.")
        return

    _render_candidates(result)
    _render_winner(result)
    _render_apply_buttons(hh, result.best)
