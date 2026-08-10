"""Regression test for audit-0722b OBBBA-1 (HIGH) -- CORRECTED by audit-0809 C19.

audit-0722b changed senior_bonus_deduction() from a per-person-floored formula
to an AGGREGATE formula (reduce the combined $12,000 once, floor once). That
"fix" was itself wrong: IRS Schedule 1-A (Form 1040), Part V, lines 31-37
derives ONE reduced per-person amount and enters that SAME amount on BOTH
line 36a (you) and line 36b (spouse), then line 37 sums them -- i.e. each
person's $6,000 must be reduced by 6% of the MAGI excess AND INDEPENDENTLY
FLOORED AT ZERO, then summed. audit-0809 C19 reverted engine/tax.py to this
per-person rule:

    per_person = max(0.0, bonus_per_person - 0.06 * max(0.0, magi - phaseout_start))
    deduction  = per_person * eligible

For dual-eligible MFJ this zeros at MAGI = $150,000 + $6,000/0.06 = $250,000,
not $350,000 (the aggregate endpoint this file previously asserted).
"""

from __future__ import annotations

import pytest

from engine.tax import senior_bonus_deduction


class TestObbbaPerPersonPhaseoutMfj:
    """Dual-eligible MFJ (both age >= 65), year 2026 unless noted."""

    def test_mfj_dual_partial_phaseout_200k(self) -> None:
        # per_person=max(0,6_000-0.06*(200_000-150_000))=3_000 -> total 2*3_000=6_000
        assert senior_bonus_deduction(70, 70, magi=200_000, year=2026) == pytest.approx(6_000.0)

    def test_mfj_dual_partial_phaseout_250k(self) -> None:
        # per_person=max(0,6_000-0.06*(250_000-150_000))=max(0,0)=0 -> total 0.0
        # (the old aggregate formula did not zero here; correct per-person does)
        assert senior_bonus_deduction(70, 70, magi=250_000, year=2026) == pytest.approx(0.0)

    def test_mfj_dual_partial_phaseout_300k_year_2027(self) -> None:
        # per_person=max(0,6_000-0.06*(300_000-150_000))=max(0,-3_000)=0 -> total 0.0
        assert senior_bonus_deduction(70, 70, magi=300_000, year=2027) == pytest.approx(0.0)

    def test_mfj_dual_above_250k_stays_zero(self) -> None:
        # Sanity check well past the correct $250,000 endpoint: still zero.
        assert senior_bonus_deduction(70, 70, magi=350_000, year=2026) == pytest.approx(0.0)

    def test_mfj_dual_below_threshold_full_bonus(self) -> None:
        # magi <= phaseout_start ($150,000) -> full aggregate $12,000
        assert senior_bonus_deduction(70, 70, magi=150_000, year=2026) == pytest.approx(12_000.0)


class TestObbbaPerPersonPhaseoutSingle:
    """Single-eligible filer: the per-person formula matches the aggregate
    formula (multiplication by eligible=1 is a no-op), so these are anchor
    checks confirming the C19 fix does not regress the single-eligible case."""

    def test_single_partial_phaseout_75k(self) -> None:
        # per_person=max(0,6_000-0.06*(75_000-75_000))=6_000 -> total 6_000
        assert senior_bonus_deduction(
            70, 0, magi=75_000, year=2026, filing_status="Single"
        ) == pytest.approx(6_000.0)

    def test_single_zeros_at_175k(self) -> None:
        # per_person=max(0,6_000-0.06*(175_000-75_000))=max(0,0)=0 -> total 0.0
        assert senior_bonus_deduction(
            70, 0, magi=175_000, year=2026, filing_status="Single"
        ) == pytest.approx(0.0)
