"""Room This Year (redesign PR 4b): how much room is left this year, the
recommended Roth-conversion / option-exercise split, and what it does to the
first RMD year.

All numbers come from ``engine.headroom`` and ``engine.this_year`` (PR 4a);
this module owns Streamlit and the wording of the engine's reason codes.
"""

from __future__ import annotations

from dataclasses import dataclass

import plotly.graph_objects as go
import streamlit as st

from engine.exercise_optimizer import OptimizedPlan, optimize_exercises
from engine.exercise_schedule_store import save_exercise_schedule
from engine.headroom import HeadroomResult, compute_headroom
from engine.irmaa import irmaa_payment_year
from engine.scenario_types import ConversionPlan
from engine.this_year import (
    IncomeStack,
    LookaheadCase,
    Reason,
    ReasonCode,
    RmdLookahead,
    ThisYearSplit,
    apply_split_conversions,
    apply_split_schedule,
    draw_aware_bracket_rooms,
    rmd_lookahead,
    summarize_this_year,
)
from models.household import Household
from models.ytd_income import YTDSnapshot
from views import optimizer_panel
from views._format import fmt_dollars, fmt_pct
from views._shared import edit_link_button
from views._style import render_page_header
from views.conversion_state import clear_conversion_widgets

RESULT_KEY = "_room_result"
SIG_KEY = "_room_sig"
FLASH_KEY = "_room_flash"

# The tiles and the card's room-after are on different bases; say which.
TILES_LABEL = "So far this year (actuals; 12% / 22% rooms count living-expense IRA draws)"
BRACKET_TILE_HELP = (
    "The conversion that lands total taxable income on this bracket top, counting "
    "any IRA draw the plan needs for living expenses."
)
ROOM_AFTER_LABEL = (
    "Room left after the split — full-year projection, incl. IRA draws for living expenses"
)

_CEILING_TEXT = {
    "top-of-12": "12% bracket",
    "top-of-22": "22% bracket",
    "top-of-24": "24% bracket",
    "irmaa-safe": "IRMAA tier 1",
    "aca-safe": "ACA subsidy cliff",
    "current": "current plan",
}

_CASE_TEXT = {
    LookaheadCase.NONE: "No conversions",
    LookaheadCase.CURRENT: "Current plan",
    LookaheadCase.SPLIT: "With this split",
}


@dataclass(frozen=True)
class RoomResult:
    """One "Find this year's split" run: the optimizer's winner, the plan it
    was measured against, the summary, and the look-ahead (None, with the
    reason in ``lookahead_error``, when the first RMD year is past the
    projection)."""

    plan: OptimizedPlan
    current_plan: ConversionPlan
    split: ThisYearSplit
    lookahead: RmdLookahead | None
    lookahead_error: str | None


# ---------------------------------------------------------------------------
# Wording (pure)
# ---------------------------------------------------------------------------


def ceiling_text(label: str) -> str:
    """Display name of an optimizer ceiling label."""
    return _CEILING_TEXT.get(label, label)


def room_text(room: float) -> str:
    """A room in dollars, or "over by $X" when past the threshold."""
    if room < 0:
        return f"over by {fmt_dollars(-room)}"
    return fmt_dollars(room)


def reason_text(reason: Reason) -> str:
    """One rationale line for a ``ReasonCode``."""
    d = reason.data
    code = reason.code
    if code is ReasonCode.GRANT_EXPIRES:
        return f"Grant {d['grant_key']} expires in {d['expiry_year']} — exercise before it lapses."
    if code is ReasonCode.FILLS_BRACKET:
        # Bracket rooms count the forced living-expense IRA draws (engine/this_year.py).
        return (
            f"Fills to the {ceiling_text(str(d['ceiling_label']))} top, "
            "counting living-expense IRA draws."
        )
    if code is ReasonCode.EXCEEDS_CEILING:
        return (
            f"Goes past the {ceiling_text(str(d['ceiling_label']))} top "
            f"by {fmt_dollars(float(d['overshoot']))}."
        )
    if code is ReasonCode.CEILING_ALREADY_PASSED:
        return (
            "Income already realized this year is past the "
            f"{ceiling_text(str(d['ceiling_label']))}; nothing more fits under it."
        )
    if code is ReasonCode.IRMAA_LOOKBACK:
        if d["counts"]:
            return f"This year's MAGI sets Medicare premiums in {d['payment_year']}."
        return (
            f"IRMAA lookback starts with {d['first_relevant_year']} income — "
            "this year doesn't count."
        )
    return "Nothing worth doing this year."


def split_headline(split: ThisYearSplit) -> str:
    """The card's sentence: what is still to do this year, then what is done."""
    to_convert = sum(c.remaining for c in split.conversions)
    parts = []
    if to_convert > 0:
        part = f"convert {fmt_dollars(to_convert)}"
        if len(split.conversions) > 1:
            each = ", ".join(f"{c.person} {fmt_dollars(c.remaining)}" for c in split.conversions)
            part += f" ({each})"
        parts.append(part)
    if split.options_remaining > 0:
        parts.append(f"exercise {fmt_dollars(split.options_remaining)} of options")

    no_action = any(r.code is ReasonCode.NO_ACTION for r in split.reasons)
    if parts and not no_action:
        sentence = " and ".join(parts)
        headline = f"{sentence[0].upper()}{sentence[1:]} for the rest of {split.year}."
    else:
        headline = "Nothing more to do this year."

    converted = sum(c.done for c in split.conversions)
    exercised = split.options_total - split.options_remaining
    done = []
    if converted > 0:
        done.append(f"{fmt_dollars(converted)} converted")
    if exercised > 0:
        done.append(f"{fmt_dollars(exercised)} of options exercised")
    if done:
        headline += f" Already done: {', '.join(done)}."
    return headline


def stack_segments(stack: IncomeStack) -> tuple[tuple[str, float, float], ...]:
    """(name, start, end) of each bar segment, drawn cumulatively from
    ``locked`` so the bar ends at locked + roth + options -- the taxable income
    the 12%/22% lines and the card's room-after are measured against.

    Locked is negative when deductions exceed the other income; the part of
    Roth/options that is sheltered then has no width, rather than the whole
    bar shifting right (clamping locked alone would overstate the total)."""
    after_locked = max(stack.locked, 0.0)
    after_roth = max(stack.locked + stack.roth, 0.0)
    after_options = max(stack.locked + stack.roth + stack.options, 0.0)
    return (
        ("Locked", 0.0, after_locked),
        ("Roth", after_locked, after_roth),
        ("Options", after_roth, after_options),
    )


def _md(text: str) -> str:
    # Two unescaped dollar signs in one markdown call render as LaTeX.
    return text.replace("$", "\\$")


# ---------------------------------------------------------------------------
# Session plumbing
# ---------------------------------------------------------------------------


def _actuals() -> YTDSnapshot | None:
    """This year's actuals, regardless of the sidebar toggle: the toggle
    governs projections on other pages; this page is about this year."""
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
    """Inputs that change the split: the whole household (any Profile edit --
    balances, ages, price, filing status, grants, exercise schedule -- moves
    it; Household is a plain dataclass, so its repr covers every field),
    actuals, the conversion plan, the optimizer settings (discount and
    terminal tax rates, set in the Auto-Optimizer panel) and the manual NII
    estimate (#591). A stale split must never be Applied.

    render() runs ``hh.effective_schedule()`` (its idempotent in-place key
    migration) and then takes the signature once, before anything runs, so
    neither a run nor a later render can move it under its own result."""
    return (
        repr(hh),
        repr(ytd),
        tuple(sorted(plan.your_conversions.items())),
        tuple(sorted(plan.spouse_conversions.items())),
        tuple(sorted(plan.qcds.items())),
        tuple(sorted(plan.spouse_qcds.items())),
        optimizer_panel.optimizer_settings(),
        optimizer_panel.manual_nii(),
    )


def _current_result(sig: tuple) -> RoomResult | None:
    result = st.session_state.get(RESULT_KEY)
    if result is None or st.session_state.get(SIG_KEY) != sig:
        return None
    return result


def _run(hh: Household, sig: tuple) -> None:
    ytd = _actuals()
    current = _current_plan()
    discount_rate, terminal_tax_rate = optimizer_panel.optimizer_settings()
    nii = optimizer_panel.manual_nii()
    best = optimize_exercises(
        hh,
        current_plan=current,
        ytd=ytd,
        discount_rate=discount_rate,
        terminal_tax_rate=terminal_tax_rate,
        net_inv_income=nii,
    ).best
    split = summarize_this_year(hh, ytd, best, current_plan=current, net_inv_income=nii)
    lookahead: RmdLookahead | None = None
    error: str | None = None
    try:
        lookahead = rmd_lookahead(hh, ytd, current, split, best, net_inv_income=nii)
    except ValueError as exc:  # first RMD year past the projection's end
        error = str(exc)
    st.session_state[RESULT_KEY] = RoomResult(best, current, split, lookahead, error)
    st.session_state[SIG_KEY] = sig


def _apply_split(hh: Household, result: RoomResult) -> None:
    """Write the split into the Conversion Plan (this year only) and the saved
    exercise schedule (this year rebalanced into later years)."""
    # Lazy: views.planner and views.option_exercise import optimizer_panel,
    # which this module imports.
    from views.option_exercise._partials._helpers import _GRID_EDITOR_KEY, _SHARES_STATE_KEY
    from views.planner import grid_normalized_conversions

    applied = apply_split_conversions(result.current_plan, result.split)
    your, spouse = grid_normalized_conversions(
        hh, applied.your_conversions, applied.spouse_conversions
    )
    st.session_state["conv_plan_your"] = your
    st.session_state["conv_plan_spouse"] = spouse
    clear_conversion_widgets()

    schedule = apply_split_schedule(hh, result.plan)
    save_exercise_schedule(schedule)
    hh.exercise_schedule = schedule
    st.session_state.pop(_SHARES_STATE_KEY, None)
    st.session_state.pop(_GRID_EDITOR_KEY, None)

    # The plan changed under both cached optimizer results.
    for key in (optimizer_panel.RESULT_KEY, optimizer_panel.SIG_KEY, RESULT_KEY, SIG_KEY):
        st.session_state.pop(key, None)
    st.session_state[FLASH_KEY] = (
        "Split applied: this year's conversions to the Conversion Plan (session only) "
        "and exercises to the saved schedule."
    )


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _render_tiles(
    hh: Household, headroom: HeadroomResult, bracket_rooms: tuple[float, float]
) -> None:
    year = hh.base_year
    room_12, room_22 = bracket_rooms
    tiles = [
        ("Locked income", fmt_dollars(headroom.locked_magi), "MAGI already realized this year."),
        ("Room to 12% top", room_text(room_12), BRACKET_TILE_HELP),
        ("Room to 22% top", room_text(room_22), BRACKET_TILE_HELP),
        ("Room to IRMAA tier 1", room_text(headroom.room_to_irmaa_t1), None),
        ("Room to NIIT", room_text(headroom.room_to_niit), None),
    ]
    st.caption(TILES_LABEL)
    for col, (label, value, help_text) in zip(st.columns(5), tiles, strict=True):
        with col:
            st.metric(label, value, help=help_text)
    if headroom.irmaa_relevant:
        st.caption(f"IRMAA: {year} income sets {irmaa_payment_year(year)} Medicare premiums.")
    else:
        st.caption(
            f"IRMAA: not yet relevant — counts from {headroom.irmaa_first_relevant_year} income."
        )


def stack_figure(stack: IncomeStack, year: int) -> go.Figure:
    """The income-stack bar. Segment geometry is the taxable part
    (``stack_segments``); legend and hover carry the full Roth / options
    amounts, which are longer than their segments when locked < 0."""
    full = {"Locked": stack.locked, "Roth": stack.roth, "Options": stack.options}
    names = {
        "Locked": "Locked income",
        "Roth": f"Roth conversion {fmt_dollars(stack.roth)}",
        "Options": f"Options {fmt_dollars(stack.options)}",
    }
    fig = go.Figure()
    for key, start, end in stack_segments(stack):
        fig.add_trace(
            go.Bar(
                y=[str(year)],
                x=[end - start],
                base=[start],
                name=names[key],
                orientation="h",
                hovertemplate=(
                    f"{names[key]}<br>full amount {fmt_dollars(full[key])}"
                    "<br>taxable span $%{base:,.0f} – $%{customdata:,.0f}<extra></extra>"
                ),
                customdata=[end],
            )
        )
    for top, label in ((stack.top_of_12, "12% top"), (stack.top_of_22, "22% top")):
        fig.add_vline(x=top, line_dash="dash", annotation_text=label)
    fig.update_layout(
        barmode="overlay",  # each segment carries its own base
        height=200,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        xaxis_title="Taxable income",
        yaxis={"type": "category"},  # else "2026" reads as a number: 2,025.5 ticks
        legend={"orientation": "h"},
    )
    return fig


def sheltered_caption(stack: IncomeStack) -> str | None:
    """When deductions exceed the other income, the first part of the split
    falls under them: say so, since the bar draws only the taxable part."""
    if stack.locked >= 0:
        return None
    excess = -stack.locked
    untaxed = min(excess, stack.roth + stack.options)
    return (
        f"Deductions exceed other income by {fmt_dollars(excess)}, so the first "
        f"{fmt_dollars(untaxed)} of the split is untaxed."
    )


def _render_stack(stack: IncomeStack, year: int) -> None:
    st.plotly_chart(stack_figure(stack, year), width="stretch")
    caption = sheltered_caption(stack)
    if caption:
        st.caption(_md(caption))


def _render_card(result: RoomResult) -> bool:
    """The recommendation card; True when Apply was clicked."""
    split = result.split
    with st.container(border=True):
        st.markdown(f"#### Recommended split for {split.year}")
        st.markdown(f"**{_md(split_headline(split))}**")
        st.markdown("\n".join(f"- {_md(reason_text(r))}" for r in split.reasons))
        st.caption(
            f"Strategy: {ceiling_text(split.ceiling_label)} — the lowest modeled "
            "lifetime cost of the optimizer's candidates."
        )
        if split.option_floor_shortfall > 0:
            st.caption(
                _md(
                    f"{fmt_dollars(split.option_floor_shortfall)} of option income already "
                    "realized is not covered by the schedule's in-the-money grants."
                )
            )
        room = split.room_after
        # Full-year scenario basis, unlike the actuals-only tiles above. The
        # bracket rooms are before the living-expense draws (engine/this_year.py).
        st.caption(
            _md(
                f"{ROOM_AFTER_LABEL}: 12% top {fmt_dollars(room.to_12pct)} · "
                f"22% top {fmt_dollars(room.to_22pct)} · IRMAA tier 1 "
                f"{fmt_dollars(room.to_irmaa_t1)} · NIIT {fmt_dollars(room.to_niit)}"
            )
        )
        apply_col, adjust_col = st.columns(2)
        with apply_col:
            clicked = st.button("Apply to plan", key="room_apply", type="primary")
        with adjust_col:
            edit_link_button(
                key="room_adjust", page="conversions", label="Adjust in Conversion Plan"
            )
    return clicked


def _render_lookahead(result: RoomResult) -> None:
    if result.lookahead is None:
        st.markdown("#### First RMD year")
        st.info(f"No look-ahead: {result.lookahead_error}.")
        return
    look = result.lookahead
    st.markdown(f"#### First RMD year ({look.year})")
    cases = [look.case(which) for which in _CASE_TEXT]
    fig = go.Figure(
        go.Bar(
            x=[_CASE_TEXT[c.case] for c in cases],
            y=[c.total_rmd for c in cases],
            text=[fmt_dollars(c.total_rmd) for c in cases],
            textposition="auto",
            hovertemplate="%{x}: $%{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=280, margin={"l": 10, "r": 10, "t": 10, "b": 10}, yaxis_title="Total RMD"
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        " · ".join(
            f"{_CASE_TEXT[c.case]}: {fmt_pct(c.marginal_bracket, 0)} bracket, "
            f"IRMAA tier {c.irmaa_tier}"
            for c in cases
        )
    )


def render(hh: Household) -> None:
    render_page_header("room", str(hh.base_year))
    st.caption(
        "Room left this year under each threshold, and how to use it. Uses this "
        "year's actuals from This Year's Income whether or not \"Include this "
        "year's actuals\" is on."
    )
    ytd = _actuals()
    hh.effective_schedule()  # run its idempotent in-place key migration first
    sig = _signature(hh, ytd, _current_plan())  # before anything can touch hh
    headroom = compute_headroom(
        hh,
        ytd if ytd is not None else YTDSnapshot(tax_year=hh.base_year),
        filing_status=hh.filing_status,
    )
    _render_tiles(
        hh, headroom, draw_aware_bracket_rooms(hh, ytd, headroom, optimizer_panel.manual_nii())
    )

    st.markdown("---")
    flash_slot = st.empty()  # filled after the button, which may clear the flash
    st.caption(
        "Runs the exercise optimizer on this year's actuals and shows this "
        "year's slice of the winning plan, plus a look-ahead to the first RMD year."
    )
    if st.button("Find this year's split", key="room_run"):
        st.session_state.pop(FLASH_KEY, None)
        with st.spinner("Comparing strategies…"):
            _run(hh, sig)
    flash = st.session_state.get(FLASH_KEY)
    if flash:
        flash_slot.success(flash)

    result = _current_result(sig)
    if result is None:
        return
    _render_stack(result.split.income_stack, result.split.year)
    if _render_card(result):
        _apply_split(hh, result)
        st.rerun()
    _render_lookahead(result)
