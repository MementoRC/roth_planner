"""Tests for engine/data_status.py — pure data-completeness/staleness/conflict.

Pure module tests: no streamlit, all datetimes are fixed literals (never
datetime.now()).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from engine.data_status import (
    STALE_THRESHOLD_DAYS,
    DataCompleteness,
    DataStatusItem,
    compute_data_completeness,
    compute_data_status,
    compute_exercise_completeness,
    compute_ytd_completeness,
)
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household
from models.sourced import Provenance, Source, SourcedValue
from models.ytd_income import YTDSnapshot

NOW = datetime(2026, 7, 24, 12, 0, 0)


def test_missing_when_no_sourced_value() -> None:
    """A field with only the plain (unsourced) household default flags missing."""
    hh = Household()
    assert not isinstance(hh.your_ira, SourcedValue)

    items = compute_data_status(hh, ["your_ira"], pending_candidates=set(), now=NOW)

    assert items == [
        DataStatusItem(
            field="your_ira",
            label="Your IRA balance",
            severity="missing",
            detail="No confirmed value on record.",
        )
    ]


def test_conflict_when_pending_candidate_blocks_field() -> None:
    """A field flagged in pending_candidates is a conflict, even if confirmed."""
    hh = Household()
    hh.your_ira = SourcedValue(500_000.0, Provenance(source=Source.MANUAL, recorded_at=NOW))

    items = compute_data_status(hh, ["your_ira"], pending_candidates={"your_ira"}, now=NOW)

    assert len(items) == 1
    assert items[0].field == "your_ira"
    assert items[0].severity == "conflict"


def test_stale_when_confirmed_value_older_than_threshold() -> None:
    """A confirmed value older than STALE_THRESHOLD_DAYS (no pending candidate) is stale."""
    old_recorded_at = NOW - timedelta(days=STALE_THRESHOLD_DAYS + 1)
    hh = Household()
    hh.your_ira = SourcedValue(500_000.0, Provenance(source=Source.MANUAL, recorded_at=old_recorded_at))

    items = compute_data_status(hh, ["your_ira"], pending_candidates=set(), now=NOW)

    assert len(items) == 1
    assert items[0].field == "your_ira"
    assert items[0].severity == "stale"
    assert "8 days ago" in items[0].detail


def test_fully_populated_recently_confirmed_household_returns_empty() -> None:
    """A fully-populated, all-confirmed, recently-synced household flags nothing."""
    recent = NOW - timedelta(days=1)
    hh = Household()
    hh.your_ira = SourcedValue(500_000.0, Provenance(source=Source.MANUAL, recorded_at=recent))
    hh.spouse_ira = SourcedValue(400_000.0, Provenance(source=Source.PDF, recorded_at=recent))

    items = compute_data_status(
        hh, ["your_ira", "spouse_ira"], pending_candidates=set(), now=NOW
    )

    assert items == []


def test_completeness_counts_total_ok_and_fraction() -> None:
    hh = Household()
    hh.your_ira = SourcedValue(500_000.0, Provenance(source=Source.MANUAL, recorded_at=NOW))

    result = compute_data_completeness(
        hh, ["your_ira", "spouse_ira"], pending_candidates=set(), now=NOW
    )

    assert isinstance(result, DataCompleteness)
    assert result.total == 2
    assert result.ok + len(result.issues) == result.total
    assert 0.0 <= result.fraction <= 1.0
    assert isinstance(result.is_complete, bool)


def test_completeness_all_ok_fields_is_complete() -> None:
    recent = NOW - timedelta(days=1)
    hh = Household()
    hh.your_ira = SourcedValue(500_000.0, Provenance(source=Source.MANUAL, recorded_at=recent))
    hh.spouse_ira = SourcedValue(400_000.0, Provenance(source=Source.PDF, recorded_at=recent))

    result = compute_data_completeness(
        hh, ["your_ira", "spouse_ira"], pending_candidates=set(), now=NOW
    )

    assert result.is_complete is True
    assert result.fraction == 1.0
    assert result.ok == result.total
    assert result.issues == ()


def test_completeness_missing_item_blocks_completeness() -> None:
    hh = Household()

    result = compute_data_completeness(
        hh, ["your_ira"], pending_candidates=set(), now=NOW
    )

    assert result.is_complete is False


def test_completeness_stale_only_is_non_blocking() -> None:
    old_recorded_at = NOW - timedelta(days=STALE_THRESHOLD_DAYS + 1)
    hh = Household()
    hh.your_ira = SourcedValue(
        500_000.0, Provenance(source=Source.MANUAL, recorded_at=old_recorded_at)
    )

    result = compute_data_completeness(
        hh, ["your_ira"], pending_candidates=set(), now=NOW
    )

    assert result.is_complete is True
    assert len(result.issues) == 1
    assert result.issues[0].severity == "stale"


def test_completeness_by_severity_tallies_mix() -> None:
    old_recorded_at = NOW - timedelta(days=STALE_THRESHOLD_DAYS + 1)
    hh = Household()
    hh.your_ira = SourcedValue(
        500_000.0, Provenance(source=Source.MANUAL, recorded_at=old_recorded_at)
    )
    hh.spouse_ira = SourcedValue(400_000.0, Provenance(source=Source.PDF, recorded_at=NOW))

    result = compute_data_completeness(
        hh,
        ["your_ira", "spouse_ira", "your_roth"],
        pending_candidates={"spouse_ira"},
        now=NOW,
    )

    assert result.by_severity == {"stale": 1, "conflict": 1, "missing": 1}


def test_setup_step_groups_partition_governed_scalars() -> None:
    from engine.data_sources.resolver import HOUSEHOLD_SCALAR_FIELDS
    from engine.data_status import SETUP_STEP_GROUPS

    assert [key for key, _label, _fields in SETUP_STEP_GROUPS] == [
        "household",
        "accounts",
        "options",
        "portfolio",
        "assumptions",
    ]

    for field_name in HOUSEHOLD_SCALAR_FIELDS:
        occurrences = sum(
            1 for _key, _label, fields in SETUP_STEP_GROUPS if field_name in fields
        )
        assert occurrences == 1


def test_governed_fields_for_step_static() -> None:
    from engine.data_status import governed_fields_for_step

    hh = Household()

    assert governed_fields_for_step(hh, "accounts") == [
        "your_ira",
        "spouse_ira",
        "your_roth",
        "spouse_roth",
    ]
    assert governed_fields_for_step(hh, "portfolio") == []


def test_governed_fields_for_step_appends_dynamic_magi() -> None:
    from engine.data_status import governed_fields_for_step

    hh = Household(prior_year_magi={2024: 280_000.0})

    fields = governed_fields_for_step(hh, "assumptions")

    assert "prior_year_magi.2024" in fields


def test_governed_fields_for_step_unknown_raises() -> None:
    import pytest

    from engine.data_status import governed_fields_for_step

    hh = Household()

    with pytest.raises(ValueError, match="Unknown setup step"):
        governed_fields_for_step(hh, "bogus")


class TestComputeYtdCompleteness:
    def test_empty_snapshot_date_is_missing(self):
        snap = YTDSnapshot()
        result = compute_ytd_completeness(snap, now=datetime(2026, 7, 28))
        assert result.issues[0].severity == "missing"
        assert result.ok == 0

    def test_malformed_snapshot_date_is_missing(self):
        snap = YTDSnapshot(snapshot_date="not-a-date")
        result = compute_ytd_completeness(snap, now=datetime(2026, 7, 28))
        assert result.issues[0].severity == "missing"

    def test_recent_snapshot_is_ok(self):
        snap = YTDSnapshot(snapshot_date="2026-07-27")
        result = compute_ytd_completeness(snap, now=datetime(2026, 7, 28))
        assert result.issues == ()
        assert result.ok == 1

    def test_snapshot_at_exact_threshold_is_still_ok(self):
        # exactly 14 days -- NOT > threshold, so still ok (boundary case)
        snap = YTDSnapshot(snapshot_date="2026-07-14")
        result = compute_ytd_completeness(snap, now=datetime(2026, 7, 28))
        assert result.issues == ()

    def test_snapshot_just_past_threshold_is_stale(self):
        # 15 days -- past the 14-day threshold
        snap = YTDSnapshot(snapshot_date="2026-07-13")
        result = compute_ytd_completeness(snap, now=datetime(2026, 7, 28))
        assert result.issues[0].severity == "stale"


class TestComputeExerciseCompleteness:
    @staticmethod
    def _grant(*, year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id=""):
        return StockGrant(
            year=year, strike=strike, shares=shares, expiry_year=expiry_year, grant_id=grant_id
        )

    def test_no_grants_is_ok(self):
        hh = Household(grants=[], base_year=2026)

        result = compute_exercise_completeness(hh)

        assert result.issues == ()

    def test_no_schedule_is_missing(self):
        hh = Household(grants=[self._grant()], base_year=2026)
        hh.exercise_schedule = None

        result = compute_exercise_completeness(hh)

        assert len(result.issues) == 1
        assert result.issues[0].severity == "missing"
        assert result.ok == 0

    def test_empty_but_not_none_schedule_is_missing_per_grant(self):
        hh = Household(grants=[self._grant()], base_year=2026)
        hh.exercise_schedule = ExerciseSchedule()

        result = compute_exercise_completeness(hh)

        assert len(result.issues) == 1
        assert result.issues[0].severity == "missing"

    def test_partially_allocated_flags_incomplete_grants_only(self):
        g1 = self._grant(year=2019, strike=104.0, expiry_year=2029)
        g2 = self._grant(year=2020, strike=130.0, expiry_year=2030)
        hh = Household(grants=[g1, g2], base_year=2026)
        schedule = ExerciseSchedule()
        schedule.set_shares(g1.key(), 2029, g1.shares)  # g1 fully allocated, g2 untouched
        hh.exercise_schedule = schedule

        result = compute_exercise_completeness(hh)

        assert len(result.issues) == 1
        assert result.issues[0].field == g2.key()

    def test_expired_grant_is_skipped(self):
        expired = self._grant(year=2010, strike=50.0, expiry_year=2020)
        hh = Household(grants=[expired], base_year=2026)
        hh.exercise_schedule = None

        result = compute_exercise_completeness(hh)

        assert result.issues == ()
        assert result.total == 0

    def test_fully_allocated_is_ok(self):
        g = self._grant(year=2019, strike=104.0, expiry_year=2029)
        hh = Household(grants=[g], base_year=2026)
        schedule = ExerciseSchedule()
        schedule.set_shares(g.key(), 2029, g.shares)
        hh.exercise_schedule = schedule

        result = compute_exercise_completeness(hh)

        assert result.issues == ()
        assert result.ok == result.total
