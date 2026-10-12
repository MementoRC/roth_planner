"""Audit 2026-10-08, wave W1: LTCG stacking and brokerage cost basis.

F-16  YTD short-term gains were credited as new spendable cash.
F-17  Base-year forced-draw gain stacked from ordinary income, not on top of YTD LTCG.
F-18  Forecast LTCG stack ignored deduction left unused by ordinary income.
F-19  Turnover-realized gains never raised brokerage cost basis.
F-26  Sweet Spot _ltcg_stack_tax ignored deduction left unused by ordinary income.

All figures are synthetic.
"""

import dataclasses

import pytest

from engine.scenario import ConversionPlan, run_no_conversion, run_scenario
from engine.sweet_spot_compute import BaseIncome, all_in_at_conversion
from engine.tax import LTCG_RATES_MFJ, LTCG_THRESHOLDS_MFJ, deductions
from models.household import Household
from models.ytd_income import YTDSnapshot


def _pref_tax(ordinary_gross: float, pref: float, ded: float) -> float:
    """IRC §1(h) worksheet shape: preferential income sits between ordinary
    taxable income and total taxable income (2026 MFJ, cpi 0)."""
    t0, t1 = LTCG_THRESHOLDS_MFJ
    start = max(0.0, ordinary_gross - ded)
    end = max(0.0, ordinary_gross + pref - ded)
    at_15 = max(0.0, min(end, t1) - max(start, t0))
    at_20 = max(0.0, end - max(start, t1))
    return at_15 * LTCG_RATES_MFJ[1] + at_20 * LTCG_RATES_MFJ[2]


def _hh(**kw) -> Household:
    hh = Household()
    for k, v in kw.items():
        assert k in {f.name for f in dataclasses.fields(hh)}, k
        setattr(hh, k, v)
    return hh


class TestF16YtdStcgIsNotNewCash:
    def _run(self, **ytd):
        hh = _hh(
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            brokerage_start=500_000.0,
            brokerage_start_basis=500_000.0,
            living_expenses=60_000.0,
            cpi_assumption=0.0,
        )
        return run_no_conversion(hh, end_age=70, ytd=YTDSnapshot(tax_year=2026, **ytd)).years

    def test_net_zero_capital_gain_matches_no_gain(self):
        base = self._run()
        netted = self._run(stcg_ytd=50_000.0, ltcg_ytd=-50_000.0)
        assert netted[0].income_needed == pytest.approx(base[0].income_needed, abs=1.0)
        assert netted[1].brokerage_balance == pytest.approx(base[1].brokerage_balance, abs=1.0)

    def test_stcg_only_adds_its_tax_to_the_need(self):
        base = self._run()
        stcg = self._run(stcg_ytd=50_000.0)
        extra_tax = stcg[0].federal_tax_amt - base[0].federal_tax_amt
        assert extra_tax > 0
        assert stcg[0].income_needed == pytest.approx(base[0].income_needed + extra_tax, abs=1.0)


class TestF17BaseYearForcedDrawStacksOnYtdLtcg:
    def test_combined_preferential_tax_matches_worksheet(self):
        hh = _hh(
            base_year=2026,
            your_age=61,
            spouse_age=60,
            your_ira=500_000.0,
            spouse_ira=500_000.0,
            your_roth=0.0,
            spouse_roth=0.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
            exercise_schedule=None,
            living_expenses=100_000.0,
            brokerage_start=1_000_000.0,
            brokerage_start_basis=500_000.0,
            cpi_assumption=0.0,
        )
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=150_000.0)
        yr = run_scenario(hh, ConversionPlan(), end_age=62, ytd=ytd).years[0]
        assert yr.forced_brokerage_draw > 0
        gain = yr.forced_brokerage_draw * 0.5  # basis fraction 0.5
        expected = _pref_tax(yr.combined_gross, 150_000.0 + gain, yr.total_deductions)
        assert yr.ytd_ltcg_tax + yr.brokerage_gain_tax == pytest.approx(expected, abs=1.0)


class TestF18ForecastStackUsesUnusedDeduction:
    def _hh(self) -> Household:
        return _hh(
            base_year=2026,
            your_age=61,
            spouse_age=60,
            your_ira=500_000.0,
            spouse_ira=0.0,
            your_roth=0.0,
            spouse_roth=0.0,
            your_ss_fra=2_000.0,
            spouse_ss_fra=2_000.0,
            ss_start_age=70,
            living_expenses=0.0,
            brokerage_start=3_000_000.0,
            brokerage_start_basis=3_000_000.0,
            brok_turnover=0.5,
            growth_rate=0.07,
            grants=[],
            exercise_schedule=None,
            cpi_assumption=0.0,
        )

    def test_no_phantom_gain_tax_below_total_taxable_threshold(self):
        years = run_scenario(self._hh(), ConversionPlan(), end_age=63).years
        for yr in years[:4]:
            assert yr.combined_gross < yr.total_deductions  # precondition
            assert yr.brokerage_gain_tax == pytest.approx(0.0, abs=0.01), yr.year

    def test_conversion_ltcg_cost_uses_capped_base_leg(self):
        plan = ConversionPlan(your_conversions={2027: 40_000.0})
        yr = next(y for y in run_scenario(self._hh(), plan, end_age=63).years if y.year == 2027)
        assert yr.brokerage_gain_tax > 0
        # The no-conversion leg sits wholly in the 0% band, so the whole
        # with-conversion gain tax is the conversion's marginal LTCG cost.
        assert yr.conversion_ltcg_cost == pytest.approx(yr.brokerage_gain_tax, abs=0.01)


class TestF19TurnoverGainsRaiseBasis:
    def _years(self, turnover: float):
        hh = _hh(
            base_year=2026,
            your_age=61,
            spouse_age=61,
            your_ira=0.0,
            spouse_ira=0.0,
            your_roth=0.0,
            spouse_roth=0.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
            exercise_schedule=None,
            growth_rate=0.07,
            expense_inflation=0.0,
            living_expenses=0.0,
            brokerage_start=1_000_000.0,
            brokerage_start_basis=1_000_000.0,
            brok_turnover=turnover,
            cpi_assumption=0.0,
        )
        return run_scenario(hh, ConversionPlan(), end_age=66).years

    def test_first_year_basis_includes_turnover_gain(self):
        none, some = self._years(0.0)[0], self._years(0.30)[0]
        turnover_gain = 1_000_000.0 * 0.07 * 0.30
        assert some.brokerage_basis - none.brokerage_basis == pytest.approx(turnover_gain, abs=1.0)

    def test_basis_never_exceeds_balance(self):
        for yr in self._years(0.30):
            assert yr.brokerage_basis <= yr.brokerage_balance_end + 0.01


class TestF26SweetSpotStackUsesUnusedDeduction:
    def test_all_in_prices_ltcg_pushed_over_zero_band(self):
        hh = _hh(filing_status="MFJ", your_aca_enrolled=False, spouse_aca_enrolled=False)
        year, cpi, ya, sa = 2026, 0.0, 58, 56
        ded = deductions(ya, sa, hh.std_deduction, hh.senior_extra, year=year, cpi=cpi)
        ordinary, ltcg = 5_000.0, 115_000.0
        base = BaseIncome(
            ya=ya,
            sa=sa,
            year=year,
            cpi=cpi,
            opt=0.0,
            combined_ss=0.0,
            base_gross=ordinary,
            base_magi=ordinary + ltcg,
            total_ded=ded,
            ded_base=ded,
            ytd_magi=ordinary + ltcg,
            ytd_niit_magi=ordinary + ltcg,
            ytd_ordinary=ordinary,
            ytd_investment_income=ordinary + ltcg,
        )
        for conv in (0.0, 20_000.0, 40_000.0):
            r = all_in_at_conversion(hh, base, conv, 0.0, ltcg_eligible=ltcg)
            expected = _pref_tax(ordinary + conv, ltcg, ded) - _pref_tax(ordinary, ltcg, ded)
            assert r.ltcg_delta == pytest.approx(expected, abs=0.01), conv
