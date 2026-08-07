"""Regression test for audit-0722b OBBBA-1 (HIGH).

engine/tax.py senior_bonus_deduction() previously applied the OBBBA senior-bonus
phase-out PER PERSON, then multiplied by the eligible count -- double-charging
the 6%-of-excess reduction for a dual-65+ MFJ household. Pub. L. 119-21 §70103
(IRC §151(d)(5)(C)) reduces the AGGREGATE deduction once:

    total_bonus = $6,000 * eligible
    reduction   = 0.06 * max(0, magi - phaseout_start)
    deduction   = max(0.0, total_bonus - reduction)

For dual-eligible MFJ this zeros at MAGI = $150,000 + $12,000/0.06 = $350,000,
not $250,000 (the old, incorrect per-person endpoint).
"""

from __future__ import annotations

import pytest

from engine.tax import senior_bonus_deduction


class TestObbbaAggregatePhaseoutMfj:
    """Dual-eligible MFJ (both age >= 65), year 2026 unless noted."""

    def test_mfj_dual_partial_phaseout_200k(self) -> None:
        # total_bonus=12_000, reduction=0.06*(200_000-150_000)=3_000 -> 9_000
        assert senior_bonus_deduction(70, 70, magi=200_000, year=2026) == pytest.approx(9_000.0)

    def test_mfj_dual_partial_phaseout_250k(self) -> None:
        # total_bonus=12_000, reduction=0.06*(250_000-150_000)=6_000 -> 6_000
        # (old buggy per-person formula zeroed here; correct aggregate does not)
        assert senior_bonus_deduction(70, 70, magi=250_000, year=2026) == pytest.approx(6_000.0)

    def test_mfj_dual_partial_phaseout_300k_year_2027(self) -> None:
        # total_bonus=12_000, reduction=0.06*(300_000-150_000)=9_000 -> 3_000
        assert senior_bonus_deduction(70, 70, magi=300_000, year=2027) == pytest.approx(3_000.0)

    def test_mfj_dual_zeros_at_350k(self) -> None:
        # Aggregate endpoint: $150,000 + $12,000/0.06 = $350,000
        assert senior_bonus_deduction(70, 70, magi=350_000, year=2026) == pytest.approx(0.0)

    def test_mfj_dual_below_threshold_gets_full_bonus(self) -> None:
        # magi <= phaseout_start ($150,000) -> full aggregate $12,000
        assert senior_bonus_deduction(70, 70, magi=150_000, year=2026) == pytest.approx(12_000.0)


class TestObbbaAggregatePhaseoutSingle:
    """Single-eligible filer: aggregate formula matches the pre-fix per-person
    formula (multiplication by eligible=1 is a no-op), so these are anchor
    checks confirming the fix does not regress the single-eligible case."""

    def test_single_partial_phaseout_75k(self) -> None:
        # total_bonus=6_000, reduction=0.06*(75_000-75_000)=0 -> 6_000
        assert senior_bonus_deduction(
            70, 0, magi=75_000, year=2026, filing_status="Single"
        ) == pytest.approx(6_000.0)

    def test_single_zeros_at_175k(self) -> None:
        # total_bonus=6_000, reduction=0.06*(175_000-75_000)=6_000 -> 0
        assert senior_bonus_deduction(
            70, 0, magi=175_000, year=2026, filing_status="Single"
        ) == pytest.approx(0.0)
