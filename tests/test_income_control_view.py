"""Income Control view: pure helpers (here) and AppTest rendering (Task 9). Synthetic data."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import views.income_control as ic_view
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


def _body(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def _render_page() -> None:
    from models.household import Household
    from views.income_control import render

    render(Household())


def _run_with(result: IncomeControlResult) -> tuple[AppTest, MagicMock]:
    mock_build = MagicMock(return_value=result)
    at = AppTest.from_function(_render_page, default_timeout=60)
    with patch.object(ic_view, "build_income_control", mock_build):
        at.run()
    return at, mock_build


def test_view_renders_with_title_caption_window_and_collapsed_expander() -> None:
    at, _ = _run_with(_result(_year(2026, top_12=100.0), _year(2027, top_12=50.0)))
    assert not at.exception
    assert "Income Control" in at.title[0].value
    captions = " ".join(c.value for c in at.caption)
    assert "Room left under each line after Test plan" in captions
    assert "2026–2027" in captions
    expanders = [e for e in at.expander if e.label == "All years (table)"]
    assert len(expanders) == 1


def test_picker_defaults_to_the_first_crossed_year() -> None:
    at, _ = _run_with(
        _result(_year(2026, top_12=100.0), _year(2027, top_12=-5.0), _year(2028, niit=-1.0))
    )
    assert not at.exception
    assert at.session_state[ic_view.PICKER_KEY] == 2027
    body = _body(at)
    assert "**2027** · age 61 / 56" in body
    assert "**2026** ·" not in body
    assert "**2028** ·" not in body


def test_picker_defaults_to_the_base_year_when_nothing_is_crossed() -> None:
    at, _ = _run_with(_result(_year(2026, top_12=100.0), _year(2027, top_12=50.0)))
    assert not at.exception
    assert at.session_state[ic_view.PICKER_KEY] == 2026


def test_detail_marks_crossed_lines_over_by() -> None:
    at, _ = _run_with(_result(_year(2026, top_12=-250.0, niit=900.0)))
    body = _body(at)
    assert "over by \\$250" in body
    assert "\\$900 left" in body


def test_changing_the_picker_switches_the_detail_panel() -> None:
    result = _result(_year(2026, top_12=100.0), _year(2027, top_12=-5.0), _year(2028, niit=40.0))
    at = AppTest.from_function(_render_page, default_timeout=60)
    with patch.object(ic_view, "build_income_control", MagicMock(return_value=result)):
        at.run()
        assert "**2027** ·" in _body(at)
        at.session_state[ic_view.PICKER_KEY] = 2028
        at.run()
    assert not at.exception
    body = _body(at)
    assert "**2028** · age 62 / 57" in body
    assert "**2027** ·" not in body


def test_picker_falls_back_to_a_radio_without_segmented_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(st, "segmented_control")
    at, _ = _run_with(_result(_year(2026, top_12=100.0), _year(2027, top_12=-5.0)))
    assert not at.exception
    assert len(at.radio) == 1
    assert at.radio[0].key == ic_view.PICKER_KEY
    assert at.radio[0].value == 2027
    assert "**2027** ·" in _body(at)


def test_survivor_year_renders_the_single_age() -> None:
    row = replace(_year(2026, top_12=100.0), ages=(65, None))
    at, _ = _run_with(_result(row))
    assert not at.exception
    assert "**2026** · age 65" in _body(at)


def test_heading_shows_a_dash_when_no_age_is_known() -> None:
    row = replace(_year(2026), ages=(None, None))
    assert ic_view._heading(row) == "**2026** · -"
    assert ic_view._heading(_year(2026)) == "**2026** · age 60 / 55"


def test_empty_window_shows_info_and_no_picker() -> None:
    at, _ = _run_with(IncomeControlResult((), 2026, 2025, "Test plan"))
    assert not at.exception
    assert any("No years in the decision window" in i.value for i in at.info)
    assert ic_view.PICKER_KEY not in at.session_state


def test_absent_planner_state_uses_the_fill_12_label() -> None:
    at, mock_build = _run_with(_result(_year(2026, top_12=100.0)))
    assert not at.exception
    assert mock_build.call_args.kwargs["plan_label"] == "Fill 12%"


def test_view_renders_end_to_end_with_the_real_engine() -> None:
    at = AppTest.from_function(_render_page, default_timeout=60)
    at.run()
    assert not at.exception
    assert ic_view.PICKER_KEY in at.session_state
    assert f"**{at.session_state[ic_view.PICKER_KEY]}** ·" in _body(at)
