"""Regression tests: ACA benchmark must scale by enrollee count across all consumers."""

import pytest

from engine.aca import aca_subsidy, aca_subsidy_loss
from engine.aca_irmaa_compute import compute_cost_curves, compute_year_by_year_timeline
from engine.scenario_compute import compute_aca
from engine.sweet_spot_compute import BaseIncome, all_in_at_conversion
from models.household import Household


def test_compute_aca_clawback_scales_benchmark_single_enrollee() -> None:
    # R2-#1: the excess-APTC clawback must use the same enrollee-scaled benchmark
    # as the subsidy-loss path. One enrollee (you 60 enrolled, spouse 66 -> off ACA)
    # => effective benchmark is half the couple figure.
    benchmark = 21_600.0
    magi = 60_000.0
    advance = 10_000.0
    _, _, clawback = compute_aca(
        magi=magi,
        combined_ss=0.0,
        taxable_ss_amt=0.0,
        your_conversion=0.0,
        spouse_conversion=0.0,
        ya=60,
        sa=66,
        your_aca_enrolled=True,
        spouse_aca_enrolled=True,
        aca_benchmark_premium_annual=benchmark,
        aca_enhanced_subsidies_active=True,
        advance_aptc_annual=advance,
        current_filing_status="MFJ",
        year=2026,
        cpi=0.0,
    )
    half_ptc = aca_subsidy(
        magi,
        benchmark * 0.5,
        enhanced_subsidies_active=True,
        filing_status="MFJ",
        year=2026,
        cpi=0.0,
    )
    full_ptc = aca_subsidy(
        magi, benchmark, enhanced_subsidies_active=True, filing_status="MFJ", year=2026, cpi=0.0
    )
    assert clawback == pytest.approx(advance - half_ptc)
    assert clawback != pytest.approx(advance - full_ptc)


def test_compute_cost_curves_scales_benchmark_single_enrollee() -> None:
    # R1 #2: ACA Explorer curves must scale the benchmark by enrollee count.
    hh = Household()
    hh.your_aca_enrolled = True
    hh.spouse_aca_enrolled = False  # one enrollee (both under 65 by default)
    hh.aca_enhanced_subsidies_active = True
    magi = 80_000.0
    cc = compute_cost_curves([magi], base_magi=magi, net_inv_income=0.0, hh=hh, year=2026, cpi=0.0)
    expected = aca_subsidy(
        magi,
        hh.aca_benchmark_premium_annual * 0.5,
        enhanced_subsidies_active=True,
        filing_status=hh.filing_status,
        year=2026,
        cpi=0.0,
    )
    assert cc.aca_subsidy_vals[0] == pytest.approx(expected)
    assert expected > 0


def test_sweet_spot_all_in_scales_aca_benchmark_single_enrollee() -> None:
    # R1 #3: Sweet Spot all-in ACA loss must scale the benchmark by enrollee count.
    hh = Household()
    hh.your_aca_enrolled = True
    hh.spouse_aca_enrolled = False  # one enrollee
    hh.aca_enhanced_subsidies_active = True
    base = BaseIncome(
        ya=hh.your_age,
        sa=hh.spouse_age,
        year=2026,
        cpi=0.0,
        opt=40_000.0,
        combined_ss=0.0,
        base_gross=40_000.0,
        base_magi=40_000.0,
        total_ded=0.0,
        ded_base=0.0,
        ytd_magi=0.0,
    )
    res = all_in_at_conversion(hh, base, conv=20_000.0, net_inv_income=0.0)
    expected = aca_subsidy_loss(
        40_000.0,
        60_000.0,
        benchmark=hh.aca_benchmark_premium_annual * 0.5,
        enhanced_subsidies_active=True,
        filing_status=hh.filing_status,
        year=2026,
        cpi=0.0,
    )
    assert res.aca_loss == pytest.approx(expected)
    assert expected > 0


def test_timeline_scales_benchmark_by_yearly_enrollee_count() -> None:
    # Timeline consumer: benchmark scales by how many spouses are on ACA THAT year.
    hh = Household()
    hh.your_aca_enrolled = True
    hh.spouse_aca_enrolled = True
    hh.aca_enhanced_subsidies_active = True
    base_magi = 80_000.0
    rows = compute_year_by_year_timeline(hh, base_magi=base_magi, years=20, cpi=0.0)

    def both(r):
        return (
            r.you_age is not None
            and r.spouse_age is not None
            and r.you_age < 65
            and r.spouse_age < 65
        )

    def exactly_one(r):
        you = r.you_age is not None and r.you_age < 65
        sp = r.spouse_age is not None and r.spouse_age < 65
        return you != sp

    r_both = next(r for r in rows if both(r))
    r_one = next(r for r in rows if exactly_one(r))
    assert r_both.aca_subsidy == pytest.approx(
        aca_subsidy(
            base_magi,
            hh.aca_benchmark_premium_annual,
            enhanced_subsidies_active=True,
            filing_status="MFJ",
            year=r_both.year,
            cpi=0.0,
        )
    )
    assert r_one.aca_subsidy == pytest.approx(
        aca_subsidy(
            base_magi,
            hh.aca_benchmark_premium_annual * 0.5,
            enhanced_subsidies_active=True,
            filing_status="MFJ",
            year=r_one.year,
            cpi=0.0,
        )
    )
