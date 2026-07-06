"""Regression test: deductions() must grant the IRC §63(f) senior extra to a
surviving-spouse Single filer whose real age is in the spouse_age slot.

Bug: deductions() only checked `your_age >= 65`; when the deceased spouse has
your_age=0 and the survivor's age is passed as spouse_age, the senior extra was
silently dropped → ~$2,050/yr lost in deductions.

Fix: for non-MFJ filing statuses use max(your_age, spouse_age) to select the
real filer's age, mirroring the already-correct senior_bonus_deduction().
"""

from __future__ import annotations

import pytest

from engine.tax import (
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
)

BASE = 2026  # no CPI indexing uncertainty for the base year


class TestSurvivorSeniorDeduction:
    """IRC §63(f) senior extra must apply to the surviving filer regardless of
    which age slot holds the real filer's age."""

    def test_survivor_age_in_spouse_slot_gets_senior_extra(self) -> None:
        """Survivor (age 67) whose age is in the spouse_age slot must receive
        the single-filer senior extra.  This is the core regression case."""
        result = deductions(
            your_age=0,
            spouse_age=67,
            std_ded=STD_DEDUCTION_SINGLE,
            senior_extra=SENIOR_EXTRA_SINGLE,
            filing_status="Single",
            year=BASE,
        )
        expected = STD_DEDUCTION_SINGLE + SENIOR_EXTRA_SINGLE
        assert result == pytest.approx(expected), (
            f"Survivor with age in spouse_age slot should get senior extra "
            f"(expected {expected}, got {result})"
        )

    def test_normal_single_filer_under_65_no_senior_extra(self) -> None:
        """Non-regression: a normal single filer under 65 must NOT get the extra."""
        result = deductions(
            your_age=55,
            spouse_age=0,
            std_ded=STD_DEDUCTION_SINGLE,
            senior_extra=SENIOR_EXTRA_SINGLE,
            filing_status="Single",
            year=BASE,
        )
        assert result == pytest.approx(STD_DEDUCTION_SINGLE)

    def test_normal_single_filer_over_65_gets_senior_extra(self) -> None:
        """Non-regression: a standard single filer 65+ in the your_age slot must
        still receive the senior extra after the fix."""
        result = deductions(
            your_age=70,
            spouse_age=0,
            std_ded=STD_DEDUCTION_SINGLE,
            senior_extra=SENIOR_EXTRA_SINGLE,
            filing_status="Single",
            year=BASE,
        )
        expected = STD_DEDUCTION_SINGLE + SENIOR_EXTRA_SINGLE
        assert result == pytest.approx(expected)

    def test_mfj_both_over_65_unchanged(self) -> None:
        """Non-regression: MFJ with both spouses 65+ still gets double extra."""
        from engine.tax import SENIOR_EXTRA_MFJ, STD_DEDUCTION_MFJ

        result = deductions(
            your_age=68,
            spouse_age=66,
            std_ded=STD_DEDUCTION_MFJ,
            senior_extra=SENIOR_EXTRA_MFJ,
            filing_status="MFJ",
            year=BASE,
        )
        expected = STD_DEDUCTION_MFJ + 2 * SENIOR_EXTRA_MFJ
        assert result == pytest.approx(expected)

    def test_mfj_one_over_65_unchanged(self) -> None:
        """Non-regression: MFJ with only one spouse 65+ gets single extra."""
        from engine.tax import SENIOR_EXTRA_MFJ, STD_DEDUCTION_MFJ

        result = deductions(
            your_age=68,
            spouse_age=60,
            std_ded=STD_DEDUCTION_MFJ,
            senior_extra=SENIOR_EXTRA_MFJ,
            filing_status="MFJ",
            year=BASE,
        )
        expected = STD_DEDUCTION_MFJ + SENIOR_EXTRA_MFJ
        assert result == pytest.approx(expected)
