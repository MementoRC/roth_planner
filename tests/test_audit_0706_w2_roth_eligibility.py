"""Audit-0706 wave-2 — Traditional IRA deduction phase-out must use year-keyed thresholds.

Finding: TRAD_DEDUCTION_PHASEOUT used 2026 thresholds even when tax_year=2025 was selected.
Fix: Replace flat dict with TRAD_DEDUCTION_PHASEOUT_BY_YEAR keyed by year; lookups
     use trad_deduction_phaseout_for_year() to resolve per tax_year.

TDD regression: pick incomes that fall BETWEEN the 2025 and 2026 bands so only the
correct year gives a partial deduction — the other year gives a wrong answer.
"""

import pytest


class TestTradDeductionPhaseoutByYear:
    """2025 and 2026 statutory boundaries must be exact and independently retrievable."""

    def test_2025_mfj_active_phaseout_lower(self):
        from views.roth_eligibility import TRAD_DEDUCTION_PHASEOUT_BY_YEAR

        lower, _upper = TRAD_DEDUCTION_PHASEOUT_BY_YEAR[2025]["MFJ_active"]
        assert lower == 126_000

    def test_2025_mfj_active_phaseout_upper(self):
        from views.roth_eligibility import TRAD_DEDUCTION_PHASEOUT_BY_YEAR

        _lower, upper = TRAD_DEDUCTION_PHASEOUT_BY_YEAR[2025]["MFJ_active"]
        assert upper == 146_000

    def test_2025_mfj_spouse_only_phaseout(self):
        from views.roth_eligibility import TRAD_DEDUCTION_PHASEOUT_BY_YEAR

        lower, upper = TRAD_DEDUCTION_PHASEOUT_BY_YEAR[2025]["MFJ_spouse_only"]
        assert lower == 236_000
        assert upper == 246_000

    def test_2025_single_phaseout(self):
        from views.roth_eligibility import TRAD_DEDUCTION_PHASEOUT_BY_YEAR

        lower, upper = TRAD_DEDUCTION_PHASEOUT_BY_YEAR[2025]["Single"]
        assert lower == 79_000
        assert upper == 89_000

    def test_2026_values_preserved_in_by_year(self):
        from views.roth_eligibility import TRAD_DEDUCTION_PHASEOUT, TRAD_DEDUCTION_PHASEOUT_BY_YEAR

        # 2026 entries must match the backward-compat alias
        assert TRAD_DEDUCTION_PHASEOUT_BY_YEAR[2026]["MFJ_active"] == TRAD_DEDUCTION_PHASEOUT["MFJ_active"]
        assert TRAD_DEDUCTION_PHASEOUT_BY_YEAR[2026]["MFJ_spouse_only"] == TRAD_DEDUCTION_PHASEOUT["MFJ_spouse_only"]
        assert TRAD_DEDUCTION_PHASEOUT_BY_YEAR[2026]["Single"] == TRAD_DEDUCTION_PHASEOUT["Single"]


class TestTradDeductionPhaseoutForYear:
    """trad_deduction_phaseout_for_year() must return correct bounds per tax year."""

    def test_2025_mfj_active_returns_2025_thresholds(self):
        from views.roth_eligibility import trad_deduction_phaseout_for_year

        lower, upper = trad_deduction_phaseout_for_year(2025, "MFJ_active")
        assert lower == 126_000
        assert upper == 146_000

    def test_2026_mfj_active_returns_2026_thresholds(self):
        from views.roth_eligibility import trad_deduction_phaseout_for_year

        lower, upper = trad_deduction_phaseout_for_year(2026, "MFJ_active")
        assert lower == 129_000
        assert upper == 149_000

    def test_2025_single_returns_2025_thresholds(self):
        from views.roth_eligibility import trad_deduction_phaseout_for_year

        lower, upper = trad_deduction_phaseout_for_year(2025, "Single")
        assert lower == 79_000
        assert upper == 89_000

    def test_2025_mfj_spouse_only_returns_2025_thresholds(self):
        from views.roth_eligibility import trad_deduction_phaseout_for_year

        lower, upper = trad_deduction_phaseout_for_year(2025, "MFJ_spouse_only")
        assert lower == 236_000
        assert upper == 246_000

    def test_unknown_year_falls_back_to_earliest_published(self):
        """Years before 2025 should not crash -- return earliest known data."""
        from views.roth_eligibility import trad_deduction_phaseout_for_year

        lower, upper = trad_deduction_phaseout_for_year(2024, "Single")
        # Should return 2025 values (earliest known), not raise
        assert lower == 79_000
        assert upper == 89_000


class TestTradDeductionYearBehavior:
    """Behavioral: income between 2025 and 2026 thresholds produces different results per year.

    MFJ_active 2025: (126K, 146K)
    MFJ_active 2026: (129K, 149K)

    Test income = $128_000 (above 2025 lower=126K, below 2026 lower=129K):
      - At tax_year=2025 -> phase-out already started (partial deduction < full limit)
      - At tax_year=2026 -> below lower bound (full deduction)

    This is the canonical false-positive valve: if the lookup does not use tax_year,
    both years would compute the same (wrong) answer.
    """

    def test_income_128k_mfj_active_2025_gives_partial_deduction(self):
        """At 2025 threshold MFJ_active lower=126K: $128K income is inside phaseout."""
        from views.roth_eligibility import _phase_out, trad_deduction_phaseout_for_year

        magi = 128_000.0
        limit = 7_500.0  # 2026 limit (any positive value works for the shape test)
        lower, upper = trad_deduction_phaseout_for_year(2025, "MFJ_active")
        result = _phase_out(magi, lower, upper, limit)
        # Phase-out started: result must be strictly less than limit
        assert result < limit, (
            f"Expected partial deduction at 2025 thresholds (lower={lower}), "
            f"got full deduction {result} == {limit}"
        )

    def test_income_128k_mfj_active_2026_gives_full_deduction(self):
        """At 2026 threshold MFJ_active lower=129K: $128K income is BELOW phaseout."""
        from views.roth_eligibility import _phase_out, trad_deduction_phaseout_for_year

        magi = 128_000.0
        limit = 7_500.0
        lower, upper = trad_deduction_phaseout_for_year(2026, "MFJ_active")
        result = _phase_out(magi, lower, upper, limit)
        # Not yet in phase-out: result must equal limit
        assert result == pytest.approx(limit), (
            f"Expected full deduction at 2026 thresholds (lower={lower}), "
            f"got {result}"
        )

    def test_single_income_between_2025_and_2026_lower_bounds(self):
        """Single 2025 lower=79K, 2026 lower=81K. Income $80K splits them."""
        from views.roth_eligibility import _phase_out, trad_deduction_phaseout_for_year

        magi = 80_000.0
        limit = 7_500.0

        lower_2025, upper_2025 = trad_deduction_phaseout_for_year(2025, "Single")
        result_2025 = _phase_out(magi, lower_2025, upper_2025, limit)
        assert result_2025 < limit, "2025: $80K should be inside Single phase-out"

        lower_2026, upper_2026 = trad_deduction_phaseout_for_year(2026, "Single")
        result_2026 = _phase_out(magi, lower_2026, upper_2026, limit)
        assert result_2026 == pytest.approx(limit), "2026: $80K should be below Single phase-out"
