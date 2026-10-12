"""Audit 2026-10-08, wave W2: NIIT payment and waterfall re-sizing.

F-15  NIIT was computed but never paid out of any balance. Per the user's
      decision (D1) it is now paid like ordinary income tax: it reduces the
      year's spendable income, so it raises income_needed / lowers excess_rmd
      and is funded by the same waterfall.
F-20  The waterfall's brokerage draw realizes a gain that moves MAGI, ACA
      premium, tax and NIIT, but the draw was never re-sized, so the extra
      outflow was neither funded nor reported as unfunded.

All figures are synthetic.
"""

import dataclasses

import pytest

import engine.scenario as scenario_mod
from engine.scenario import ConversionPlan, run_scenario
from models.household import Household


def _hh(**kw) -> Household:
    hh = Household()
    for k, v in kw.items():
        assert k in {f.name for f in dataclasses.fields(hh)}, k
        setattr(hh, k, v)
    return hh


def _cash_raised(yr) -> float:
    return (
        yr.forced_brokerage_draw
        + yr.forced_your_ira_draw
        + yr.forced_spouse_ira_draw
        + yr.forced_your_roth_draw
        + yr.forced_spouse_roth_draw
    )


def _early_aca_retirees(basis: float | None) -> Household:
    return _hh(
        base_year=2026,
        your_age=60,
        spouse_age=60,
        your_ira=500_000.0,
        spouse_ira=500_000.0,
        your_roth=0.0,
        spouse_roth=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
        exercise_schedule=None,
        filing_status="MFJ",
        growth_rate=0.0,
        brok_turnover=0.0,
        expense_inflation=0.0,
        cpi_assumption=0.0,
        brokerage_start=1_000_000.0,
        brokerage_start_basis=basis,
        living_expenses=90_000.0,
        your_aca_enrolled=True,
        spouse_aca_enrolled=True,
    )


_PLAN = ConversionPlan(extra_withdrawals=dict.fromkeys(range(2026, 2035), 50_000.0))


class TestF20BrokerageDrawIsResized:
    def test_low_basis_draw_funds_the_premium_it_triggers(self):
        yr = run_scenario(_early_aca_retirees(100_000.0), _PLAN, end_age=62).years[0]
        assert yr.forced_brokerage_draw > 0
        # Every dollar of this year's need -- including the ACA premium the
        # draw's own gain pushed over the cliff -- is raised or reported.
        assert _cash_raised(yr) + yr.unfunded_need == pytest.approx(yr.income_needed, abs=2.0)
        assert yr.unfunded_need == 0.0

    def test_low_basis_ends_poorer_than_full_basis(self):
        full = run_scenario(_early_aca_retirees(None), _PLAN, end_age=62).years[0]
        low = run_scenario(_early_aca_retirees(100_000.0), _PLAN, end_age=62).years[0]
        extra_premium = low.aca_premium_cost - full.aca_premium_cost
        assert extra_premium > 1_000
        assert low.brokerage_balance_end <= full.brokerage_balance_end - extra_premium + 2.0

    def test_full_basis_draw_still_balances(self):
        yr = run_scenario(_early_aca_retirees(None), _PLAN, end_age=62).years[0]
        assert _cash_raised(yr) == pytest.approx(yr.income_needed, abs=2.0)


def _rich_surplus_household() -> Household:
    return _hh(
        base_year=2026,
        your_age=66,
        spouse_age=66,
        your_ira=2_000_000.0,
        spouse_ira=0.0,
        your_roth=0.0,
        spouse_roth=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
        exercise_schedule=None,
        filing_status="MFJ",
        expense_inflation=0.0,
        cpi_assumption=0.0,
        brokerage_start=2_000_000.0,
        brokerage_start_basis=1_000_000.0,
        living_expenses=50_000.0,
    )


class TestF15NiitIsPaid:
    def _pair(self, monkeypatch, hh, plan):
        real = run_scenario(hh, plan, end_age=68).years
        monkeypatch.setattr(scenario_mod, "niit", lambda *a, **k: 0.0)
        zero = run_scenario(hh, plan, end_age=68).years
        return real, zero

    def test_niit_reduces_the_surplus_reinvested(self, monkeypatch):
        plan = ConversionPlan(extra_withdrawals={2026: 400_000.0, 2027: 400_000.0})
        real, zero = self._pair(monkeypatch, _rich_surplus_household(), plan)
        y0, z0 = real[0], zero[0]
        assert y0.niit_cost > 1_000
        assert z0.niit_cost == 0.0
        assert y0.income_needed == 0.0
        assert y0.excess_rmd > 0
        assert y0.excess_rmd == pytest.approx(z0.excess_rmd - y0.niit_cost, abs=1.0)
        assert y0.brokerage_balance_end == pytest.approx(
            z0.brokerage_balance_end - y0.niit_cost, abs=1.0
        )

    def test_waterfall_funds_niit(self):
        hh = _rich_surplus_household()
        hh.living_expenses = 450_000.0
        # Ordinary withdrawals push MAGI past the NIIT threshold; the rest of the
        # need is drawn by the waterfall. Year 1: the base year suppresses
        # forecast realized gains (YTD covers it).
        plan = ConversionPlan(extra_withdrawals={2026: 300_000.0, 2027: 300_000.0})
        yr = run_scenario(hh, plan, end_age=68).years[1]
        assert yr.niit_cost > 0
        assert yr.income_needed > 0
        assert _cash_raised(yr) + yr.unfunded_need == pytest.approx(yr.income_needed, abs=2.0)
