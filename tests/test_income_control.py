"""Income Control engine (slice 1). Synthetic data only."""

from __future__ import annotations

import pytest

from engine.income_control import (
    build_income_control,
    crossed_lines,
    forced_exercise,
    tightest_line,
    window_bounds,
)
from engine.room_lines import LINE_KEYS, room_after, room_lines_for_year
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from engine.this_year import first_rmd_year
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household, SurvivorScenario


def _hh(**kw) -> Household:
    base = {
        "base_year": 2026,
        "your_age": 61,
        "spouse_age": 58,
        "filing_status": "MFJ",
        "your_ira": 800_000.0,
        "spouse_ira": 600_000.0,
        "cpi_assumption": 0.025,
        "stock_price_now": 150.0,
        "grants": [],
    }
    base.update(kw)
    return Household(**base)


def _grant(grant_id: str, shares: int, expiry: int, strike: float = 100.0) -> StockGrant:
    return StockGrant(
        year=2020, strike=strike, shares=shares, expiry_year=expiry, grant_id=grant_id
    )


def test_window_without_grants_ends_at_first_rmd_year() -> None:
    hh = _hh()
    assert window_bounds(hh) == (2026, first_rmd_year(hh))


def test_window_extends_to_a_grant_expiring_after_the_first_rmd_year() -> None:
    hh0 = _hh()
    late = first_rmd_year(hh0) + 3
    hh = _hh(grants=[_grant("g1", 100, 2030), _grant("g2", 100, late)])
    assert window_bounds(hh) == (2026, late)


def test_window_uses_first_rmd_year_when_grants_expire_earlier() -> None:
    hh = _hh(grants=[_grant("g1", 100, 2030)])
    assert window_bounds(hh) == (2026, first_rmd_year(hh))


def test_window_can_be_empty_when_end_precedes_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import engine.income_control as ic

    monkeypatch.setattr(ic, "first_rmd_year", lambda hh: 2025)
    assert ic.window_bounds(_hh()) == (2026, 2025)


def test_window_end_is_survivor_aware_when_you_die_before_your_rmd_age() -> None:
    hh = _hh(your_age=70, spouse_age=60, survivor=SurvivorScenario("you", 2027))
    deferral = 1 if hh.spouse_defer_first_rmd else 0
    survivor_first = hh.base_year + (hh.spouse_rmd_start_age - 60) + deferral
    assert window_bounds(hh) == (2026, survivor_first)


def test_first_rmd_year_keeps_deceased_rmd_when_death_follows_it() -> None:
    plain = _hh(your_age=70, spouse_age=60)
    late_death = _hh(your_age=70, spouse_age=60, survivor=SurvivorScenario("you", 2060))
    assert first_rmd_year(late_death) == first_rmd_year(plain)


def test_window_covers_a_grant_expiring_past_the_default_scenario_horizon() -> None:
    hh = _hh(your_age=90, spouse_age=88, grants=[_grant("g1", 100, 2036)])
    result = build_income_control(hh, ConversionPlan(), plan_label="x")
    assert result.window_end == 2036
    assert [row.year for row in result.years] == list(range(2026, 2037))


def test_fixed_income_includes_forced_ira_and_inherited_distributions() -> None:
    from engine import income_control as ic
    from engine.scenario_types import YearResult

    hh = _hh()
    yr = YearResult(
        year=2026,
        your_age=61,
        spouse_age=58,
        phase="clean",
        filing_status="MFJ",
        forced_your_ira_draw=10_000.0,
        forced_spouse_ira_draw=2_000.0,
        your_inherited_distribution=3_000.0,
        spouse_inherited_distribution=500.0,
    )
    row = ic._row(hh, yr)
    assert row.ira_draws == 15_500.0
    assert row.rmd == 0.0
    assert row.fixed_income == 15_500.0


def _schedule_hh() -> Household:
    """g1 (1000 sh, expires 2028): 400 in 2027, 600 in 2028.
    g2 (500 sh, expires 2031): 100 in 2028. Spread = 200 - 100 = 100/share."""
    sched = ExerciseSchedule(
        shares_by_grant_year={"g1": {2027: 400, 2028: 600}, "g2": {2028: 100}},
        price_by_year=dict.fromkeys(range(2026, 2033), 200.0),
    )
    return _hh(
        grants=[_grant("g1", 1000, 2028), _grant("g2", 500, 2031)],
        exercise_schedule=sched,
    )


def test_forced_exercise_counts_only_the_expiring_grant_in_its_expiry_year() -> None:
    hh = _schedule_hh()
    assert forced_exercise(hh, 2027, hh.option_income(2027)) == 0.0  # g1 exercised early
    assert forced_exercise(hh, 2028, hh.option_income(2028)) == 60_000.0  # g1's last 600 sh
    assert hh.option_income(2028) == 70_000.0  # plus g2's 100 sh, which is discretionary


def test_forced_exercise_is_zero_without_grants() -> None:
    hh = _hh()
    assert forced_exercise(hh, 2028, 0.0) == 0.0


def test_forced_exercise_is_clamped_to_option_income() -> None:
    hh = _schedule_hh()
    assert forced_exercise(hh, 2028, 1_000.0) == 1_000.0  # forced 60k cannot exceed the year's 1k
    assert forced_exercise(hh, 2028, 0.0) == 0.0


def _rooms(**kw: float) -> dict[str, float | None]:
    rooms: dict[str, float | None] = dict.fromkeys(LINE_KEYS)
    rooms.update(kw)
    return rooms


def test_tightest_line_is_smallest_non_negative_room() -> None:
    assert (
        tightest_line(_rooms(top_12=5_000, top_22=5_000, irmaa_t1=2_000, niit=-100)) == "irmaa_t1"
    )


def test_tightest_line_breaks_ties_by_line_key_order() -> None:
    assert tightest_line(_rooms(top_24=3_000, top_12=3_000)) == "top_12"


def test_zero_room_counts_as_binding_and_none_is_ignored() -> None:
    assert tightest_line(_rooms(top_12=0.0, niit=10.0)) == "top_12"


def test_tightest_line_is_none_when_all_crossed_or_not_applicable() -> None:
    assert tightest_line(_rooms(top_12=-1.0, niit=-2.0)) is None
    assert tightest_line(_rooms()) is None


def test_crossed_lines_most_negative_first_ties_by_key_order() -> None:
    rooms = _rooms(niit=-100.0, ltcg_zero=-5_000.0, top_12=-100.0, top_22=50.0)
    assert crossed_lines(rooms) == ("ltcg_zero", "top_12", "niit")


def test_one_model_guard_rows_equal_the_single_run_scenario_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import engine.income_control as ic

    hh = _schedule_hh()
    plan = ConversionPlan(your_conversions={2027: 40_000.0, 2028: 25_000.0})
    calls: list[int] = []
    real = ic.run_scenario

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(ic, "run_scenario", counting)
    result = build_income_control(hh, plan, plan_label="Test plan")
    assert len(calls) == 1
    assert result.plan_label == "Test plan"

    expected = {yr.year: yr for yr in run_scenario(hh, plan, "Test plan").years}
    assert [row.year for row in result.years] == list(
        range(result.window_start, result.window_end + 1)
    )
    for row in result.years:
        assert row.year_result == expected[row.year]
        assert row.planned_conversion == (
            expected[row.year].your_conversion + expected[row.year].spouse_conversion
        )


def test_rows_split_option_income_into_forced_and_discretionary() -> None:
    hh = _schedule_hh()
    result = build_income_control(hh, ConversionPlan(), plan_label="x")
    by_year = {row.year: row for row in result.years}
    assert (by_year[2027].forced_exercise, by_year[2027].discretionary_exercise) == (0.0, 40_000.0)
    assert (by_year[2028].forced_exercise, by_year[2028].discretionary_exercise) == (
        60_000.0,
        10_000.0,
    )
    for row in result.years:
        assert row.forced_exercise + row.discretionary_exercise == pytest.approx(
            row.year_result.option_income
        )


def test_row_fields_come_from_the_year_result() -> None:
    hh = _hh()
    result = build_income_control(hh, ConversionPlan(), plan_label="x")
    row = result.years[0]
    yr = row.year_result
    assert row.social_security == yr.combined_ss
    assert row.rmd == yr.taxable_rmd + yr.spouse_taxable_rmd
    assert row.dividends_qualified == yr.brokerage_qual_div
    assert row.dividends_ordinary == yr.brokerage_ord_div
    assert row.interest == 0.0
    assert row.ages == (yr.your_age, yr.spouse_age)
    assert row.fixed_income == (
        row.social_security
        + row.rmd
        + row.ira_draws
        + row.dividends_qualified
        + row.dividends_ordinary
        + row.interest
    )
    assert set(row.rooms) == set(LINE_KEYS)


def _assert_rows_wired(hh: Household, plan: ConversionPlan) -> list:
    result = build_income_control(hh, plan, plan_label="x")
    by_year = {yr.year: yr for yr in run_scenario(hh, plan, "x").years}
    for row in result.years:
        yr = by_year[row.year]
        assert row.rooms == room_after(yr, room_lines_for_year(hh, yr))
        assert row.binding_line == tightest_line(row.rooms)
        assert row.crossed_lines == crossed_lines(row.rooms)
    return list(result.years)


def test_row_rooms_binding_and_crossed_are_wired_from_the_matching_year_result() -> None:
    hh = _schedule_hh()
    rows = _assert_rows_wired(hh, ConversionPlan(your_conversions={2027: 40_000.0}))
    assert any(row.binding_line is not None for row in rows)


def test_row_wiring_with_crossed_lines() -> None:
    hh = _schedule_hh()
    rows = _assert_rows_wired(hh, ConversionPlan(your_conversions={2027: 900_000.0}))
    by_year = {row.year: row for row in rows}
    assert by_year[2027].crossed_lines
    assert by_year[2027].binding_line != by_year[2027].crossed_lines[0]


def test_total_income_sums_fixed_exercise_and_conversion() -> None:
    hh = _schedule_hh()
    result = build_income_control(
        hh, ConversionPlan(your_conversions={2028: 25_000.0}), plan_label="x"
    )
    row = {r.year: r for r in result.years}[2028]
    assert row.planned_conversion == 25_000.0
    assert row.forced_exercise == 60_000.0
    assert row.total_income == pytest.approx(row.fixed_income + 60_000.0 + 10_000.0 + 25_000.0)


def test_forced_exercise_is_clamped_below_at_zero() -> None:
    # income_for already floors underwater grants at 0, so the lower clamp is
    # only reachable through a negative option_income argument.
    hh = _schedule_hh()
    assert forced_exercise(hh, 2028, -5_000.0) == 0.0


def test_empty_window_gives_no_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    import engine.income_control as ic

    monkeypatch.setattr(ic, "first_rmd_year", lambda hh: 2025)
    result = build_income_control(_hh(), ConversionPlan(), plan_label="x")
    assert result.years == ()
    assert (result.window_start, result.window_end) == (2026, 2025)


def test_single_household_has_no_spouse_age() -> None:
    hh = _hh(filing_status="Single", your_age=63)
    result = build_income_control(hh, ConversionPlan(), plan_label="x")
    assert result.years
    assert all(row.ages[1] is None for row in result.years)


def test_engine_does_not_zero_ages_in_survivor_years() -> None:
    """Characterization (plan finding): survivor years flip filing_status only."""
    hh = _hh(your_age=70, spouse_age=66, survivor=SurvivorScenario("spouse", 2027))
    years = {yr.year: yr for yr in run_scenario(hh, ConversionPlan(), "x").years}
    assert years[2029].filing_status == "Single"
    assert years[2029].spouse_age == 66 + 3
    assert years[2029].your_age == 70 + 3


def test_survivor_rows_set_the_deceased_slot_to_none() -> None:
    spouse_dies = _hh(your_age=70, spouse_age=66, survivor=SurvivorScenario("spouse", 2027))
    result = build_income_control(spouse_dies, ConversionPlan(), plan_label="x")
    by_year = {row.year: row for row in result.years}
    assert by_year[2027].ages == (71, 67)  # death year still files MFJ
    assert by_year[2029].ages == (73, None)

    you_die = _hh(your_age=70, spouse_age=66, survivor=SurvivorScenario("you", 2027))
    result = build_income_control(you_die, ConversionPlan(), plan_label="x")
    by_year = {row.year: row for row in result.years}
    assert by_year[2029].ages == (None, 69)  # you died; the spouse keeps slot 1
