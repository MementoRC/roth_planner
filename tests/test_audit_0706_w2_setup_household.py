"""TDD tests for audit-0706 wave-2 setup/household findings.

Findings covered:
- ui-setup-router-12: household __post_init__ guard accepts invalid age 74
- models-config-3: option_income early=False returns only first grant when multiple share expiry_year
"""

from __future__ import annotations

import pytest

from models.grants import StockGrant
from models.household import Household


class TestRmdStartAgeGuard:
    """ui-setup-router-12 (medium): __post_init__ must correct any value not in (73, 75).

    The widget was step=1, so 74 was reachable. The guard previously only
    triggered when == 75; 74 passed through uncorrected. After the fix the guard
    must correct any value outside {73, 75} back to the birth-year-derived value.
    """

    def test_your_rmd_age_74_is_corrected_to_birth_year_derived_value(self) -> None:
        """Setting your_rmd_start_age=74 (invalid) must be corrected by __post_init__."""
        hh = Household(
            your_age=61,
            base_year=2026,
            your_rmd_start_age=74,  # invalid — neither 73 nor 75
        )
        # Born 1965 (1960+) → SECURE 2.0 age is 75
        assert hh.your_rmd_start_age == 75

    def test_spouse_rmd_age_74_is_corrected_to_birth_year_derived_value(self) -> None:
        """Setting spouse_rmd_start_age=74 (invalid) must be corrected by __post_init__."""
        hh = Household(
            spouse_age=55,
            base_year=2026,
            spouse_rmd_start_age=74,  # invalid
        )
        # Born 1971 (1960+) → SECURE 2.0 age is 75
        assert hh.spouse_rmd_start_age == 75

    def test_your_rmd_age_74_corrected_for_1959_cohort(self) -> None:
        """74 for a 1959-born person must correct to 73, not 75."""
        hh = Household(
            your_age=67,
            base_year=2026,
            your_rmd_start_age=74,  # invalid
        )
        # Born 1959 → SECURE 2.0 age is 73
        assert hh.your_rmd_start_age == 73

    def test_valid_73_is_preserved(self) -> None:
        """Explicitly setting 73 must not be overwritten."""
        hh = Household(
            your_age=67,  # born 1959 → statutory 73
            base_year=2026,
            your_rmd_start_age=73,
        )
        assert hh.your_rmd_start_age == 73

    def test_valid_75_triggers_birth_year_derivation(self) -> None:
        """Default 75 still triggers derivation (existing behaviour preserved)."""
        hh = Household(
            your_age=61,
            base_year=2026,
            your_rmd_start_age=75,
        )
        # Born 1965 (1960+) → stays 75
        assert hh.your_rmd_start_age == 75


class TestOptionIncomeMultiGrant:
    """models-config-3 (low): option_income early=False must accumulate ALL grants
    sharing an expiry_year, not short-circuit after the first match.
    """

    def _two_same_expiry_grants(self) -> list[StockGrant]:
        return [
            StockGrant(year=2019, strike=100.0, shares=100, expiry_year=2029),
            StockGrant(year=2020, strike=120.0, shares=200, expiry_year=2029),
        ]

    def test_single_grant_late_exercise_unchanged(self) -> None:
        """Baseline: single grant late exercise still works."""
        grants = [StockGrant(year=2019, strike=100.0, shares=100, expiry_year=2029)]
        hh = Household(grants=grants, txn_price_late=200.0)
        expected = (200.0 - 100.0) * 100  # 10_000
        assert hh.option_income(2029, early=False) == pytest.approx(expected)

    def test_two_grants_same_expiry_returns_sum(self) -> None:
        """Two grants sharing expiry_year=2029 must both be included in late income."""
        grants = self._two_same_expiry_grants()
        hh = Household(grants=grants, txn_price_late=200.0)
        # grant0: (200-100)*100 = 10_000; grant1: (200-120)*200 = 16_000; total = 26_000
        expected = (200.0 - 100.0) * 100 + (200.0 - 120.0) * 200
        assert hh.option_income(2029, early=False) == pytest.approx(expected)

    def test_two_grants_same_expiry_first_grant_not_sole_result(self) -> None:
        """Verify the old early-return bug would have returned only 10_000, not 26_000."""
        grants = self._two_same_expiry_grants()
        hh = Household(grants=grants, txn_price_late=200.0)
        result = hh.option_income(2029, early=False)
        # Old buggy result would be 10_000 (just grant0)
        assert result != pytest.approx(10_000.0), "Bug reproduced: only first grant returned"
        assert result == pytest.approx(26_000.0)

    def test_year_with_no_matching_grant_returns_zero(self) -> None:
        """Non-matching year still returns 0."""
        grants = self._two_same_expiry_grants()
        hh = Household(grants=grants, txn_price_late=200.0)
        assert hh.option_income(2030, early=False) == 0.0

    def test_three_grants_same_expiry_all_accumulated(self) -> None:
        """Three grants sharing expiry_year are all summed."""
        grants = [
            StockGrant(year=2019, strike=100.0, shares=100, expiry_year=2029),
            StockGrant(year=2020, strike=120.0, shares=200, expiry_year=2029),
            StockGrant(year=2021, strike=140.0, shares=150, expiry_year=2029),
        ]
        hh = Household(grants=grants, txn_price_late=200.0)
        expected = (200 - 100) * 100 + (200 - 120) * 200 + (200 - 140) * 150
        assert hh.option_income(2029, early=False) == pytest.approx(expected)

    def test_out_of_the_money_grant_contributes_zero(self) -> None:
        """A grant where price < strike contributes 0 (not negative)."""
        grants = [
            StockGrant(year=2019, strike=100.0, shares=100, expiry_year=2029),
            StockGrant(year=2020, strike=250.0, shares=200, expiry_year=2029),  # OTM
        ]
        hh = Household(grants=grants, txn_price_late=200.0)
        expected = (200.0 - 100.0) * 100 + 0  # OTM = max(200-250,0)*200 = 0
        assert hh.option_income(2029, early=False) == pytest.approx(expected)
