"""Audit 2026-10-18 W3: ACA + IRMAA Explorer (F-01, F-11, F-12, F-14, F-37).

All households are synthetic.

- F-01: the §36B non-taxable-SS add-back is recomputed per swept MAGI point,
  and the 400% FPL cliff marker sits where the per-point ACA MAGI crosses it.
- F-11: every timeline year prices IRMAA room/tier on that year's own
  no-conversion MAGI, not the base year's.
- F-12: a spouse who is dead by the payment year is not a Medicare beneficiary.
- F-14: the Explorer's tax/marginal-rate curves keep LTCG + qualified dividends
  preferential and muni interest out of AGI.
- F-37: the Part B IRMAA is a fixed multiple of the standard premium, so an
  edited base scales it up instead of shrinking it.
"""

from __future__ import annotations

import math

import pytest

from engine.aca import aca_subsidy, aca_subsidy_loss, fpl_for
from engine.aca_irmaa_compute import (
    _nontaxable_ss,
    compute_cost_curves,
    compute_year_by_year_timeline,
    default_base_magi,
)
from engine.irmaa import irmaa_next_threshold, irmaa_surcharge
from engine.sweet_spot_compute import (
    base_income_for_year,
    zero_conversion_sale_gains,
    zero_conversion_years,
)
from engine.tax import (
    LTCG_THRESHOLDS_MFJ,
    deductions,
    federal_tax,
    marginal_rate,
    senior_bonus_deduction,
)
from models.household import Household, SurvivorScenario
from models.ytd_income import YTDSnapshot

# --------------------------------------------------------------------------- F-01

_BENCH = 24_000.0


def _ss_aca_household() -> Household:
    return Household(
        your_age=63,
        spouse_age=63,
        grants=[],
        your_ss_fra=1_800.0,
        spouse_ss_fra=1_800.0,
        your_ss_start_age=62,
        spouse_ss_start_age=62,
        your_aca_enrolled=True,
        spouse_aca_enrolled=True,
        aca_enhanced_subsidies_active=False,
        aca_benchmark_premium_annual=_BENCH,
    )


def _true_aca_magi(hh: Household, p: float) -> float:
    return p + _nontaxable_ss(hh, 63, 63, other_income=p, filing_status="MFJ")


def test_f01_aca_curves_use_per_point_nontaxable_ss() -> None:
    hh = _ss_aca_household()
    points = [float(p) for p in range(20_000, 121_000, 5_000)]
    base = 30_000.0
    curves = compute_cost_curves(points, base, 0.0, hh, year=2026, cpi=0.0)
    for i, p in enumerate(points):
        expected = aca_subsidy(
            _true_aca_magi(hh, p),
            benchmark=_BENCH,
            enhanced_subsidies_active=False,
            filing_status="MFJ",
            year=2026,
            cpi=0.0,
        )
        assert curves.aca_subsidy_vals[i] == pytest.approx(expected, abs=1.0), p
        loss = aca_subsidy_loss(
            _true_aca_magi(hh, base),
            _true_aca_magi(hh, p),
            benchmark=_BENCH,
            enhanced_subsidies_active=False,
            filing_status="MFJ",
            year=2026,
            cpi=0.0,
        )
        assert curves.aca_subsidy_loss_vals[i] == pytest.approx(loss, abs=1.0), p


def test_f01_cliff_marker_is_where_per_point_aca_magi_crosses_400_fpl() -> None:
    hh = _ss_aca_household()
    curves = compute_cost_curves([30_000.0], 30_000.0, 0.0, hh, year=2026, cpi=0.0)
    cliff = curves.aca_cliff_magi
    assert cliff is not None
    four_fpl = 4.0 * fpl_for("MFJ", 2026, 0.0)
    assert _true_aca_magi(hh, cliff) == pytest.approx(four_fpl, abs=1.0)
    # Not the base-point constant offset the old marker used.
    assert cliff != pytest.approx(four_fpl - curves.nontaxable_ss, abs=1_000.0)


def test_f01_no_cliff_marker_without_aca_or_with_enhanced_subsidies() -> None:
    hh = _ss_aca_household()
    hh.aca_enhanced_subsidies_active = True
    curves = compute_cost_curves([30_000.0], 30_000.0, 0.0, hh, year=2026, cpi=0.0)
    assert curves.aca_cliff_magi is None


# --------------------------------------------------------------------------- F-11


def _projected_magi(hh: Household, year: int, base_magi: float) -> float:
    if year == hh.base_year:
        return base_magi
    zero = zero_conversion_years(hh, ytd=None, net_inv_income=0.0)
    yr = zero.get(year)
    if yr is None:
        return base_income_for_year(hh, year, ytd=None).base_magi
    return base_income_for_year(
        hh,
        year,
        ytd=None,
        ira_draw=yr.forced_your_ira_draw + yr.forced_spouse_ira_draw,
        sale_gain=zero_conversion_sale_gains(hh, zero).get(year, 0.0),
        brokerage=yr.brokerage_balance,
    ).base_magi


def test_f11_timeline_irmaa_room_uses_each_years_own_magi() -> None:
    hh = Household()
    base_magi = default_base_magi(hh, ytd=None, net_inv_income=0.0)
    rows = compute_year_by_year_timeline(hh, base_magi, years=20, cpi=hh.cpi_assumption)
    seen_rooms: set[float] = set()
    for row in rows:
        if row.irmaa_room is None:
            continue
        expected = irmaa_next_threshold(
            _projected_magi(hh, row.year, base_magi),
            filing_status=hh.filing_status,
            year=row.year + 2,
            cpi=hh.cpi_assumption,
        )
        assert row.irmaa_room == pytest.approx(expected, abs=1.0), row.year
        seen_rooms.add(round(row.irmaa_room))
    assert len(seen_rooms) > 3  # the rooms are no longer one repeated number


# --------------------------------------------------------------------------- F-12


def test_f12_dead_spouse_is_not_a_medicare_beneficiary_in_payment_year() -> None:
    hh = Household(
        your_age=63,
        spouse_age=55,
        survivor=SurvivorScenario(who_dies="you", death_year=2028),
    )
    rows = {
        r.year: r
        for r in compute_year_by_year_timeline(hh, 150_000.0, years=6, cpi=hh.cpi_assumption)
    }
    # Payment years 2029/2030: "you" is dead, the survivor is 58-59 (not on Medicare).
    for year in (2027, 2028):
        assert rows[year].irmaa_tier is None, year
        assert rows[year].irmaa_room is None, year
    # Income 2026 is paid in 2028 while "you" (65) is alive: still a beneficiary.
    assert rows[2026].irmaa_tier is not None


# --------------------------------------------------------------------------- F-14


def test_f14_ltcg_and_qualified_dividends_stay_preferential() -> None:
    hh = Household(your_age=60, spouse_age=60, grants=[])
    ytd = YTDSnapshot(tax_year=hh.base_year, qualified_dividends_ytd=60_000.0)
    magi = 150_000.0
    curves = compute_cost_curves([magi], magi, 0.0, hh, year=hh.base_year, cpi=0.0, ytd=ytd)
    ded = deductions(60, 60, hh.std_deduction, hh.senior_extra, year=hh.base_year, cpi=0.0)
    ordinary_taxable = magi - 60_000.0 - ded
    zero_ceiling = LTCG_THRESHOLDS_MFJ[0]
    at_15 = (ordinary_taxable + 60_000.0) - max(ordinary_taxable, zero_ceiling)
    expected_tax = federal_tax(ordinary_taxable, year=hh.base_year, cpi=0.0) + 0.15 * at_15
    assert curves.fed_tax_vals[0] == pytest.approx(expected_tax, abs=1.0)
    ordinary_rate = marginal_rate(ordinary_taxable, year=hh.base_year, cpi=0.0)
    assert curves.marginal_rate_vals[0] == pytest.approx(ordinary_rate + 0.15, abs=1e-6)


def test_f14_muni_interest_is_out_of_agi_and_the_senior_bonus_phaseout() -> None:
    hh = Household(your_age=66, spouse_age=66, grants=[])
    ytd = YTDSnapshot(tax_year=hh.base_year, tax_exempt_interest_ytd=20_000.0)
    magi = 170_000.0  # IRMAA-basis MAGI including the $20K muni
    curves = compute_cost_curves([magi], magi, 0.0, hh, year=hh.base_year, cpi=0.0, ytd=ytd)
    ded = deductions(66, 66, hh.std_deduction, hh.senior_extra, year=hh.base_year, cpi=0.0)
    bonus = senior_bonus_deduction(
        66, 66, magi - 20_000.0, year=hh.base_year, cpi=0.0, filing_status="MFJ"
    )
    expected = federal_tax(magi - 20_000.0 - ded - bonus, year=hh.base_year, cpi=0.0)
    assert curves.fed_tax_vals[0] == pytest.approx(expected, abs=1.0)


# --------------------------------------------------------------------------- F-37


def _part_b_per_person_month(base_monthly: float, magi: float, year: int = 2026) -> float:
    total = irmaa_surcharge(
        magi,
        num_people=1,
        base_part_b=base_monthly * 12,
        filing_status="MFJ",
        year=year,
        cpi=0.0,
        medical_cpi=0.0,
    )
    return total / 12.0 - _PART_D_MONTHLY[magi]


# Tier 2 (274k-342k) and tier 5 (750k+) Part D surcharges, $/month (2026 CMS).
_PART_D_MONTHLY = {300_000.0: 37.50, 800_000.0: 91.00}


@pytest.mark.parametrize("base", [202.90, 209.50, 250.00])
def test_f37_part_b_irmaa_scales_with_the_edited_base(base: float) -> None:
    # CMS 2026 published surcharges over the 202.90 standard premium: tier 2
    # is 202.90 (1.0x) and tier 5 is 487.00 (~2.4x). They scale with the base.
    assert _part_b_per_person_month(base, 300_000.0) == pytest.approx(base, abs=0.01)
    assert _part_b_per_person_month(base, 800_000.0) == pytest.approx(
        base * 487.00 / 202.90, abs=0.01
    )


def test_f37_published_2026_figures_stay_exact_at_the_default_base() -> None:
    assert math.isclose(_part_b_per_person_month(202.90, 300_000.0), 405.80 - 202.90, abs_tol=1e-9)
    assert math.isclose(_part_b_per_person_month(202.90, 800_000.0), 689.90 - 202.90, abs_tol=1e-9)
