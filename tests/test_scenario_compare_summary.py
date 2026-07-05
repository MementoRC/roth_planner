"""Audit 0705 #2 — comparator summary all_in_cost must fold in ACA+NIIT, and the savings
delta must use a positive-means-saved convention (consistent with the chart caption)."""

import pytest

from engine.scenario_compare import compute_summary_rows
from engine.scenario_types import ConversionPlan, ScenarioResult, YearResult
from models.household import Household


def _yr(**kw) -> YearResult:
    return YearResult(year=2026, your_age=61, spouse_age=55, phase="clean", **kw)


def _result(name: str, years: list[YearResult]) -> ScenarioResult:
    hh = Household(your_age=61, spouse_age=55, your_ira=1_000_000, spouse_ira=1_000_000)
    return ScenarioResult(name=name, years=years, household=hh, plan=ConversionPlan())


class TestComparatorSummaryAllIn:
    """The 'Total All-In Cost' the Comparator uses to pick a strategy must include the same
    ACA-loss and NIIT costs the Sweet Spot / ACA+IRMAA pages headline."""

    def test_all_in_cost_folds_in_aca_and_niit(self):
        baseline = _result(
            "baseline",
            [_yr(federal_tax_amt=1000.0, irmaa_cost=200.0, brokerage_gain_tax=100.0)],
        )
        scen = _result(
            "convert",
            [
                _yr(
                    federal_tax_amt=1500.0,
                    irmaa_cost=300.0,
                    brokerage_gain_tax=100.0,
                    aca_loss=400.0,
                    niit_cost=250.0,
                )
            ],
        )
        rows = compute_summary_rows([baseline, scen], baseline)
        b, s = rows[0], rows[1]
        # all_in_cost must fold in ACA loss + NIIT (tax + irmaa + brok + aca + niit)
        assert s.all_in_cost == pytest.approx(1500.0 + 300.0 + 100.0 + 400.0 + 250.0)
        assert b.all_in_cost == pytest.approx(1000.0 + 200.0 + 100.0)

    def test_savings_vs_baseline_is_positive_when_cheaper(self):
        baseline = _result("baseline", [_yr(federal_tax_amt=2000.0)])
        cheaper = _result("convert", [_yr(federal_tax_amt=1200.0)])
        rows = compute_summary_rows([baseline, cheaper], baseline)
        # positive = SAVED money vs baseline (matches the cumulative-benefit chart caption)
        assert rows[1].savings_vs_baseline == pytest.approx(800.0)
        assert rows[0].savings_vs_baseline == pytest.approx(0.0)
