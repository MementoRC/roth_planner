"""Income Control slice 2 view: stack table, notes, captions. Synthetic data only."""

from __future__ import annotations

from dataclasses import replace

from engine.income_control import IncomeControlYear, StackLine
from tests.test_income_control_view import _result, _year
from views.income_control import chart_layers, line_heights


def _row_with_lines(*, has_actuals: bool) -> IncomeControlYear:
    lines = (
        StackLine("social_security", "Social Security", 0.0, 10_000.0, 10_000.0),
        StackLine("wages", "Wages", 30_000.0, 0.0),
        StackLine("nec", "1099-NEC", 0.0, 0.0),
        StackLine("forced_exercise", "Forced exercise (grant expires)", 0.0, 0.0, 0.0),
        StackLine("discretionary_exercise", "Discretionary exercise", 0.0, 0.0, 0.0),
        StackLine("dividends_qualified", "Qualified dividends", 1_200.0, 0.0, None, True),
        StackLine("conversion", "Planned conversion", 20_000.0, 30_000.0, 40_000.0),
    )
    return replace(_year(2026, top_12=100.0), stack_lines=lines, has_actuals=has_actuals)


def test_chart_layers_use_full_year_values_so_bars_meet_the_limit_lines() -> None:
    result = _result(_row_with_lines(has_actuals=True))
    layers = {name: vals for name, vals, _c in chart_layers(result)}
    row = result.years[0]
    assert layers["Planned conversion"] == [50_000.0]  # done 20,000 + remaining 30,000
    assert sum(vals[0] for vals in layers.values()) == row.total_income
    assert line_heights(result, "top_12") == [row.total_income + 100.0]
