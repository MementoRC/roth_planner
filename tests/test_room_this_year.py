"""views/room_this_year.py: the Room This Year page (redesign PR 4b).

Synthetic households only -- the repo is public.
"""

import pytest
from streamlit.testing.v1 import AppTest

from engine.this_year import (
    IncomeStack,
    PersonConversion,
    Reason,
    ReasonCode,
    RoomAfter,
    ThisYearSplit,
)
from views.room_this_year import (
    ROOM_AFTER_LABEL,
    TILES_LABEL,
    ceiling_text,
    reason_text,
    room_text,
    sheltered_caption,
    split_headline,
    stack_figure,
    stack_segments,
)

# ---------------------------------------------------------------------------
# Pure wording helpers
# ---------------------------------------------------------------------------


def _split(
    conversions=(),
    options_total=0.0,
    options_remaining=0.0,
    reasons=(),
    locked=50_000.0,
) -> ThisYearSplit:
    return ThisYearSplit(
        year=2026,
        ceiling_label="top-of-22",
        exercises=(),
        options_total=options_total,
        options_remaining=options_remaining,
        option_floor_shortfall=0.0,
        conversions=tuple(conversions),
        income_stack=IncomeStack(
            locked=locked, roth=0.0, options=0.0, top_of_12=100_800.0, top_of_22=211_400.0
        ),
        room_after=RoomAfter(to_12pct=0.0, to_22pct=0.0, to_irmaa_t1=0.0, to_niit=0.0),
        reasons=tuple(reasons),
    )


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("top-of-12", "12% bracket"),
        ("top-of-22", "22% bracket"),
        ("top-of-24", "24% bracket"),
        ("irmaa-safe", "IRMAA tier 1"),
        ("aca-safe", "ACA subsidy cliff"),
        ("current", "current plan"),
        ("something-new", "something-new"),
    ],
)
def test_ceiling_text(label, text):
    assert ceiling_text(label) == text


def test_reason_text_grant_expires():
    r = Reason(ReasonCode.GRANT_EXPIRES, {"grant_key": "g1", "expiry_year": 2027})
    assert reason_text(r) == "Grant g1 expires in 2027 — exercise before it lapses."


def test_reason_text_fills_bracket():
    r = Reason(ReasonCode.FILLS_BRACKET, {"ceiling_label": "top-of-22", "room_after": 120.0})
    assert reason_text(r) == "Fills to the 22% bracket top, counting living-expense IRA draws."


def test_reason_text_exceeds_ceiling_uses_the_reason_overshoot():
    r = Reason(ReasonCode.EXCEEDS_CEILING, {"ceiling_label": "top-of-12", "overshoot": 4_321.0})
    assert reason_text(r) == "Goes past the 12% bracket top by $4,321."


def test_reason_text_ceiling_already_passed():
    r = Reason(ReasonCode.CEILING_ALREADY_PASSED, {"ceiling_label": "irmaa-safe"})
    assert reason_text(r) == (
        "Income already realized this year is past the IRMAA tier 1; nothing more fits under it."
    )


def test_reason_text_irmaa_lookback_counts():
    r = Reason(
        ReasonCode.IRMAA_LOOKBACK,
        {"payment_year": 2028, "counts": True, "first_relevant_year": 2026},
    )
    assert reason_text(r) == "This year's MAGI sets Medicare premiums in 2028."


def test_reason_text_irmaa_lookback_not_yet():
    r = Reason(
        ReasonCode.IRMAA_LOOKBACK,
        {"payment_year": 2028, "counts": False, "first_relevant_year": 2027},
    )
    assert reason_text(r) == "IRMAA lookback starts with 2027 income — this year doesn't count."


def test_reason_text_no_action():
    assert reason_text(Reason(ReasonCode.NO_ACTION)) == "Nothing worth doing this year."


def test_every_reason_code_has_wording():
    data = {
        "grant_key": "g",
        "expiry_year": 2027,
        "ceiling_label": "top-of-22",
        "room_after": 0.0,
        "overshoot": 1.0,
        "payment_year": 2028,
        "counts": True,
        "first_relevant_year": 2026,
    }
    for code in ReasonCode:
        assert reason_text(Reason(code, data))


def test_headline_per_person_and_options():
    split = _split(
        conversions=[
            PersonConversion("you", year_total=25_000.0, done=0.0, remaining=25_000.0),
            PersonConversion("spouse", year_total=15_000.0, done=0.0, remaining=15_000.0),
        ],
        options_total=30_000.0,
        options_remaining=30_000.0,
    )
    assert split_headline(split) == (
        "Convert $40,000 (you $25,000, spouse $15,000) and exercise $30,000 of options "
        "for the rest of 2026."
    )


def test_headline_single_person_options_only():
    split = _split(
        conversions=[PersonConversion("you", year_total=0.0, done=0.0, remaining=0.0)],
        options_total=12_000.0,
        options_remaining=12_000.0,
    )
    assert split_headline(split) == "Exercise $12,000 of options for the rest of 2026."


def test_headline_mentions_what_is_already_done():
    split = _split(
        conversions=[
            PersonConversion("you", year_total=30_000.0, done=10_000.0, remaining=20_000.0)
        ],
        options_total=8_000.0,
        options_remaining=0.0,
    )
    assert split_headline(split) == (
        "Convert $20,000 for the rest of 2026. "
        "Already done: $10,000 converted, $8,000 of options exercised."
    )


def test_headline_no_action():
    split = _split(
        conversions=[PersonConversion("you", year_total=5_000.0, done=5_000.0, remaining=0.0)],
        reasons=[Reason(ReasonCode.NO_ACTION)],
    )
    assert split_headline(split) == (
        "Nothing more to do this year. Already done: $5,000 converted."
    )


def _stack(locked, roth, options):
    return IncomeStack(
        locked=locked, roth=roth, options=options, top_of_12=100_800.0, top_of_22=211_400.0
    )


def test_stack_segments_negative_locked_starts_roth_at_zero_and_ends_at_the_total():
    # Deductions exceed other income by 32k: that much of the Roth is sheltered.
    segs = stack_segments(_stack(-32_000.0, 120_000.0, 10_000.0))
    assert segs == (
        ("Locked", 0.0, 0.0),
        ("Roth", 0.0, 88_000.0),
        ("Options", 88_000.0, 98_000.0),
    )
    assert segs[-1][2] == -32_000.0 + 120_000.0 + 10_000.0


def test_stack_segments_negative_locked_larger_than_roth():
    segs = stack_segments(_stack(-50_000.0, 30_000.0, 25_000.0))
    assert segs == (
        ("Locked", 0.0, 0.0),
        ("Roth", 0.0, 0.0),
        ("Options", 0.0, 5_000.0),
    )


def test_stack_segments_positive_locked_stacks_end_to_end():
    segs = stack_segments(_stack(7_000.0, 40_000.0, 5_000.0))
    assert segs == (
        ("Locked", 0.0, 7_000.0),
        ("Roth", 7_000.0, 47_000.0),
        ("Options", 47_000.0, 52_000.0),
    )


def test_stack_figure_year_axis_is_categorical():
    fig = stack_figure(_stack(7_000.0, 40_000.0, 5_000.0), 2026)
    assert fig.layout.yaxis.type == "category"
    assert all(trace.y == ("2026",) for trace in fig.data)


def test_stack_figure_names_carry_the_full_amounts_when_locked_is_negative():
    fig = stack_figure(_stack(-32_200.0, 71_350.0, 10_000.0), 2026)
    names = [trace.name for trace in fig.data]
    assert names == ["Locked income", "Roth conversion $71,350", "Options $10,000"]
    roth = fig.data[1]
    assert roth.base == (0.0,)
    assert roth.x == (39_150.0,)  # geometry is the taxable part only
    assert "full amount $71,350" in roth.hovertemplate


def test_sheltered_caption_only_when_locked_is_negative():
    assert sheltered_caption(_stack(0.0, 40_000.0, 0.0)) is None
    assert sheltered_caption(_stack(7_000.0, 40_000.0, 0.0)) is None
    assert sheltered_caption(_stack(-32_200.0, 71_350.0, 0.0)) == (
        "Deductions exceed other income by $32,200, so the first $32,200 of the split is untaxed."
    )


def test_sheltered_caption_caps_untaxed_at_the_split():
    assert sheltered_caption(_stack(-50_000.0, 20_000.0, 5_000.0)) == (
        "Deductions exceed other income by $50,000, so the first $25,000 of the split is untaxed."
    )


@pytest.mark.parametrize(
    ("room", "text"),
    [(12_345.4, "$12,345"), (0.0, "$0"), (-2_500.0, "over by $2,500")],
)
def test_room_text(room, text):
    assert room_text(room) == text


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _hh(**overrides):
    from models.grants import StockGrant
    from models.household import Household

    kwargs = {
        "your_age": 60,
        "spouse_age": 54,
        "base_year": 2026,
        "your_ira": 1_000_000,
        "spouse_ira": 1_000_000,
        "stock_price_now": 200.0,
        "grants": [
            StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")
        ],
    }
    kwargs.update(overrides)
    return Household(**kwargs)


def _render_page() -> None:
    # Self-contained (AppTest runs this function's source): the household is
    # _hh()'s, with per-test field overrides from session_state["_test_hh"].
    import streamlit as st

    from models.grants import StockGrant
    from models.household import Household
    from views.room_this_year import render

    kwargs = {
        "your_age": 60,
        "spouse_age": 54,
        "base_year": 2026,
        "your_ira": 1_000_000,
        "spouse_ira": 1_000_000,
        "stock_price_now": 200.0,
        "grants": [
            StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")
        ],
    }
    kwargs.update(st.session_state.get("_test_hh", {}))
    render(Household(**kwargs))


def _app() -> AppTest:
    # Optimizer + look-ahead + rerun can pass Streamlit's 3s default under load.
    return AppTest.from_function(_render_page, default_timeout=90)


def _click(at: AppTest, label: str) -> None:
    [b for b in at.button if b.label == label][0].click()
    at.run()


def _labels(at: AppTest) -> list[str]:
    return [b.label for b in at.button]


def test_page_renders_tiles_without_running_the_optimizer(monkeypatch) -> None:
    import views.room_this_year as page

    def boom(*args, **kwargs):
        raise AssertionError("optimizer must not run on load")

    monkeypatch.setattr(page, "optimize_exercises", boom)
    at = _app()
    at.run()

    assert not at.exception
    assert len(at.metric) == 5
    assert "Find this year's split" in _labels(at)
    assert "Apply to plan" not in _labels(at)
    assert "_room_result" not in at.session_state


def test_find_split_shows_card_chart_and_lookahead() -> None:
    at = _app()
    at.run()
    _click(at, "Find this year's split")

    assert not at.exception
    assert "_room_result" in at.session_state
    assert "Apply to plan" in _labels(at)
    assert "Adjust in Conversion Plan" in _labels(at)
    assert any("Recommended split for 2026" in m.value for m in at.markdown)
    # The tiles and the card's room-after are on different bases; both say so.
    assert any(c.value == TILES_LABEL for c in at.caption)
    assert any(c.value.startswith(f"{ROOM_AFTER_LABEL}:") for c in at.caption)

    from engine.this_year import LookaheadCase, first_rmd_year
    from views._format import fmt_pct

    look = at.session_state["_room_result"].lookahead
    assert look is not None
    assert look.year == first_rmd_year(_hh())
    assert any(f"First RMD year ({look.year})" in m.value for m in at.markdown)
    split_case = look.case(LookaheadCase.SPLIT)
    expected = (
        f"With this split: {fmt_pct(split_case.marginal_bracket, 0)} bracket, "
        f"IRMAA tier {split_case.irmaa_tier}"
    )
    assert any(expected in c.value for c in at.caption)


def test_result_survives_in_place_schedule_migration() -> None:
    """A legacy ``year:strike`` key in the stored schedule is migrated in
    place by effective_schedule(); that must not strand the result's own
    signature, on the run render or the next one."""
    from models.exercise_schedule import ExerciseSchedule
    from models.grants import StockGrant

    schedule = ExerciseSchedule(shares_by_grant_year={"2019:100": {2027: 100}})
    at = _app()
    at.run()
    # Set after the first render so the legacy key is still unmigrated when
    # the Find click's render takes its signature.
    at.session_state["_test_hh"] = {
        "grants": [StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030)],
        "exercise_schedule": schedule,
    }
    _click(at, "Find this year's split")
    assert not at.exception
    assert list(schedule.shares_by_grant_year) == ["2019:100:2030"]  # migrated
    assert "Apply to plan" in _labels(at)
    at.run()
    assert not at.exception
    assert "Apply to plan" in _labels(at)


def test_find_split_uses_actuals_even_with_the_toggle_off(monkeypatch) -> None:
    import views.room_this_year as page
    from models.ytd_income import YTDSnapshot

    calls: list[dict] = []
    real = page.optimize_exercises

    def spy(hh, **kwargs):
        calls.append(kwargs)
        return real(hh, **kwargs)

    monkeypatch.setattr(page, "optimize_exercises", spy)
    at = _app()
    at.session_state["apply_ytd_to_projection"] = False
    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=12_300.0)
    at.run()
    _click(at, "Find this year's split")

    assert not at.exception
    assert calls[-1]["ytd"].nqo_exercise_ytd == 12_300.0


def test_apply_writes_conversions_and_schedule(monkeypatch) -> None:
    import views.room_this_year as page

    saved = []
    monkeypatch.setattr(page, "save_exercise_schedule", lambda schedule: saved.append(schedule))
    at = _app()
    at.session_state["conv_grid_editor"] = {"edited_rows": {}}
    at.session_state["oe_grid_editor"] = {"edited_rows": {}}
    at.session_state["_oe_shares_state"] = {"stale": 1}
    at.session_state["_auto_opt_result"] = "stale panel result"
    at.run()
    _click(at, "Find this year's split")
    result = at.session_state["_room_result"]
    _click(at, "Apply to plan")

    assert not at.exception
    from engine.this_year import apply_split_conversions, apply_split_schedule
    from views.planner import grid_normalized_conversions

    hh = _hh()
    applied = apply_split_conversions(result.current_plan, result.split)
    exp_your, exp_spouse = grid_normalized_conversions(
        hh, applied.your_conversions, applied.spouse_conversions
    )
    assert exp_your.get(2026, 0.0) + exp_spouse.get(2026, 0.0) > 0  # else this passes vacuously
    assert at.session_state["conv_plan_your"] == pytest.approx(exp_your)
    assert at.session_state["conv_plan_spouse"] == pytest.approx(exp_spouse)
    assert len(saved) == 1
    assert saved[0] == apply_split_schedule(hh, result.plan)
    for key in ("conv_grid_editor", "oe_grid_editor", "_oe_shares_state", "_auto_opt_result"):
        assert key not in at.session_state
    assert "_room_result" not in at.session_state
    assert any("applied" in s.value for s in at.success)


def test_result_goes_stale_when_the_plan_changes() -> None:
    at = _app()
    at.run()
    _click(at, "Find this year's split")
    assert "Apply to plan" in _labels(at)

    at.session_state["conv_plan_your"] = {2026: 12_345.0}
    at.run()
    assert not at.exception
    assert "Apply to plan" not in _labels(at)


def test_result_goes_stale_when_actuals_change() -> None:
    from models.ytd_income import YTDSnapshot

    at = _app()
    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026, wages_ytd=10_000.0)
    at.run()
    _click(at, "Find this year's split")
    assert "Apply to plan" in _labels(at)

    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026, wages_ytd=90_000.0)
    at.run()
    assert "Apply to plan" not in _labels(at)


def test_lookahead_beyond_projection_shows_info(monkeypatch) -> None:
    import views.room_this_year as page

    def beyond(*args, **kwargs):
        raise ValueError("first RMD year 2099 is beyond the projection's end")

    monkeypatch.setattr(page, "rmd_lookahead", beyond)
    at = _app()
    at.run()
    _click(at, "Find this year's split")

    assert not at.exception
    assert "Apply to plan" in _labels(at)
    assert any("beyond the projection" in i.value for i in at.info)


@pytest.mark.parametrize("locked", [-32_200.0, None])
def test_page_shows_the_sheltered_caption_only_for_negative_locked(monkeypatch, locked) -> None:
    import dataclasses

    import views.room_this_year as page

    real = page.summarize_this_year

    def with_locked(*args, **kwargs):
        split = real(*args, **kwargs)
        if locked is None:
            return dataclasses.replace(
                split,
                income_stack=dataclasses.replace(
                    split.income_stack, locked=max(split.income_stack.locked, 0.0)
                ),
            )
        return dataclasses.replace(
            split, income_stack=dataclasses.replace(split.income_stack, locked=locked)
        )

    monkeypatch.setattr(page, "summarize_this_year", with_locked)
    at = _app()
    at.run()
    _click(at, "Find this year's split")

    assert not at.exception
    shown = [c.value for c in at.caption if "Deductions exceed other income" in c.value]
    if locked is None:
        assert not shown
    else:
        assert shown == [
            r"Deductions exceed other income by \$32,200, so the first \$32,200 "
            "of the split is untaxed."
        ]


def test_result_survives_a_rerun_with_unchanged_inputs() -> None:
    at = _app()
    at.run()
    _click(at, "Find this year's split")
    at.run()
    at.run()
    assert not at.exception
    assert "Apply to plan" in _labels(at)


@pytest.mark.parametrize(
    ("field", "value"),
    [("your_ira", 900_000), ("stock_price_now", 210.0), ("filing_status", "Single")],
)
def test_result_goes_stale_when_the_household_changes(field, value) -> None:
    """A Profile edit after Find must hide the split, so it cannot be Applied."""
    at = _app()
    at.run()
    _click(at, "Find this year's split")
    assert "Apply to plan" in _labels(at)

    at.session_state["_test_hh"] = {field: value}
    at.run()
    assert not at.exception
    assert "Apply to plan" not in _labels(at)


@pytest.mark.parametrize("toggle", [False, True])
def test_apply_writes_the_year_total_not_total_minus_done(monkeypatch, toggle) -> None:
    """Conversions already done count toward the year: conv_plan_* holds the
    base-year YEAR TOTAL (the engine nets the done part), whatever the toggle
    that grid normalization reads."""
    import views.room_this_year as page
    from models.ytd_income import YTDSnapshot

    monkeypatch.setattr(page, "save_exercise_schedule", lambda schedule: None)
    at = _app()
    at.session_state["apply_ytd_to_projection"] = toggle
    at.session_state["ytd_snapshot"] = YTDSnapshot(
        tax_year=2026, ira_conversions_ytd=30_000.0, spouse_ira_conversions_ytd=20_000.0
    )
    at.run()
    _click(at, "Find this year's split")
    split = at.session_state["_room_result"].split
    _click(at, "Apply to plan")

    assert not at.exception
    totals = {c.person: c for c in split.conversions}
    assert totals["you"].done == 30_000.0
    assert totals["you"].year_total == pytest.approx(totals["you"].done + totals["you"].remaining)
    assert at.session_state["conv_plan_your"].get(2026, 0.0) == pytest.approx(
        totals["you"].year_total
    )
    assert at.session_state["conv_plan_spouse"].get(2026, 0.0) == pytest.approx(
        totals["spouse"].year_total
    )


def test_single_filer_find_and_apply(monkeypatch) -> None:
    import views.room_this_year as page

    saved = []
    monkeypatch.setattr(page, "save_exercise_schedule", lambda schedule: saved.append(schedule))
    at = _app()
    at.session_state["_test_hh"] = {"filing_status": "Single"}
    at.run()
    _click(at, "Find this year's split")
    split = at.session_state["_room_result"].split
    assert [c.person for c in split.conversions] == ["you"]
    _click(at, "Apply to plan")

    assert not at.exception
    assert len(saved) == 1
    assert at.session_state["conv_plan_your"].get(2026, 0.0) == pytest.approx(
        split.conversions[0].year_total
    )
    assert not at.session_state["conv_plan_spouse"].get(2026, 0.0)


def test_no_grants_no_actuals_find_and_apply(monkeypatch) -> None:
    import views.room_this_year as page

    saved = []
    monkeypatch.setattr(page, "save_exercise_schedule", lambda schedule: saved.append(schedule))
    at = _app()
    at.session_state["_test_hh"] = {"grants": []}
    at.run()
    _click(at, "Find this year's split")
    assert at.session_state["_room_result"].split.exercises == ()
    _click(at, "Apply to plan")

    assert not at.exception
    assert len(saved) == 1
    assert not any(any(years.values()) for years in saved[0].shares_by_grant_year.values())
