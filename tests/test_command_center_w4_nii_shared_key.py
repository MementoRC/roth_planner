"""W4 — engine tests for net investment income semantics in ACA+IRMAA.

`engine.aca_irmaa_compute.compute_cost_curves` adds the auto-derived base
(`BaseIncome.net_investment_income_addl`, plus opted-in YTD investment
income) to the manual NII before computing NIIT, matching Sweet Spot's
treatment. These tests call the engine directly.

NII's single editable home is now Setup > Assumptions; see
tests/test_one_home_nii.py for the UI tests.
"""

from __future__ import annotations

import pytest

from engine.aca_irmaa_compute import compute_cost_curves
from engine.niit import NIIT_RATE, NIIT_THRESHOLD_SINGLE, niit
from engine.sweet_spot_compute import base_income_for_year
from models.household import Household
from models.ytd_income import YTDSnapshot


def _hh_with_investment_income() -> Household:
    """Single filer with a taxable brokerage balance -> forecast realized gains
    feed `BaseIncome.net_investment_income_addl` via the default (no
    `brokerage_growth`) path: `brokerage_start * growth_rate * brok_turnover`.
    """
    return Household(filing_status="Single", your_age=61, spouse_age=61, brokerage_start=500_000.0)


# --- Step 0/2: engine auto-base alignment (the correctness fix) ---------------


def test_compute_cost_curves_adds_auto_detected_nii_to_niit() -> None:
    """ACA+IRMAA must add the auto-derived net_investment_income_addl (the SAME
    base Sweet Spot uses) to the manual net_inv_income before computing NIIT.

    Pre-fix, compute_cost_curves fed the raw manual value only, under-counting
    NIIT whenever the household has forecast brokerage income.
    """
    hh = _hh_with_investment_income()
    manual_nii = 5_000.0
    magi = 250_000.0  # Single NIIT threshold $200K -> $50K excess

    auto_nii = base_income_for_year(hh, hh.base_year).net_investment_income_addl
    assert auto_nii == pytest.approx(500_000.0 * 0.07 * 0.30)  # forecast realized gains only

    cc = compute_cost_curves([magi], magi, manual_nii, hh, year=hh.base_year, cpi=0.0)

    excess = magi - NIIT_THRESHOLD_SINGLE
    expected_total_nii = manual_nii + auto_nii
    expected_niit = min(expected_total_nii, excess) * NIIT_RATE

    assert cc.base_niit == pytest.approx(expected_niit)
    assert cc.niit_vals[0] == pytest.approx(expected_niit)
    # total_hidden_cost's embedded NIIT increase must be internally consistent
    # with niit_increase_vals (both must use the same total_nii).
    assert cc.niit_increase_vals[0] == pytest.approx(max(cc.niit_vals[0] - cc.base_niit, 0))

    # Hand-verified delta: the correctness fix raises NIIT by exactly the NIIT
    # contribution of the auto-derived base at this MAGI.
    manual_only_niit = niit(magi, manual_nii, filing_status="Single")
    delta = cc.base_niit - manual_only_niit
    assert delta == pytest.approx(
        niit(magi, expected_total_nii, filing_status="Single")
        - niit(magi, manual_nii, filing_status="Single")
    )
    assert delta > 0


def test_compute_cost_curves_zero_investment_income_unchanged() -> None:
    """Byte-identical golden: a household with no brokerage/YTD investment income
    (net_investment_income_addl == 0) is untouched by the auto-base fix."""
    hh = Household()  # brokerage_start=0.0 default -> auto_nii == 0
    magi = 300_000.0  # MFJ NIIT threshold $250K -- would be exposed if nii > 0

    auto_nii = base_income_for_year(hh, hh.base_year).net_investment_income_addl
    assert auto_nii == 0.0

    cc = compute_cost_curves([magi], magi, 0.0, hh, year=hh.base_year, cpi=0.0)
    assert cc.base_niit == 0.0
    assert cc.niit_vals[0] == 0.0


# --- Step 3: apply_ytd parity --------------------------------------------------


def test_compute_cost_curves_ytd_param_folds_in_ytd_investment_income() -> None:
    """Passing ytd= must fold YTD investment income into the auto base, matching
    Sweet Spot's apply_ytd_to_projection-gated treatment for the base year."""
    hh = Household(filing_status="Single", your_age=61, spouse_age=61)
    ytd = YTDSnapshot(tax_year=hh.base_year, ltcg_ytd=20_000.0)
    magi = 250_000.0

    cc_no_ytd = compute_cost_curves([magi], magi, 0.0, hh, year=hh.base_year, cpi=0.0)
    assert cc_no_ytd.base_niit == 0.0  # no manual, no auto (default hh, no ytd)

    cc_with_ytd = compute_cost_curves([magi], magi, 0.0, hh, year=hh.base_year, cpi=0.0, ytd=ytd)

    excess = magi - NIIT_THRESHOLD_SINGLE
    expected = min(20_000.0, excess) * NIIT_RATE
    assert cc_with_ytd.base_niit == pytest.approx(expected)
