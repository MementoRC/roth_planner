"""Tests for engine.exercise_schedule_store — ExerciseSchedule persistence in
the document store.
"""

from __future__ import annotations

from engine.exercise_schedule_store import load_exercise_schedule, save_exercise_schedule
from engine.store.namespaces import EXERCISE_SCHEDULE
from models.exercise_schedule import _SCHEDULE_VERSION, ExerciseSchedule


def make_schedule() -> ExerciseSchedule:
    sched = ExerciseSchedule()
    sched.set_shares("g19", 2026, 1000)
    sched.set_shares("g19", 2027, 2000)
    sched.set_shares("g20", 2027, 500)
    sched.set_price(2026, 154.0)
    sched.set_price(2027, 160.5)
    return sched


class TestRoundTrip:
    def test_save_then_load_reproduces_schedule(self):
        original = make_schedule()

        save_exercise_schedule(original)
        loaded = load_exercise_schedule()

        assert loaded is not None
        assert loaded.shares_by_grant_year == original.shares_by_grant_year
        assert loaded.price_by_year == original.price_by_year

    def test_year_keys_are_ints_after_load(self):
        save_exercise_schedule(make_schedule())

        loaded = load_exercise_schedule()

        assert loaded is not None
        for years in loaded.shares_by_grant_year.values():
            for year in years:
                assert isinstance(year, int)
        for year in loaded.price_by_year:
            assert isinstance(year, int)


class TestMissingOrMalformed:
    def test_missing_row_returns_none(self):
        assert load_exercise_schedule() is None

    def test_malformed_row_returns_none(self, corrupt_row):
        corrupt_row(EXERCISE_SCHEDULE)

        assert load_exercise_schedule() is None

    def test_missing_version_returns_none(self, store):
        store.put(EXERCISE_SCHEDULE, {"shares_by_grant_year": {}, "price_by_year": {}})

        assert load_exercise_schedule() is None

    def test_wrong_version_returns_none(self, store):
        store.put(
            EXERCISE_SCHEDULE,
            {"version": 999, "shares_by_grant_year": {}, "price_by_year": {}},
        )

        assert load_exercise_schedule() is None

    def test_correct_version_but_non_numeric_value_returns_none(self, store):
        corrupt = {
            "version": _SCHEDULE_VERSION,
            "shares_by_grant_year": {"2019:98": {"2029": "not-an-int"}},
            "price_by_year": {},
        }
        store.put(EXERCISE_SCHEDULE, corrupt)

        assert load_exercise_schedule() is None


class TestPermissions:
    def test_written_row_created(self, store):
        save_exercise_schedule(make_schedule())

        assert store.has(EXERCISE_SCHEDULE)
