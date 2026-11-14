"""Income Control slice 2 view: stack table, notes, captions. Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pandas as pd
import pandas.io.formats
import pytest
from streamlit.testing.v1 import AppTest

import views.income_control as ic_view
from engine.income_control import IncomeControlResult, IncomeControlYear, StackLine
from models.ytd_income import YTDSnapshot
from tests.test_income_control_view import _render_page, _result, _year
from views.income_control import (
    ABOVE_PLAN_COLOR,
    MAGI_ONLY_COLOR,
    TOGGLE_CAPTION,
    chart_layers,
    line_heights,
    stack_table,
    styled_stack,
)


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
    # fixed layer = SS 10,000 + wages 30,000 + qualified dividends 1,200
    assert layers["Fixed income"] == [41_200.0]
    assert sum(vals[0] for vals in layers.values()) == row.total_income
    assert line_heights(result, "top_12") == [row.total_income + 100.0]


_PLAIN = ["Line", "Full year"]  # forecast-only rows: no Note column (matches today)


def test_stack_table_with_actuals_has_so_far_remaining_full_year_and_notes() -> None:
    table = stack_table(_row_with_lines(has_actuals=True), with_actuals=True)
    assert list(table.columns) == ["Line", "So far", "Remaining", "Full year", "Note"]
    by_line = table.set_index("Line")
    assert by_line.loc["Wages", "So far"] == "$30,000"
    assert by_line.loc["Planned conversion", "Remaining"] == "$30,000"
    assert by_line.loc["Planned conversion", "Full year"] == "$50,000"
    assert by_line.loc["Planned conversion", "Note"] == "$10,000 above plan"
    assert by_line.loc["Qualified dividends", "Note"] == "MAGI-only"
    assert by_line.loc["Social Security", "Note"] == ""


def test_stack_table_hides_zero_actual_only_and_zero_optional_lines() -> None:
    lines = set(stack_table(_row_with_lines(has_actuals=True), with_actuals=True)["Line"])
    assert "1099-NEC" not in lines  # actual-only, done == 0
    assert "Forced exercise (grant expires)" not in lines  # optional, zero
    assert {"Social Security", "Discretionary exercise", "Planned conversion"} <= lines


def test_stack_table_without_actuals_has_only_line_and_full_year() -> None:
    table = stack_table(_row_with_lines(has_actuals=False), with_actuals=False)
    assert list(table.columns) == _PLAIN
    assert "Wages" in set(table["Line"])  # fixture rows are shown as given


def test_styled_stack_colours_the_note_column() -> None:
    table = stack_table(_row_with_lines(has_actuals=True), with_actuals=True)
    html = styled_stack(table).to_html()
    assert MAGI_ONLY_COLOR in html
    assert ABOVE_PLAN_COLOR in html


def test_view_imports_and_falls_back_without_jinja2(monkeypatch: pytest.MonkeyPatch) -> None:
    # Importing the view must not need jinja2 (pandas Styler imports it at module level).
    table = stack_table(_row_with_lines(has_actuals=True), with_actuals=True)
    monkeypatch.setitem(sys.modules, "jinja2", None)
    for name in ("pandas.io.formats.style", "pandas.io.formats.style_render"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delattr(pandas.io.formats, "style", raising=False)
    spec = importlib.util.spec_from_file_location("_income_control_no_jinja2", ic_view.__file__)
    assert spec is not None
    assert spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)  # raises ImportError if the module needs jinja2 at import
    assert fresh.styled_stack(table) is table


def test_display_fixed_income_follows_actuals() -> None:
    with_actuals = _row_with_lines(has_actuals=True)
    without = _row_with_lines(has_actuals=False)
    assert with_actuals.display_fixed_income == with_actuals.stack_fixed_income == 41_200.0
    assert without.display_fixed_income == without.fixed_income
    assert without.stack_fixed_income != without.fixed_income


def test_styled_stack_without_a_note_column_returns_the_plain_style() -> None:
    table = stack_table(_row_with_lines(has_actuals=False), with_actuals=False)
    assert styled_stack(table) is table


@pytest.mark.parametrize("exc", [ImportError, AttributeError])
def test_styled_stack_falls_back_to_the_plain_table_when_styling_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, exc: type[Exception]
) -> None:
    # pandas Styler needs jinja2; if it is missing show the plain table.
    table = stack_table(_row_with_lines(has_actuals=True), with_actuals=True)

    def boom(self: pd.DataFrame) -> None:
        raise exc("jinja2")

    monkeypatch.setattr(pd.DataFrame, "style", property(boom))
    assert styled_stack(table) is table


def _run(
    result: IncomeControlResult,
    *,
    toggle_on: bool,
    snapshot_date: str = "2026-06-30",
    with_snapshot: bool = True,
) -> AppTest:
    at = AppTest.from_function(_render_page, default_timeout=60)
    if toggle_on:
        at.session_state["apply_ytd_to_projection"] = True
        if with_snapshot:
            at.session_state["ytd_snapshot"] = YTDSnapshot(
                tax_year=2026, snapshot_date=snapshot_date
            )
    with patch.object(ic_view, "build_income_control", MagicMock(return_value=result)):
        at.run()
    return at


def _stack_frame(at: AppTest) -> pd.DataFrame:
    frames = [d.value for d in at.dataframe if "Full year" in d.value.columns]
    assert frames, "stack dataframe not rendered"
    return frames[0]


def _captions(at: AppTest) -> str:
    return " ".join(c.value for c in at.caption)


def test_toggle_on_renders_so_far_and_remaining_and_the_snapshot_date_caption() -> None:
    at = _run(_result(_row_with_lines(has_actuals=True)), toggle_on=True)
    assert not at.exception
    frame = _stack_frame(at)
    assert list(frame.columns) == ["Line", "So far", "Remaining", "Full year", "Note"]
    assert "1099-NEC" not in set(frame["Line"])
    assert {"Wages", "Planned conversion"} <= set(frame["Line"])
    assert "So far = actuals as of 2026-06-30." in _captions(at)
    assert "Turn on Include this year's actuals" not in _captions(at)


def test_empty_snapshot_date_uses_the_to_date_caption() -> None:
    at = _run(_result(_row_with_lines(has_actuals=True)), toggle_on=True, snapshot_date="")
    assert "So far = actuals to date." in _captions(at)


def test_toggle_off_renders_full_year_only_and_points_to_the_toggle() -> None:
    at = _run(_result(_row_with_lines(has_actuals=False)), toggle_on=False)
    assert not at.exception
    assert list(_stack_frame(at).columns) == _PLAIN
    assert TOGGLE_CAPTION in _captions(at)
    assert "Of which MAGI-only" not in _captions(at)  # shown only with actuals


def test_toggle_on_without_a_snapshot_behaves_like_toggle_off() -> None:
    at = _run(_result(_row_with_lines(has_actuals=False)), toggle_on=True, with_snapshot=False)
    assert not at.exception
    assert list(_stack_frame(at).columns) == _PLAIN
    assert TOGGLE_CAPTION in _captions(at)


def test_non_base_year_gets_no_toggle_caption() -> None:
    result = _result(
        _year(2026, top_12=100.0), replace(_row_with_lines(has_actuals=False), year=2027)
    )
    at = AppTest.from_function(_render_page, default_timeout=60)
    at.session_state[ic_view.PICKER_KEY] = 2027
    with patch.object(ic_view, "build_income_control", MagicMock(return_value=result)):
        at.run()
    assert not at.exception
    assert "Turn on Include this year's actuals" not in _captions(at)


def test_all_years_table_shows_total_income_and_full_year_figures_with_actuals() -> None:
    row = _row_with_lines(has_actuals=True)
    entry = ic_view.table_rows(_result(row))[0]
    assert entry["Total income"] == "$91,200"  # 10,000 + 30,000 + 1,200 + 50,000
    assert entry["Fixed income"] == "$41,200"  # not the total
    assert entry["Planned conversion"] == "$50,000"


def test_magi_only_subtotal_caption_is_shown_when_present() -> None:
    at = _run(_result(_row_with_lines(has_actuals=True)), toggle_on=True)
    assert "Of which MAGI-only" in _captions(at)
