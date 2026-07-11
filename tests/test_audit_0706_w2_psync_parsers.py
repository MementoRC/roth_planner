"""TDD tests for audit-0706 wave-2 portfolio-sync parser fixes.

Findings:
- psync-income-2: HSA savings rows misclassified as investment_income (tax_return.py)
- psync-income-5: apply_magi accepts invalid year values (magi.py)
- psync-equity-5: Roth 403(b) misclassified as roth_ira (classify.py)
"""

from __future__ import annotations

import datetime

import pytest


class TestPsyncIncome5MagiYearRangeGuard:
    """psync-income-5: apply_magi must reject out-of-range year values.

    Negative, zero, or far-future years must be rejected (snap returned
    unchanged). Valid years in [2000, current_year+1] must be accepted.
    """

    def _apply(self, year_val: object, magi_val: float = 95_000.0) -> object:
        from engine.portfolio_sync.magi import apply_magi
        from engine.portfolio_sync.shapes import MagiSnapshot

        snap = MagiSnapshot(fetched_at=datetime.datetime.now(datetime.UTC))
        magi_dict = {"year": year_val, "magi": magi_val, "agi": magi_val}
        return apply_magi(snap, magi_dict)

    def test_year_zero_is_rejected(self):
        """year=0 must not be written into prior_year_magi."""
        snap = self._apply(0)
        assert 0 not in snap.prior_year_magi, "year=0 must be rejected"

    def test_negative_year_is_rejected(self):
        """year=-1 must not be written into prior_year_magi."""
        snap = self._apply(-1)
        assert -1 not in snap.prior_year_magi

    def test_far_future_year_is_rejected(self):
        """A year far in the future (9999) must be rejected."""
        snap = self._apply(9999)
        assert 9999 not in snap.prior_year_magi

    def test_year_1999_is_rejected(self):
        """year=1999 (below 2000 floor) must be rejected."""
        snap = self._apply(1999)
        assert 1999 not in snap.prior_year_magi

    def test_valid_recent_year_is_accepted(self):
        """year=2023 (clearly valid) must be written into prior_year_magi."""
        snap = self._apply(2023)
        assert 2023 in snap.prior_year_magi
        assert snap.prior_year_magi[2023] == pytest.approx(95_000.0)

    def test_valid_year_2000_boundary_accepted(self):
        """year=2000 (lower boundary) must be accepted."""
        snap = self._apply(2000)
        assert 2000 in snap.prior_year_magi

    def test_none_magi_still_no_op(self):
        """None magi dict must still return snap unchanged (existing behaviour)."""
        from engine.portfolio_sync.magi import apply_magi
        from engine.portfolio_sync.shapes import MagiSnapshot

        snap = MagiSnapshot(fetched_at=datetime.datetime.now(datetime.UTC))
        result = apply_magi(snap, None)
        assert result is snap
        assert result.prior_year_magi == {}


class TestPsyncEquity5Roth403bClassification:
    """psync-equity-5: 'Roth 403(b)' must classify as 403b, not roth_ira.

    The 'roth' substring check fires before the '403b'/'403(b)' check,
    so 'Roth 403(b)' gets misclassified as roth_ira.
    Fix: move the 403b check before the roth check.
    """

    def _classify(self, name: str) -> tuple[str, str]:
        from engine.portfolio_sync.classify import _classify_account

        return _classify_account(name)

    def test_roth_403b_with_parentheses_classifies_as_403b(self):
        """'Roth 403(b)' must be classified as 403b, not roth_ira."""
        acct_type, _ = self._classify("Employer Roth 403(b) Plan")
        assert acct_type == "403b", f"Expected 403b, got {acct_type!r}"

    def test_roth_403b_without_parentheses_classifies_as_403b(self):
        """'Roth 403b' (no parens) must also classify as 403b."""
        acct_type, _ = self._classify("Employer Roth 403b Plan")
        assert acct_type == "403b", f"Expected 403b, got {acct_type!r}"

    def test_plain_roth_ira_still_classifies_as_roth_ira(self):
        """A plain Roth IRA account must still classify as roth_ira after the reorder."""
        acct_type, _ = self._classify("Claude R. Cirba — Roth IRA Brokerage Account — 61037368*")
        assert acct_type == "roth_ira"

    def test_plain_403b_without_roth_still_classifies_as_403b(self):
        """A plain 403b account (no 'roth') must still classify as 403b."""
        acct_type, _ = self._classify("VANDERBILT 403B59208")
        assert acct_type == "403b"

    def test_403b_check_precedes_roth_check_for_combined_name(self):
        """Any name with both '403b' and 'roth' must yield 403b."""
        acct_type, _ = self._classify("Roth 403b Retirement Account")
        assert acct_type == "403b"
