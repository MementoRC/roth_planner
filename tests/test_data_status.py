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
)
from models.household import Household
from models.sourced import Provenance, Source, SourcedValue

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
