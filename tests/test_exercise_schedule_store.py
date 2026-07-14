"""Tests for engine.exercise_schedule_store — ExerciseSchedule persistence via
the hardened secure_io PII cache pattern.
"""

from __future__ import annotations

import json

from engine.exercise_schedule_store import load_exercise_schedule, save_exercise_schedule
from models.exercise_schedule import ExerciseSchedule


def make_schedule() -> ExerciseSchedule:
    sched = ExerciseSchedule()
    sched.set_shares("g19", 2026, 1000)
    sched.set_shares("g19", 2027, 2000)
    sched.set_shares("g20", 2027, 500)
    sched.set_price(2026, 154.0)
    sched.set_price(2027, 160.5)
    return sched


class TestRoundTrip:
    def test_save_then_load_reproduces_schedule(self, tmp_path):
        path = tmp_path / ".exercise_schedule_cache.json"
        original = make_schedule()

        save_exercise_schedule(original, path=path)
        loaded = load_exercise_schedule(path=path)

        assert loaded is not None
        assert loaded.shares_by_grant_year == original.shares_by_grant_year
        assert loaded.price_by_year == original.price_by_year

    def test_year_keys_are_ints_after_load(self, tmp_path):
        path = tmp_path / ".exercise_schedule_cache.json"
        save_exercise_schedule(make_schedule(), path=path)

        loaded = load_exercise_schedule(path=path)

        assert loaded is not None
        for years in loaded.shares_by_grant_year.values():
            for year in years:
                assert isinstance(year, int)
        for year in loaded.price_by_year:
            assert isinstance(year, int)


class TestMissingOrMalformed:
    def test_missing_file_returns_none(self, tmp_path):
        path = tmp_path / "does_not_exist.json"
        assert load_exercise_schedule(path=path) is None

    def test_malformed_json_returns_none(self, tmp_path):
        path = tmp_path / ".exercise_schedule_cache.json"
        path.write_text("{not valid json")

        assert load_exercise_schedule(path=path) is None

    def test_missing_version_returns_none(self, tmp_path):
        path = tmp_path / ".exercise_schedule_cache.json"
        path.write_text(json.dumps({"shares_by_grant_year": {}, "price_by_year": {}}))

        assert load_exercise_schedule(path=path) is None

    def test_wrong_version_returns_none(self, tmp_path):
        path = tmp_path / ".exercise_schedule_cache.json"
        path.write_text(
            json.dumps({"version": 999, "shares_by_grant_year": {}, "price_by_year": {}})
        )

        assert load_exercise_schedule(path=path) is None


class TestPermissions:
    def test_written_file_created(self, tmp_path):
        path = tmp_path / ".exercise_schedule_cache.json"
        save_exercise_schedule(make_schedule(), path=path)

        assert path.exists()
