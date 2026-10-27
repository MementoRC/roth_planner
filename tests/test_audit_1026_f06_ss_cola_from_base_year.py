"""audit-1026 F-06: the SS FRA benefit is a today's-dollar (base-year) statement
figure, so COLA compounds from the base year, not from the claim year.

Synthetic households only.
"""

from __future__ import annotations

import pytest

from engine.aca_irmaa_compute import _nontaxable_ss
from engine.headroom import compute_headroom
from engine.ira import ss_benefit_at_age
from engine.scenario import run_scenario
from engine.scenario_compute import social_security_benefits
from engine.scenario_types import ConversionPlan
from engine.sweet_spot_compute import base_income_for_year
from models.household import Household
from models.ytd_income import YTDSnapshot
from views.ytd_income._partials._analysis import ytd_combined_ss

COLA = 0.025
YEAR = 2035  # 9 years after the base year; you are 70, the spouse 64


def _hh(*, your_age: int = 61, start: int = 70, cola: float = COLA) -> Household:
    return Household(
        your_age=your_age,
        spouse_age=55,
        base_year=2026,
        grants=[],
        stock_price_now=0.0,
        stock_price_late=0.0,
        your_ss_fra=3_000.0,
        spouse_ss_fra=1_500.0,
        your_ss_start_age=start,
        spouse_ss_start_age=62,
        cpi_assumption=0.0,
        ss_cola=cola,
        growth_rate=0.0,
        filing_status="MFJ",
        brokerage_start=0.0,
        living_expenses=40_000.0,
        your_ira=500_000.0,
        spouse_ira=500_000.0,
    )


def _expected(hh: Household) -> tuple[float, float]:
    grow = (1 + COLA) ** (YEAR - hh.base_year)
    your = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age) * grow
    spouse = ss_benefit_at_age(hh.spouse_ss_fra, hh.spouse_ss_start_age, hh.spouse_fra_age) * grow
    return your, spouse


def test_f06_ss_cola_run_scenario_grows_from_base_year() -> None:
    hh = _hh()
    your, spouse = _expected(hh)
    plan = ConversionPlan(your_conversions={}, spouse_conversions={})
    yr = run_scenario(hh, plan, "f06", end_age=75).years[YEAR - hh.base_year]
    assert yr.your_ss == pytest.approx(your, rel=1e-9)
    assert yr.combined_ss == pytest.approx(your + spouse, rel=1e-9)


def test_f06_ss_cola_benefits_helper_grows_from_base_year() -> None:
    hh = _hh()
    your, spouse = _expected(hh)
    got = social_security_benefits(
        hh,
        hh.your_age_in(YEAR),
        hh.spouse_age_in(YEAR),
        survivor_active=False,
        who_dies=None,
        death_year=None,
    )
    assert got == pytest.approx((your, spouse), rel=1e-9)


def test_f06_ss_cola_sweet_spot_parity() -> None:
    hh = _hh()
    your, spouse = _expected(hh)
    assert base_income_for_year(hh, YEAR).combined_ss == pytest.approx(your + spouse, rel=1e-9)


def test_f06_ss_cola_aca_irmaa_parity() -> None:
    hh = _hh()
    # Oracle: the same household with no COLA but FRA benefits pre-grown to YEAR.
    grow = (1 + COLA) ** (YEAR - hh.base_year)
    oracle = _hh(cola=0.0)
    oracle.your_ss_fra *= grow
    oracle.spouse_ss_fra *= grow
    ya, sa = hh.your_age_in(YEAR), hh.spouse_age_in(YEAR)
    kw = {"other_income": 40_000.0, "filing_status": "MFJ"}
    assert _nontaxable_ss(hh, ya, sa, **kw) == pytest.approx(_nontaxable_ss(oracle, ya, sa, **kw))


def test_f06_ss_cola_headroom_base_year_has_no_growth() -> None:
    # Already collecting in the base year: the benefit is the statement figure
    # itself (zero years of growth), so the COLA rate cannot move base-year MAGI.
    ytd = YTDSnapshot(tax_year=2026, wages_ytd=60_000.0)
    with_cola = compute_headroom(_hh(your_age=72, start=65), ytd)
    without = compute_headroom(_hh(your_age=72, start=65, cola=0.0), ytd)
    assert with_cola.projected_magi_base == pytest.approx(without.projected_magi_base, rel=1e-12)


def test_f06_ss_cola_ytd_analysis_matches_engine_for_collecting_person() -> None:
    # Both already collecting in the base year (you 72/claimed 65, spouse 64/claimed 62):
    # the YTD analysis must show the engine's figure, not a claim-year-grown one.
    hh = _hh(your_age=72, start=65)
    hh.spouse_age = 64
    engine = social_security_benefits(
        hh, hh.your_age, hh.spouse_age, survivor_active=False, who_dies=None, death_year=None
    )
    assert ytd_combined_ss(hh) == pytest.approx(sum(engine), rel=1e-12)
