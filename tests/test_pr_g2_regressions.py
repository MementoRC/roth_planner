"""Regression test for deep-review 2026-06-18 PR-G2 (IRMAA payment-year indexing)."""

import pytest

from engine.irmaa import irmaa_for_year
from engine.scenario import ConversionPlan, run_scenario
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestIrmaaPaymentYearIndexing:
    def test_thresholds_index_to_payment_year_not_income_year(self):
        """irmaa-3: IRMAA thresholds must be indexed to the PAYMENT year (income_year+2),
        matching CMS-published thresholds, not the income year.

        Pin lookback MAGI = 260k for payment year 2032 (income year 2030) with cpi=4%.
        MFJ Tier-1 base 218k indexes to ~255k at 2030 and ~276k at 2032. 260k is ABOVE
        the income-year (2030) threshold but BELOW the payment-year (2032) threshold, so:
          - correct (payment-year) IRMAA = 0
          - buggy (income-year) IRMAA > 0
        """
        hh = Household(
            your_age=66,
            spouse_age=66,
            cpi_assumption=0.04,
            prior_year_magi={2030: 260_000.0},
        )
        result = run_scenario(hh, ConversionPlan(), end_age=75)
        yr2032 = next(y for y in result.years if y.your_age == 72)  # calendar 2032

        part_b = hh.medicare_part_b_base_monthly * 12
        oracle_payment, _ = irmaa_for_year(260_000, 70, 70, base_part_b=part_b, year=2032, cpi=0.04)
        oracle_income, _ = irmaa_for_year(260_000, 70, 70, base_part_b=part_b, year=2030, cpi=0.04)

        assert oracle_income > 0  # the old (income-year) indexing would have charged IRMAA
        assert oracle_payment == 0  # correctly indexed payment-year threshold clears it
        assert yr2032.irmaa_cost == approx(oracle_payment)  # engine uses payment year -> 0
