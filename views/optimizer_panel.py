"""Exercise Auto-Optimizer panel, shared by Conversion Plan and Option Exercises.

The optimizer sweeps bracket/MAGI ceiling strategies for option-exercise
timing and picks the schedule + conversion plan with the lowest ranking score:
modeled lifetime tax and costs plus the tax still owed on IRA money left at the
end, optionally discounted (audit-1008 F-07). It solves both jointly, so one
run feeds both pages: the result is cached in session state and each page shows
its own half with its own Apply button.

All computation goes through ``engine.exercise_optimizer``; this module owns
Streamlit only.
"""

from dataclasses import replace

import pandas as pd
import streamlit as st

from config.scalar_registry import TERMINAL_IRA_TAX_RATE_BOUNDS, scalar_default
from engine.exercise_optimizer import (
    OptimizedPlan,
    OptimizerResult,
    optimize_exercises,
)
from engine.exercise_schedule_store import save_exercise_schedule
from engine.scenario_types import ConversionPlan
from models.exercise_schedule import ExerciseSchedule, explicit_price_overrides
from models.household import Household
from models.ytd_income import YTDSnapshot
from views._format import fmt_dollars
from views.conversion_state import clear_conversion_widgets
from views.setup._state import autosave_user_defaults

RESULT_KEY = "_auto_opt_result"
SIG_KEY = "_auto_opt_sig"
FLASH_KEY = "_auto_opt_flash"

# audit-1008 F-07: optimizer settings, in percent. The store keys hold the
# value for the session; each half has its own widget (both halves can render
# on one page), seeded from the store every render and copied back by an
# on_change callback -- which runs before the rerun, so the signature taken at
# the top of the rerun already sees the new value. Both optimizer pages and
# Room This Year read the same stored setting.
#
# The terminal rate's store is the persisted household setting
# ``terminal_ira_tax_rate`` (config.scalar_registry; edited under Setup ›
# Assumptions, saved and exported like the other scalars). The panel inputs
# edit that same setting, so a change made here is the setting. The discount
# rate stays session-only.
DISCOUNT_STORE_KEY = "_auto_opt_discount_pct"
TERMINAL_STORE_KEY = "terminal_ira_tax_rate"
_DEFAULT_DISCOUNT_PCT = 0.0


def terminal_rate_pct() -> float:
    """The terminal IRA tax rate setting in percent, clamped to its bounds."""
    low, high = TERMINAL_IRA_TAX_RATE_BOUNDS
    raw = st.session_state.get(TERMINAL_STORE_KEY)
    try:
        value = float(raw) if raw is not None else float(scalar_default(TERMINAL_STORE_KEY))
    except (TypeError, ValueError):
        value = float(scalar_default(TERMINAL_STORE_KEY))
    return min(max(value, low), high)


def manual_nii() -> float:
    """The manual "Additional NII" estimate (Setup › Assumptions) the planning
    pages price NIIT on (audit-1008 F-78); the optimizer runs on it too (#591)."""
    raw = st.session_state.get("net_inv_income", scalar_default("net_inv_income"))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(scalar_default("net_inv_income"))


def optimizer_settings() -> tuple[float, float]:
    """(discount_rate, terminal_tax_rate) as fractions, from the session."""
    discount = float(st.session_state.get(DISCOUNT_STORE_KEY, _DEFAULT_DISCOUNT_PCT))
    return discount / 100.0, terminal_rate_pct() / 100.0


def _copy_to_store(widget_key: str, store_key: str) -> None:
    st.session_state[store_key] = st.session_state[widget_key]


def _copy_terminal_rate(widget_key: str) -> None:
    """Copy the terminal rate to its store and persist it if it changed.

    The setting is a saved household scalar, but only the Profile/Data pages
    autosave, so an edit made on the optimizer pages was lost on restart
    (audit-1026 F-22).
    """
    before = st.session_state.get(TERMINAL_STORE_KEY)
    _copy_to_store(widget_key, TERMINAL_STORE_KEY)
    if st.session_state[TERMINAL_STORE_KEY] != before:
        autosave_user_defaults()


def _render_settings(half: str) -> None:
    """Discount rate and terminal IRA tax rate inputs."""
    discount_key = f"auto_opt_discount_pct_{half}"
    terminal_key = f"auto_opt_terminal_tax_pct_{half}"
    discount_pct, terminal_pct = (rate * 100.0 for rate in optimizer_settings())
    st.session_state[discount_key] = discount_pct
    st.session_state[terminal_key] = terminal_pct
    left, right = st.columns(2)
    left.number_input(
        "Discount rate (%/yr)",
        min_value=0.0,
        max_value=15.0,
        step=0.5,
        key=discount_key,
        on_change=_copy_to_store,
        args=(discount_key, DISCOUNT_STORE_KEY),
        help=(
            "Counts a dollar of tax paid in a later year as worth less than a "
            "dollar paid today. 0 compares plain dollar totals."
        ),
    )
    right.number_input(
        "Tax rate on IRA money left at the end (%)",
        min_value=TERMINAL_IRA_TAX_RATE_BOUNDS[0],
        max_value=TERMINAL_IRA_TAX_RATE_BOUNDS[1],
        step=1.0,
        key=terminal_key,
        on_change=_copy_terminal_rate,
        args=(terminal_key,),
        help=(
            "Traditional and inherited IRA balances still unspent at the end of "
            "the projection are taxed when they come out, by the survivor or by "
            "heirs. This rate prices that deferred tax so a plan can't look "
            "cheap just by leaving it behind. Same setting as Setup › "
            "Assumptions; a change here changes it there."
        ),
    )


def _projection_ytd() -> YTDSnapshot | None:
    """This year's actuals when the sidebar's "Include this year's actuals"
    toggle is on (same gating as views/planner.py), else None."""
    if not st.session_state.get("apply_ytd_to_projection", False):
        return None
    return st.session_state.get("ytd_snapshot")


def _current_plan() -> ConversionPlan:
    """The plan the Conversion Plan page runs, QCDs included (audit-1008 F-60)."""
    return ConversionPlan(
        your_conversions=dict(st.session_state.get("conv_plan_your", {})),
        spouse_conversions=dict(st.session_state.get("conv_plan_spouse", {})),
        qcds=dict(st.session_state.get("conv_plan_qcd", {})),
        spouse_qcds=dict(st.session_state.get("conv_plan_spouse_qcd", {})),
    )


def _signature(hh: Household, ytd: YTDSnapshot | None, plan: ConversionPlan) -> tuple:
    """Inputs that change the optimizer's answer: the whole household (any
    Profile edit -- balances, ages, price, filing status, grants, exercise
    schedule -- moves it; Household is a plain dataclass, so its repr covers
    every field), actuals (``repr`` covers every field, including by_owner),
    the current plan (the baseline candidate), the optimizer settings
    (discount and terminal tax rates) and the manual NII estimate (#591). A
    stale result must never be displayed or Applied.

    Each half takes it once, before anything runs: the optimizer can migrate
    keys on ``hh.exercise_schedule`` in place (effective_schedule()), so a
    signature taken after a run might never match the next render's."""
    return (
        repr(hh),
        repr(ytd),
        tuple(sorted(plan.your_conversions.items())),
        tuple(sorted(plan.spouse_conversions.items())),
        tuple(sorted(plan.qcds.items())),
        tuple(sorted(plan.spouse_qcds.items())),
        optimizer_settings(),
        manual_nii(),
    )


def _render_signature(hh: Household) -> tuple:
    hh.effective_schedule()  # run its idempotent in-place key migration first
    return _signature(hh, _projection_ytd(), _current_plan())


def current_result(hh: Household, sig: tuple | None = None) -> OptimizerResult | None:
    """The cached result, or None when there is none or it is stale."""
    result = st.session_state.get(RESULT_KEY)
    if sig is None:
        sig = _render_signature(hh)
    if result is None or st.session_state.get(SIG_KEY) != sig:
        return None
    return result


def _run_optimizer(hh: Household, sig: tuple) -> None:
    ytd = _projection_ytd()
    current_plan = _current_plan()
    discount_rate, terminal_tax_rate = optimizer_settings()
    st.session_state[RESULT_KEY] = optimize_exercises(
        hh,
        current_plan=current_plan,
        ytd=ytd,
        discount_rate=discount_rate,
        terminal_tax_rate=terminal_tax_rate,
        net_inv_income=manual_nii(),
    )
    st.session_state[SIG_KEY] = sig


def _render_strategies(result: OptimizerResult) -> None:
    st.markdown("#### Candidate Strategies")
    ordered = sorted(result.candidates, key=lambda c: c is not result.best)
    rows = []
    for c in ordered:
        over = ", ".join(str(y) for y in c.over_ceiling_years)
        rows.append(
            {
                "Best": "✅" if c is result.best else "",
                "Strategy": c.ceiling_label,
                "Lifetime tax & costs": fmt_dollars(c.lifetime_all_in),
                "Tax left in IRAs at end": fmt_dollars(c.terminal_deferred_tax),
                "Ranking score": fmt_dollars(c.score),
                "Δ vs your plan": fmt_dollars(c.score - result.baseline_score, sign=True),
                "⚠ Over ceiling": f"⚠ {over}" if over else "",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "Ranking score = lifetime tax & costs + tax left in IRAs at the end, "
        "discounted to this year at the discount rate (plain sum at 0%)."
    )

    best = result.best
    st.markdown("#### Winning Strategy")
    st.success(
        f"**{best.ceiling_label}** — ranking score {fmt_dollars(best.score)} "
        f"(Δ {fmt_dollars(best.score - result.baseline_score, sign=True)} vs your plan when the optimizer ran)"
    )
    if best.over_ceiling_years:
        years = ", ".join(str(y) for y in best.over_ceiling_years)
        st.warning(f"Ceiling exceeded in: {years} (forced hold-to-expiry lump).")


def _flash_for(half: str) -> str | None:
    """The post-Apply message belonging to ``half``, or None."""
    flash = st.session_state.get(FLASH_KEY)
    if isinstance(flash, dict) and flash.get("half") == half:
        return flash["message"]
    return None


def _render_run(hh: Household, sig: tuple, key: str, half: str) -> OptimizerResult | None:
    """Flash, caption, Run button; returns the current result or None.

    The flash is not popped here: the Conversion Plan page reruns a second
    time after Apply, so it stays until the next Run or Apply replaces it.
    """
    flash_slot = st.empty()  # filled after the button, which may clear the flash
    st.caption(
        "Sweeps bracket and MAGI ceiling strategies for option-exercise timing "
        "and auto-filled conversions, then picks the combination with the "
        "lowest ranking score: modeled lifetime tax and costs plus the tax "
        "still owed on IRA money left at the end. Never worse than your "
        "current plan — it's included as a baseline candidate. One run feeds "
        "both Conversion Plan and Option Exercises."
    )
    _render_settings(half)
    if st.button("Run optimizer", type="primary", key=key):
        st.session_state.pop(FLASH_KEY, None)
        _run_optimizer(hh, sig)
    message = _flash_for(half)
    if message:
        flash_slot.success(message)
    result = current_result(hh, sig)
    if result is None:
        st.info("Click **Run optimizer** to compare exercise strategies.")
        return None
    _render_strategies(result)
    return result


def _render_conversions(best: OptimizedPlan) -> None:
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


def _render_schedule(best: OptimizedPlan) -> None:
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


def render_conversion_half(hh: Household) -> None:
    """Conversion Plan: the winning conversions and Apply conversions."""
    if not hh.grants:
        return  # without grants the optimizer is just Auto-Fill
    sig = _render_signature(hh)  # before anything can touch hh
    with st.expander(
        "🧮 Auto-Optimizer — conversions with option exercises",
        expanded=_flash_for("conversions") is not None or current_result(hh, sig) is not None,
    ):
        result = _render_run(hh, sig, key="auto_opt_run_conversions", half="conversions")
        if result is None:
            return
        _render_conversions(result.best)
        if st.button("Apply conversions"):
            # Lazy: views.planner imports this module. Write the plan in the
            # grid's normalized form so the grid's round-trip is a no-op.
            from views.planner import grid_normalized_conversions

            your, spouse = grid_normalized_conversions(
                hh,
                result.best.conversions.your_conversions,
                result.best.conversions.spouse_conversions,
            )
            st.session_state["conv_plan_your"] = your
            st.session_state["conv_plan_spouse"] = spouse
            # The plan is now this result's own winner, not a stale baseline:
            # re-stamp so the same run still feeds the exercise half.
            st.session_state[SIG_KEY] = _render_signature(hh)
            clear_conversion_widgets()
            st.session_state[FLASH_KEY] = {
                "half": "conversions",
                "message": (
                    "Conversions applied to the Conversion Plan "
                    "(session only, like the plan itself — not saved to disk)."
                ),
            }
            st.rerun()


def _schedule_to_persist(hh: Household, schedule: ExerciseSchedule) -> ExerciseSchedule:
    """The winner's schedule in the shape the manual Save writes (#543): only
    prices that differ from the projection are stored. The user's saved
    overrides stay (even for years the winner doesn't exercise in); projected
    prices the optimizer used are dropped so they can't go stale."""
    stored = hh.exercise_schedule.price_by_year if hh.exercise_schedule else {}
    prices = explicit_price_overrides(
        {**stored, **schedule.price_by_year}, hh.projected_stock_price
    )
    return replace(schedule, price_by_year=prices)


def render_exercise_half(hh: Household) -> None:
    """Option Exercises: the winning exercise schedule and Apply exercises."""
    if not hh.grants:
        return
    sig = _render_signature(hh)  # before anything can touch hh
    with st.expander(
        "🧮 Auto-Optimizer — exercise timing",
        expanded=_flash_for("exercises") is not None or current_result(hh, sig) is not None,
    ):
        result = _render_run(hh, sig, key="auto_opt_run_exercises", half="exercises")
        if result is None:
            return
        _render_schedule(result.best)
        if st.button("Apply exercises"):
            # Lazy: views.option_exercise imports this module. Only the shares
            # editor is dropped; _clear_widget_state would also wipe oe_price_*.
            from views.option_exercise._partials._helpers import (
                _GRID_EDITOR_KEY,
                _SHARES_STATE_KEY,
            )

            schedule = _schedule_to_persist(hh, result.best.schedule)
            save_exercise_schedule(schedule)
            hh.exercise_schedule = schedule
            # The schedule is now this result's own winner (the base projection
            # ignores the stored schedule, #546): re-stamp so it stays current.
            st.session_state[SIG_KEY] = _render_signature(hh)
            st.session_state.pop(_SHARES_STATE_KEY, None)
            st.session_state.pop(_GRID_EDITOR_KEY, None)
            st.session_state[FLASH_KEY] = {
                "half": "exercises",
                "message": "Exercise schedule applied and saved.",
            }
            st.rerun()
