"""RED gate for audit-0823 `engine/GRID-KEY-COLLISION`.

`StockGrant.key()` is content-based (``grant_id`` else ``year:strike:expiry_year``),
so two grants with an empty ``grant_id`` that share year+strike+expiry legitimately
collide -- e.g. one award split across two rows by an importer. `models.grants.
aggregate_by_key` is the single shared place that resolves such collisions, and
``ExerciseSchedule.income_for``/``default_at_expiry`` and `engine.exercise_optimizer`
all route through it. `normalize_grid_edits` was the missed 4th consumer: it looped
the RAW grant list, so each colliding grant got an independent full ``grant.shares``
budget and the last one's cleaned cells silently overwrote the first's.

The audit's $102K headline is NOT reachable -- the real TXN grants (2019/$104,
2020/$130, 2021/$169) and the `config/defaults.py` placeholders are pairwise
distinct on key(). This is a consistency fix; the gates below use a deliberately
constructed split award.
"""

from __future__ import annotations

from engine.exercise_grid import normalize_grid_edits
from models.grants import StockGrant

# One award split across two rows: identical year+strike+expiry, empty grant_id.
SPLIT_A = StockGrant(2021, 60.0, 400, 2031)
SPLIT_B = StockGrant(2021, 60.0, 600, 2031)
SPLIT_KEY = SPLIT_A.key()  # "2021:60:2031" -- identical for both

DISTINCT = StockGrant(2020, 50.0, 900, 2030)
# Must extend past SPLIT's 2031 expiry: normalize_grid_edits only visits years
# in this list, so a past-expiry cell outside it is never reached or rejected.
YEARS = list(range(2026, 2034))


def test_colliding_grants_share_one_combined_share_budget() -> None:
    """Pre-fix: A is capped at 400, then B's cap of 600 overwrites it entirely,
    so 1,000 entered shares normalize to 600 and 400 are silently dropped."""
    norm = normalize_grid_edits([SPLIT_A, SPLIT_B], YEARS, {SPLIT_KEY: {2027: 1000}})

    assert norm.shares_by_key[SPLIT_KEY] == {2027: 1000}
    assert norm.remaining_by_key[SPLIT_KEY] == 0


def test_remaining_is_measured_against_the_combined_lot() -> None:
    """Pre-fix the last-writer's own 600-share lot decides remaining (300),
    understating the 700 shares actually left across the combined award."""
    norm = normalize_grid_edits([SPLIT_A, SPLIT_B], YEARS, {SPLIT_KEY: {2027: 300}})

    assert norm.shares_by_key[SPLIT_KEY] == {2027: 300}
    assert norm.remaining_by_key[SPLIT_KEY] == 700


def test_cross_year_cap_spends_the_combined_budget_in_year_order() -> None:
    """The audit-0721 C11 aggregate cap must run against the combined 1,000,
    not against whichever lot happens to be processed last."""
    norm = normalize_grid_edits([SPLIT_A, SPLIT_B], YEARS, {SPLIT_KEY: {2027: 700, 2028: 700}})

    assert norm.shares_by_key[SPLIT_KEY] == {2027: 700, 2028: 300}
    assert norm.remaining_by_key[SPLIT_KEY] == 0


def test_colliding_grants_produce_exactly_one_entry() -> None:
    norm = normalize_grid_edits([SPLIT_A, SPLIT_B], YEARS, {SPLIT_KEY: {2027: 100}})

    assert list(norm.shares_by_key) == [SPLIT_KEY]
    assert list(norm.remaining_by_key) == [SPLIT_KEY]


def test_out_of_range_is_reported_once_for_the_combined_lot() -> None:
    """Pre-fix the same past-expiry entry is recorded once per colliding grant,
    so the view renders the identical st.error twice."""
    norm = normalize_grid_edits([SPLIT_A, SPLIT_B], YEARS, {SPLIT_KEY: {2032: 50}})

    assert len(norm.out_of_range) == 1
    grant, year, entered = norm.out_of_range[0]
    assert (grant.key(), year, entered) == (SPLIT_KEY, 2032, 50)
    assert grant.shares == 1000
    assert norm.remaining_by_key[SPLIT_KEY] == 1000


# --- non-regression: distinct keys must be untouched by the aggregation ---


def test_distinct_grants_are_not_merged() -> None:
    raw = {SPLIT_KEY: {2027: 250}, DISTINCT.key(): {2027: 100}}
    norm = normalize_grid_edits([SPLIT_A, SPLIT_B, DISTINCT], YEARS, raw)

    assert norm.shares_by_key[SPLIT_KEY] == {2027: 250}
    assert norm.remaining_by_key[SPLIT_KEY] == 750
    assert norm.shares_by_key[DISTINCT.key()] == {2027: 100}
    assert norm.remaining_by_key[DISTINCT.key()] == 800


def test_single_grant_behaviour_is_unchanged() -> None:
    norm = normalize_grid_edits([DISTINCT], YEARS, {DISTINCT.key(): {2027: 900, 2028: 50}})

    assert norm.shares_by_key[DISTINCT.key()] == {2027: 900}
    assert norm.remaining_by_key[DISTINCT.key()] == 0
    assert norm.out_of_range == []
