"""audit-1018 W2: F-04 (brokerage-sale gain in the Sweet Spot / Explorer base) and
F-05 (IRMAA-Safe Max, card All-In costs and summary columns are draw-aware).

Synthetic households only.
"""

from __future__ import annotations

import pytest

from engine.aca_irmaa_compute import default_base_magi
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from engine.sweet_spot_compute import (
    STEP,
    all_in_at_conversion,
    base_income_for_year,
    compute_multi_year_summary,
    forced_draw_fills,
    irmaa_safe_max,
    zero_conversion_ira_draws,
    zero_conversion_years,
)
from engine.sweet_spot_draw_curve import sweep_all_in
from models.household import Household
from tests.test_issue_1_room_matches_fill import (
    BASE_YEAR,
    _forced_draw_hh,
    _hh,
    _no_draw_hh,
)


def _sale_hh() -> Household:
    """Spending is funded by selling low-basis brokerage: a realized gain, and
    no IRA draw."""
    return _hh(brokerage_start=200_000.0, brokerage_start_basis=40_000.0)


def _zero_magi(hh: Household) -> float:
    return run_scenario(hh, ConversionPlan(), "zero", end_age=95).years[0].magi


# --------------------------------------------------------------------------
# F-04
# --------------------------------------------------------------------------


def test_precondition_sale_year_sells_brokerage_and_draws_no_ira() -> None:
    yr = zero_conversion_years(_sale_hh())[BASE_YEAR]
    assert yr.forced_brokerage_draw > 0.0
    assert yr.forced_your_ira_draw + yr.forced_spouse_ira_draw == 0.0


def test_sweet_spot_base_magi_includes_the_brokerage_sale_gain() -> None:
    hh = _sale_hh()
    row = compute_multi_year_summary(hh)[0]
    assert row.base_magi == pytest.approx(_zero_magi(hh), abs=1.0)


def test_explorer_default_base_magi_includes_the_brokerage_sale_gain() -> None:
    hh = _sale_hh()
    got = default_base_magi(hh, ytd=None, net_inv_income=0.0)
    assert got == pytest.approx(_zero_magi(hh), abs=1.0)


def test_sale_gain_is_magi_and_nii_but_not_ordinary() -> None:
    hh = _sale_hh()
    years = zero_conversion_years(hh)
    draw = years[BASE_YEAR].forced_brokerage_draw
    plain = base_income_for_year(hh, BASE_YEAR)
    aware = compute_multi_year_summary(hh)[0].base_magi
    assert aware - plain.base_magi == pytest.approx(draw * 0.8, abs=1.0)


def test_no_sale_year_base_is_unchanged() -> None:
    hh = _no_draw_hh()
    # The big brokerage sells at full basis: no gain, so the base is the old one.
    row = compute_multi_year_summary(hh)[0]
    assert row.base_magi == pytest.approx(base_income_for_year(hh, BASE_YEAR).base_magi, abs=0.01)


# --------------------------------------------------------------------------
# F-05 remainder
# --------------------------------------------------------------------------


def _setup(hh: Household):  # type: ignore[no-untyped-def]
    draws = zero_conversion_ira_draws(hh)
    years = zero_conversion_years(hh)
    fills = forced_draw_fills(hh, ytd=None, net_inv_income=0.0, ira_draws=draws)
    base = base_income_for_year(hh, BASE_YEAR, ira_draw=draws[BASE_YEAR])
    return years, fills, base


def test_summary_irmaa_safe_is_draw_aware() -> None:
    hh = _forced_draw_hh()
    years, fills, base = _setup(hh)
    row = compute_multi_year_summary(hh)[0]
    blind = irmaa_safe_max(hh, base, row_threshold(hh), 0.0, 0.0)
    assert row.irmaa_safe is not None
    assert row.irmaa_safe < blind
    # The curve's own pricing: safe stays under the tier, one step more crosses.
    thr = row_threshold(hh)
    at = sweep_all_in(
        hh,
        base,
        [row.irmaa_safe, row.irmaa_safe + STEP],
        net_inv_income=0.0,
        ltcg_eligible=0.0,
        zero_year=years[BASE_YEAR],
        fills=fills,
    )
    assert at[0].magi <= thr < at[1].magi


def row_threshold(hh: Household) -> float:
    from engine.irmaa import IRMAA_TIERS_MFJ, _index_irmaa_tiers, irmaa_payment_year

    return _index_irmaa_tiers(IRMAA_TIERS_MFJ, irmaa_payment_year(BASE_YEAR), hh.cpi_assumption)[0][
        0
    ]


def test_direct_irmaa_safe_matches_summary() -> None:
    from engine.sweet_spot_draw_curve import draw_aware_irmaa_safe_max

    hh = _forced_draw_hh()
    years, fills, base = _setup(hh)
    got = draw_aware_irmaa_safe_max(
        hh,
        base,
        row_threshold(hh),
        0.0,
        0.0,
        zero_year=years[BASE_YEAR],
        fills=fills,
    )
    assert got == compute_multi_year_summary(hh)[0].irmaa_safe


def test_summary_costs_equal_the_draw_aware_curve_at_the_fills() -> None:
    hh = _forced_draw_hh()
    years, fills, base = _setup(hh)
    row = compute_multi_year_summary(hh)[0]
    curve = sweep_all_in(
        hh,
        base,
        [row.fill_12, row.fill_22],
        net_inv_income=0.0,
        ltcg_eligible=0.0,
        zero_year=years[BASE_YEAR],
        fills=fills,
    )
    assert row.cost_12 == pytest.approx(curve[0].all_in, abs=1.0)
    assert row.cost_22 == pytest.approx(curve[1].all_in, abs=1.0)
    blind = all_in_at_conversion(hh, base, row.fill_22, 0.0)
    assert row.cost_22 > blind.all_in + 1_000.0


def test_no_draw_year_summary_is_unchanged() -> None:
    hh = _no_draw_hh()
    draws = zero_conversion_ira_draws(hh)
    base = base_income_for_year(hh, BASE_YEAR, ira_draw=draws[BASE_YEAR])
    row = compute_multi_year_summary(hh)[0]
    assert row.cost_12 == pytest.approx(all_in_at_conversion(hh, base, row.fill_12, 0.0).all_in)
    assert row.cost_22 == pytest.approx(all_in_at_conversion(hh, base, row.fill_22, 0.0).all_in)
    assert row.irmaa_safe == irmaa_safe_max(hh, base, row_threshold(hh), 0.0, 0.0) or (
        row.irmaa_safe is None
    )
