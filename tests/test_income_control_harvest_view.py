"""Income Control slice 3 view: 0% LTCG harvest column, detail line, notes. Synthetic data."""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.income_control import IncomeControlYear
from engine.room_lines import HARVEST_BINDINGS, HarvestSizing
from tests.test_income_control_view import _body, _result, _run_with, _year
from views._format import md_dollars
from views.income_control import (
    HARVEST_COLUMN,
    HARVEST_ENTER_BASIS,
    HARVEST_NOT_ADDITIVE,
    HARVEST_PHRASES,
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
    assert harvest_text(_KNOWN) == (
        "0% LTCG harvest: up to $12,345 — stops at the top of the 0% LTCG bracket"
    )


def test_harvest_text_every_engine_binding_has_a_phrase() -> None:
    assert set(HARVEST_BINDINGS) <= set(HARVEST_PHRASES)


@pytest.mark.parametrize("binding", sorted(HARVEST_BINDINGS))
def test_harvest_text_uses_the_phrase_for_each_binding(binding: str) -> None:
    text = harvest_text(HarvestSizing(1_000.0, binding, 1_000.0, True))
    assert text == f"0% LTCG harvest: up to $1,000 — stops at {HARVEST_PHRASES[binding]}"


def test_harvest_text_unknown_binding_falls_back_to_the_raw_label() -> None:
    text = harvest_text(HarvestSizing(1_000.0, "mystery", 1_000.0, True))
    assert text.endswith("stops at mystery")


def test_harvest_view_detail_line_is_rendered() -> None:
    at, _ = _run_with(_result(_with_harvest(_KNOWN, ltcg_zero=50_000.0, niit=90_000.0)))
    assert not at.exception
    assert md_dollars(harvest_text(_KNOWN)) in _body(at)


def _conv_row(
    room: float, before: float | None, harvest: HarvestSizing = _ZERO
) -> IncomeControlYear:
    """``before``: the engine's 0% LTCG room with the conversion removed (None: no conversion)."""
    return replace(_with_harvest(harvest, ltcg_zero=room), ltcg_room_without_conversion=before)


def test_harvest_view_conversion_used_the_room_note() -> None:
    row = replace(
        _with_harvest(_ZERO, ltcg_zero=0.0, niit=90_000.0), ltcg_room_without_conversion=20_000.0
    )
    assert harvest_notes(row) == [HARVEST_USED_BY_CONVERSION]
    assert md_dollars(HARVEST_USED_BY_CONVERSION) in _captions((row,))


@pytest.mark.parametrize(
    ("room", "before", "fires"),
    [
        (0.0, 10_000.0, True),
        (-500.0, 9_500.0, True),  # overshoot, but the conversion still used real room
        (-20_000.0, -10_000.0, False),  # fixed income had already filled the bracket
        (0.0, None, False),  # no conversion
        (5_000.0, 15_000.0, False),  # room left: $0 for another reason
        (0.99, 10_000.99, True),
        (1.01, 10_001.01, False),
        (-320.0, 400.0, True),  # the conversion cost more room than its face amount
    ],
)
def test_harvest_view_conversion_note_only_when_the_conversion_used_room(
    room: float, before: float | None, fires: bool
) -> None:
    notes = harvest_notes(_conv_row(room, before))
    assert (HARVEST_USED_BY_CONVERSION in notes) is fires


def test_harvest_view_conversion_note_boundary_of_room_without_conversion() -> None:
    assert HARVEST_USED_BY_CONVERSION in harvest_notes(_conv_row(-9_998.99, 1.01))
    assert HARVEST_USED_BY_CONVERSION not in harvest_notes(_conv_row(-9_999.0, 1.0))


def test_harvest_view_no_conversion_note_when_a_harvest_is_possible() -> None:
    assert harvest_notes(_conv_row(0.0, 10_000.0, _KNOWN)) == []


def test_harvest_view_basis_unknown_note() -> None:
    harvest = HarvestSizing(30_000.0, "embedded gain", 30_000.0, False)
    row = _with_harvest(harvest, ltcg_zero=30_000.0, niit=90_000.0)
    assert harvest_notes(row) == [HARVEST_ENTER_BASIS]
    assert harvest_text(harvest) == (
        "0% LTCG harvest: up to $30,000 — stops at your unrealized gain in the brokerage account"
    )
    assert HARVEST_ENTER_BASIS in _captions((row,))
    assert md_dollars(harvest_text(harvest)) in _body(_run_with(_result(row))[0])


def test_harvest_view_not_additive_caption_is_shown_once_under_the_table() -> None:
    rows = (_with_harvest(_KNOWN, ltcg_zero=50_000.0), _with_harvest(_ZERO, ltcg_zero=0.0))
    at, _ = _run_with(_result(*rows))
    assert not at.exception
    assert [c.value for c in at.caption].count(HARVEST_NOT_ADDITIVE) == 1
    assert HARVEST_NOT_ADDITIVE not in " ".join(harvest_notes(rows[0]) + harvest_notes(rows[1]))


def test_harvest_view_rows_without_a_harvest_have_no_line_and_no_notes() -> None:
    row = _year(2026, niit=1_000.0)
    assert harvest_notes(row) == []
    at, _ = _run_with(_result(row))
    assert not at.exception
    assert "0% LTCG harvest" not in _body(at)
    assert HARVEST_NOT_ADDITIVE not in " ".join(c.value for c in at.caption)
