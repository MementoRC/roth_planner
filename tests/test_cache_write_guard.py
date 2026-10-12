"""Permanent self-test for tests/conftest.py's cache-write guard mechanism
(audit-0805 W1 follow-up).

Unit-tests the pure ``_diff_cache_snapshots`` helper directly with synthetic
``{path: (exists, digest)}`` dicts — no real path is ever touched. This is
what proves the guard's create/modify/delete classification actually works,
independent of whether any given test run happens to exercise a real write;
a future edit that silently breaks the comparison (e.g. an inverted
condition) fails HERE instead of only ever showing up as a suspiciously
all-green suite.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import _diff_cache_snapshots

_FAKE_A = Path("/fake/repo-root/.fake_cache_a.json")
_FAKE_B = Path("/fake/repo-root/.fake_cache_b.json")


def test_no_diff_when_snapshots_are_identical() -> None:
    before = {_FAKE_A: (True, "hash1"), _FAKE_B: (False, None)}
    after = {_FAKE_A: (True, "hash1"), _FAKE_B: (False, None)}
    assert _diff_cache_snapshots(before, after) == []


def test_flags_created_file() -> None:
    before = {_FAKE_A: (False, None)}
    after = {_FAKE_A: (True, "hash1")}
    assert _diff_cache_snapshots(before, after) == [f"{_FAKE_A}: created"]


def test_flags_deleted_file() -> None:
    before = {_FAKE_A: (True, "hash1")}
    after = {_FAKE_A: (False, None)}
    assert _diff_cache_snapshots(before, after) == [f"{_FAKE_A}: deleted"]


def test_flags_modified_file_same_existence_different_digest() -> None:
    before = {_FAKE_A: (True, "hash1")}
    after = {_FAKE_A: (True, "hash2")}
    assert _diff_cache_snapshots(before, after) == [f"{_FAKE_A}: modified"]


def test_flags_multiple_offenders_independently_in_input_order() -> None:
    before = {_FAKE_A: (True, "hash1"), _FAKE_B: (False, None)}
    after = {_FAKE_A: (True, "hash2"), _FAKE_B: (True, "hashX")}
    assert _diff_cache_snapshots(before, after) == [
        f"{_FAKE_A}: modified",
        f"{_FAKE_B}: created",
    ]


def test_untouched_paths_alongside_an_offender_are_not_flagged() -> None:
    before = {_FAKE_A: (True, "hash1"), _FAKE_B: (True, "hash1")}
    after = {_FAKE_A: (True, "hash1"), _FAKE_B: (True, "hash2")}
    assert _diff_cache_snapshots(before, after) == [f"{_FAKE_B}: modified"]


def test_absent_before_and_after_is_not_flagged() -> None:
    """A watched file that never existed (before OR after) must never be
    reported — this is the exact case that made C67 unconfirmable in the
    audit-0805 stage-1 run (the file was already absent going in)."""
    before = {_FAKE_A: (False, None)}
    after = {_FAKE_A: (False, None)}
    assert _diff_cache_snapshots(before, after) == []
