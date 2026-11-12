"""Income Control view: pure helpers (here) and AppTest rendering (Task 9). Synthetic data."""

from __future__ import annotations

from engine.income_control import (
    IncomeControlResult,
    IncomeControlYear,
    crossed_lines,
    tightest_line,
)
from engine.room_lines import LINE_KEYS
from engine.scenario_types import YearResult
from views.income_control import (
    chart_layers,
    default_year,
    line_heights,
    table_rows,
    tightest_first,
)


def _year(
    year: int,
    *,
    forced: float = 0.0,
    disc: float = 0.0,
    conv: float = 20_000.0,
    **rooms: float,
) -> IncomeControlYear:
    full: dict[str, float | None] = dict.fromkeys(LINE_KEYS)
    full.update(rooms)
    yr = YearResult(
        year=year,
        your_age=60 + year - 2026,
        spouse_age=55 + year - 2026,
        phase="clean",
        filing_status="MFJ",
    )
    return IncomeControlYear(
        year=year,
        ages=(yr.your_age, yr.spouse_age),
        social_security=10_000.0,
        rmd=5_000.0,
        dividends_qualified=1_000.0,
        dividends_ordinary=500.0,
        interest=0.0,
        forced_exercise=forced,
        discretionary_exercise=disc,
        planned_conversion=conv,
        rooms=full,
        binding_line=tightest_line(full),
        crossed_lines=crossed_lines(full),
        year_result=yr,
    )


def _result(*years: IncomeControlYear) -> IncomeControlResult:
    return IncomeControlResult(years, 2026, 2026 + len(years) - 1, "Test plan")


def test_default_year_is_first_year_with_a_crossed_line() -> None:
    result = _result(_year(2026, top_12=100.0), _year(2027, top_12=-5.0), _year(2028, niit=-1.0))
    assert default_year(result) == 2027


def test_default_year_is_base_year_when_nothing_is_crossed() -> None:
    result = _result(_year(2026, top_12=100.0), _year(2027, top_12=50.0))
    assert default_year(result) == 2026


def test_tightest_first_lists_crossed_lines_before_open_ones() -> None:
    row = _year(2026, top_12=300.0, niit=-40.0, irmaa_t1=10.0)
    assert tightest_first(row) == [("niit", -40.0), ("irmaa_t1", 10.0), ("top_12", 300.0)]


def test_chart_layers_omit_forced_when_there_are_no_grants() -> None:
    result = _result(_year(2026), _year(2027))
    names = [name for name, _vals, _color in chart_layers(result)]
    assert names == ["Fixed income", "Discretionary exercise", "Planned conversion"]


def test_chart_layers_include_forced_when_any_year_has_it() -> None:
    result = _result(_year(2026), _year(2027, forced=60_000.0, disc=10_000.0))
    layers = {name: vals for name, vals, _c in chart_layers(result)}
    assert layers["Forced exercise"] == [0.0, 60_000.0]
    assert layers["Discretionary exercise"] == [0.0, 10_000.0]
    assert layers["Fixed income"] == [16_500.0, 16_500.0]
    assert layers["Planned conversion"] == [20_000.0, 20_000.0]


def test_line_heights_are_bar_total_plus_room_and_none_when_not_applicable() -> None:
    result = _result(_year(2026, top_12=1_000.0), _year(2027))
    # bar total = 16_500 fixed + 20_000 conversion = 36_500
    assert line_heights(result, "top_12") == [37_500.0, None]


def test_table_rows_has_one_row_per_year_with_binding_line_name() -> None:
    result = _result(_year(2026, top_12=1_000.0, niit=500.0))
    rows = table_rows(result)
    assert len(rows) == 1
    assert rows[0]["Year"] == 2026
    assert rows[0]["Binding line"] == "NIIT threshold"
    assert rows[0]["Ages"] == "60 / 55"
