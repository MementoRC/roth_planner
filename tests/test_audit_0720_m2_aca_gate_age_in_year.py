"""Regression test for audit-0720 finding M2.

compute_cost_curves' ACA eligibility gate must use age-in-year, not the raw
base-year age, matching sibling IRMAA/non-taxable-SS logic in the same
function.
"""

from __future__ import annotations

import pytest

from engine.aca_irmaa_compute import compute_cost_curves
from models.household import Household


class TestM2AcaGateUsesAgeInYear:
    def test_household_on_medicare_by_swept_year_gets_zero_aca_subsidy(self) -> None:
        hh = Household(
            your_age=63,
            spouse_age=63,
            filing_status="MFJ",
            your_aca_enrolled=True,
            spouse_aca_enrolled=True,
            base_year=2026,
        )

        cc = compute_cost_curves(
            [50_000.0], base_magi=50_000.0, net_inv_income=0.0, hh=hh, year=2032, cpi=0.0
        )

        # Household is 69/69 in 2032 (past ACA age, on Medicare) -> no subsidy.
        assert cc.aca_subsidy_vals == [pytest.approx(0.0)]
