"""Income Control slice 3 view: 0% LTCG harvest column, detail line, notes. Synthetic data."""

from __future__ import annotations

from dataclasses import replace

from engine.income_control import IncomeControlYear
from engine.room_lines import HarvestSizing
from tests.test_income_control_view import _body, _result, _run_with, _year
from views._format import md_dollars
from views.income_control import (
    HARVEST_COLUMN,
    HARVEST_ENTER_BASIS,
    HARVEST_NOT_ADDITIVE,
    HARVEST_USED_BY_CONVERSION,
    harvest_notes,
    harvest_text,
    table_rows,
)

_KNOWN = HarvestSizing(12_345.0, "0% LTCG", 12_345.0, True)
_ZERO = HarvestSizing(0.0, "0% LTCG", 0.0, True)


def _with_harvest(harvest: HarvestSizing, **rooms: float) -> IncomeControlYear:
    return replace(_year(2026, **rooms), harvest=harvest)


def _captions(rows: tuple[IncomeControlYear, ...]) -> str:
    at, _ = _run_with(_result(*rows))
    assert not at.exception
    return " ".join(c.value for c in at.caption)


def test_harvest_view_column_follows_the_room_columns() -> None:
    entry = table_rows(_result(_with_harvest(_KNOWN, ltcg_zero=50_000.0, niit=90_000.0)))[0]
    keys = list(entry)
    assert entry[HARVEST_COLUMN] == "$12,345"
    assert keys.index(HARVEST_COLUMN) == keys.index("NIIT threshold") + 1
    assert keys.index("Binding line") == keys.index(HARVEST_COLUMN) + 1


def test_harvest_view_rows_without_a_harvest_show_a_dash() -> None:
    assert table_rows(_result(_year(2026, niit=1_000.0)))[0][HARVEST_COLUMN] == "-"


def test_harvest_text_names_the_amount_and_the_binding() -> None:
    assert harvest_text(_KNOWN) == "0% LTCG harvest: up to $12,345 (limited by 0% LTCG)"


def test_harvest_view_detail_line_is_rendered() -> None:
    at, _ = _run_with(_result(_with_harvest(_KNOWN, ltcg_zero=50_000.0, niit=90_000.0)))
    assert not at.exception
    assert md_dollars(harvest_text(_KNOWN)) in _body(at)


def test_harvest_view_conversion_used_the_room_note() -> None:
    row = _with_harvest(_ZERO, ltcg_zero=0.0, niit=90_000.0)  # _year: conversion 20,000
    assert harvest_notes(row) == [HARVEST_USED_BY_CONVERSION, HARVEST_NOT_ADDITIVE]
    captions = _captions((row,))
    assert md_dollars(HARVEST_USED_BY_CONVERSION) in captions
    assert HARVEST_NOT_ADDITIVE in captions


def test_harvest_view_no_conversion_note_without_a_conversion_or_with_room_left() -> None:
    no_conversion = replace(_with_harvest(_ZERO, ltcg_zero=0.0), planned_conversion=0.0)
    assert harvest_notes(no_conversion) == [HARVEST_NOT_ADDITIVE]
    room_left = _with_harvest(_ZERO, ltcg_zero=5_000.0)  # $0 for another reason
    assert harvest_notes(room_left) == [HARVEST_NOT_ADDITIVE]


def test_harvest_view_basis_unknown_note() -> None:
    row = _with_harvest(
        HarvestSizing(30_000.0, "0% LTCG", 30_000.0, False), ltcg_zero=30_000.0, niit=90_000.0
    )
    assert harvest_notes(row) == [HARVEST_ENTER_BASIS, HARVEST_NOT_ADDITIVE]
    assert HARVEST_ENTER_BASIS in _captions((row,))


def test_harvest_view_rows_without_a_harvest_have_no_line_and_no_notes() -> None:
    row = _year(2026, niit=1_000.0)
    assert harvest_notes(row) == []
    at, _ = _run_with(_result(row))
    assert not at.exception
    assert "0% LTCG harvest" not in _body(at)
    assert HARVEST_NOT_ADDITIVE not in " ".join(c.value for c in at.caption)
