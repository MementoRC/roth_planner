"""W4 — unify net investment income (align ACA+IRMAA semantics + shared key).

Two NII widgets used to diverge:
- views/aca_irmaa.py fed the RAW manual value to niit() (ignoring the
  auto-derived base), under-counting NIIT whenever forecast dividends/gains
  or opted-in YTD investment income exist.
- views/sweet_spot.py treats the manual value as an add-on OVER the
  auto-derived base (`BaseIncome.net_investment_income_addl`).

Option A: ACA+IRMAA now ALSO adds the auto-derived base before computing
NIIT (matching Sweet Spot's treatment), and both widgets share one
`st.session_state["net_inv_income"]` key so edits stay in sync.

Engine tests use `engine.aca_irmaa_compute.compute_cost_curves` directly;
view tests use `streamlit.testing.v1.AppTest.from_function` (mirrors
tests/test_roth_eligibility_view.py — the wrapped function must be fully
self-contained, all imports/object construction inside its body).
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

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
        niit(magi, expected_total_nii, filing_status="Single") - niit(magi, manual_nii, filing_status="Single")
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


# --- Step 4: shared key ---------------------------------------------------------


def _render_aca_irmaa() -> None:
    from models.household import Household
    from views.aca_irmaa import render

    hh = Household(your_age=61, spouse_age=55, base_year=2026, filing_status="MFJ")
    render(hh)


def _render_sweet_spot() -> None:
    from models.household import Household
    from views.sweet_spot import render

    hh = Household(your_age=61, spouse_age=55, base_year=2026, filing_status="MFJ")
    render(hh)


def test_aca_irmaa_nii_widget_uses_shared_key() -> None:
    at = AppTest.from_function(_render_aca_irmaa)
    at.run()
    assert not at.exception
    at.number_input(key="net_inv_income").set_value(15_000).run()
    assert not at.exception
    assert at.session_state["net_inv_income"] == 15_000


def test_sweet_spot_nii_widget_reads_value_set_by_aca_irmaa() -> None:
    """Entering a value on ACA+IRMAA (writing session_state["net_inv_income"])
    must be read back by the Sweet Spot widget via the same key -- proving the
    two pages stay in sync."""
    at = AppTest.from_function(_render_sweet_spot)
    at.session_state["net_inv_income"] = 15_000
    at.run()
    assert not at.exception
    assert at.number_input(key="net_inv_income").value == 15_000


def test_sweet_spot_nii_widget_uses_shared_key() -> None:
    at = AppTest.from_function(_render_sweet_spot)
    at.run()
    assert not at.exception
    at.number_input(key="net_inv_income").set_value(7_500).run()
    assert not at.exception
    assert at.session_state["net_inv_income"] == 7_500
