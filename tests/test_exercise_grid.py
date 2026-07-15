"""Unit tests for engine.exercise_grid.normalize_grid_edits — pure grid logic
extracted from the Option Exercise Planner view."""

from __future__ import annotations

from engine.exercise_grid import normalize_grid_edits
from models.grants import StockGrant

GRANT_2019 = StockGrant(2019, 104.0, 650, 2029)
GRANT_2020 = StockGrant(2020, 130.0, 1000, 2030)
YEARS = list(range(2026, 2032))


def test_entry_past_expiry_is_rejected_and_excluded() -> None:
    raw = {GRANT_2019.key(): {2030: 100}}
    norm = normalize_grid_edits([GRANT_2019], YEARS, raw)

    assert norm.out_of_range == [(GRANT_2019, 2030, 100)]
    assert norm.shares_by_key[GRANT_2019.key()] == {}
    assert norm.remaining_by_key[GRANT_2019.key()] == GRANT_2019.shares


def test_in_range_entry_counts_toward_scheduled_and_remaining() -> None:
    raw = {GRANT_2019.key(): {2028: 200, 2029: 50}}
    norm = normalize_grid_edits([GRANT_2019], YEARS, raw)

    assert norm.shares_by_key[GRANT_2019.key()] == {2028: 200, 2029: 50}
    assert norm.remaining_by_key[GRANT_2019.key()] == GRANT_2019.shares - 250
    assert norm.out_of_range == []


def test_non_positive_and_zero_cells_are_dropped() -> None:
    raw = {GRANT_2019.key(): {2026: 0, 2027: -10, 2028: 100}}
    norm = normalize_grid_edits([GRANT_2019], YEARS, raw)

    assert norm.shares_by_key[GRANT_2019.key()] == {2028: 100}
    assert 2026 not in norm.shares_by_key[GRANT_2019.key()]
    assert 2027 not in norm.shares_by_key[GRANT_2019.key()]


def test_remaining_equals_shares_minus_scheduled_sum() -> None:
    raw = {GRANT_2020.key(): {2026: 100, 2027: 200, 2028: 300}}
    norm = normalize_grid_edits([GRANT_2020], YEARS, raw)

    scheduled = sum(norm.shares_by_key[GRANT_2020.key()].values())
    assert norm.remaining_by_key[GRANT_2020.key()] == GRANT_2020.shares - scheduled
    assert norm.remaining_by_key[GRANT_2020.key()] == 1000 - 600


def test_multiple_grants_enforce_independent_expiry_bounds() -> None:
    raw = {
        GRANT_2019.key(): {2029: 650, 2030: 999},  # 2030 is past 2019's expiry
        GRANT_2020.key(): {2029: 500, 2030: 500},  # both in-range for 2020's expiry
    }
    norm = normalize_grid_edits([GRANT_2019, GRANT_2020], YEARS, raw)

    assert norm.shares_by_key[GRANT_2019.key()] == {2029: 650}
    assert norm.out_of_range == [(GRANT_2019, 2030, 999)]
    assert norm.remaining_by_key[GRANT_2019.key()] == 0

    assert norm.shares_by_key[GRANT_2020.key()] == {2029: 500, 2030: 500}
    assert norm.remaining_by_key[GRANT_2020.key()] == 0


def test_missing_grant_key_yields_empty_schedule() -> None:
    norm = normalize_grid_edits([GRANT_2019], YEARS, raw_by_key={})

    assert norm.shares_by_key[GRANT_2019.key()] == {}
    assert norm.remaining_by_key[GRANT_2019.key()] == GRANT_2019.shares
    assert norm.out_of_range == []
