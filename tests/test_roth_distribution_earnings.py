"""Unit tests for earnings in Roth distributions (Pub 590-B ordering, qualified
distributions, the 10% additional tax) and the exposed IRMAA lookback MAGI."""

from __future__ import annotations

import pytest

from engine.roth_five_year import RothConversionLedger, RothExposure
from engine.scenario import ProjectionState, project_year_with_draw, run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from engine.withdrawal_waterfall import Accounts, WaterfallDraw, solve_waterfall
from models.household import GrowthProfile, Household


def _no_tax(_: float) -> float:
    return 0.0


def _household(**overrides: object) -> Household:
    fields: dict[str, object] = {
        "your_age": 50,
        "spouse_age": 50,
        "filing_status": "MFJ",
        "base_year": 2026,
        "cpi_assumption": 0.0,
        "your_ira": 0.0,
        "spouse_ira": 0.0,
        "your_roth": 1_000.0,
        "spouse_roth": 500.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "grants": [],
        "brokerage_start": 0.0,
        "living_expenses": 0.0,
    }
    fields.update(overrides)
    return Household(**fields)  # type: ignore[arg-type]


def _year_with_roth_draw(
    hh: Household, roth_draw: float, *, your_roth: float, spouse_roth: float
) -> YearResult:
    """Project the base year with a given Roth draw on balances that have grown
    past the principal the ledgers track."""
    state = ProjectionState.initial(hh)
    state.your_roth = your_roth
    state.spouse_roth = spouse_roth
    draw = WaterfallDraw(0.0, 0.0, 0.0, 0.0, 0.0, roth_draw, 0.0, 0, True)
    return project_year_with_draw(0, hh, ConversionPlan(), None, 0.0, state, draw).yr


class TestLedgerEarnings:
    def test_draw_within_principal_has_no_earnings(self) -> None:
        ledger = RothConversionLedger(100.0)
        assert ledger.exposure(2026).earnings_part(100.0) == 0.0

    def test_draw_beyond_principal_is_earnings(self) -> None:
        ledger = RothConversionLedger(100.0)
        ledger.record_conversion(2026, 50.0)
        assert ledger.exposure(2026).earnings_part(180.0) == pytest.approx(30.0)

    def test_earnings_accumulate_across_years(self) -> None:
        ledger = RothConversionLedger()
        ledger.record_conversion(2026, 100.0)
        ledger.record_draw(60.0)
        # 40 of principal left: a 70 draw is 30 of earnings.
        assert ledger.exposure(2027).earnings_part(70.0) == pytest.approx(30.0)
        ledger.record_draw(70.0)
        # Principal is spent, so the whole next draw is earnings.
        assert ledger.exposure(2028).earnings_part(25.0) == 25.0

    def test_untracked_principal_reports_no_earnings(self) -> None:
        assert RothExposure().earnings_part(1_000.0) == 0.0

    def test_not_qualified_under_59_and_a_half(self) -> None:
        ledger = RothConversionLedger()
        ledger.record_conversion(2026, 100.0)
        assert not ledger.earnings_qualified(2040, 59.4)
        assert ledger.earnings_qualified(2040, 59.5)

    def test_not_qualified_inside_the_five_years(self) -> None:
        ledger = RothConversionLedger()
        ledger.record_conversion(2026, 100.0)
        assert not ledger.earnings_qualified(2030, 70.0)
        assert ledger.earnings_qualified(2031, 70.0)

    def test_first_conversion_year_starts_the_period(self) -> None:
        ledger = RothConversionLedger()
        ledger.record_conversion(2026, 100.0)
        ledger.record_conversion(2029, 100.0)
        assert ledger.earnings_qualified(2031, 70.0)

    def test_explicit_first_year_overrides_the_first_conversion(self) -> None:
        ledger = RothConversionLedger(first_year=2015)
        ledger.record_conversion(2026, 100.0)
        assert ledger.earnings_qualified(2026, 70.0)

    def test_unknown_first_year_is_not_qualified_even_at_70(self) -> None:
        assert not RothConversionLedger(100.0).earnings_qualified(2060, 70.0)

    def test_override_first_year_qualifies_after_five_years(self) -> None:
        ledger = RothConversionLedger(100.0, first_year=2020)
        assert not ledger.earnings_qualified(2024, 70.0)
        assert ledger.earnings_qualified(2025, 70.0)

    def test_a_conversion_starts_the_clock_when_no_override(self) -> None:
        ledger = RothConversionLedger(100.0)
        ledger.record_conversion(2026, 10.0)
        assert not ledger.earnings_qualified(2030, 70.0)
        assert ledger.earnings_qualified(2031, 70.0)

    def test_copy_keeps_the_first_year(self) -> None:
        ledger = RothConversionLedger()
        ledger.record_conversion(2026, 100.0)
        assert not ledger.copy().earnings_qualified(2030, 70.0)


class TestWaterfallEarningsPenalty:
    @staticmethod
    def _accounts() -> Accounts:
        return Accounts(
            brokerage=0.0,
            brokerage_basis_fraction=1.0,
            your_ira=0.0,
            spouse_ira=0.0,
            your_roth=1_000.0,
            spouse_roth=0.0,
            your_roth_exposure=RothExposure(free_first=100.0, exposed=0.0, principal=100.0),
        )

    def test_earnings_before_59_and_a_half_carry_the_penalty(self) -> None:
        result = solve_waterfall(150.0, self._accounts(), _no_tax, your_age=50, spouse_age=0)
        earnings = result.roth_draw - 100.0
        assert earnings > 0
        assert result.early_withdrawal_penalty == pytest.approx(0.10 * earnings)
        assert result.roth_draw - result.early_withdrawal_penalty == pytest.approx(150.0)

    def test_no_penalty_from_59_and_a_half(self) -> None:
        result = solve_waterfall(150.0, self._accounts(), _no_tax, your_age=60, spouse_age=0)
        assert result.roth_draw == pytest.approx(150.0)
        assert result.early_withdrawal_penalty == 0.0

    def test_principal_only_draw_has_no_penalty(self) -> None:
        result = solve_waterfall(80.0, self._accounts(), _no_tax, your_age=50, spouse_age=0)
        assert result.early_withdrawal_penalty == 0.0


class TestWaterfallEarningsTax:
    @staticmethod
    def _accounts() -> Accounts:
        return TestWaterfallEarningsPenalty._accounts()

    @staticmethod
    def _flat_tax(roth_draw: float, _ira_draw: float) -> float:
        # 20% on the earnings part (principal is 100).
        return 0.20 * max(roth_draw - 100.0, 0.0)

    def test_draw_is_grossed_up_for_tax_on_earnings(self) -> None:
        result = solve_waterfall(
            150.0, self._accounts(), _no_tax, 60, 0, roth_tax_of=self._flat_tax
        )
        assert result.roth_draw - self._flat_tax(result.roth_draw, 0.0) == pytest.approx(150.0)
        assert result.unfunded == 0.0

    def test_gross_up_exceeds_the_draw_without_earnings_tax(self) -> None:
        plain = solve_waterfall(150.0, self._accounts(), _no_tax, 60, 0)
        taxed = solve_waterfall(150.0, self._accounts(), _no_tax, 60, 0, roth_tax_of=self._flat_tax)
        assert taxed.roth_draw > plain.roth_draw

    def test_no_earnings_tax_on_a_principal_only_draw(self) -> None:
        result = solve_waterfall(80.0, self._accounts(), _no_tax, 60, 0, roth_tax_of=self._flat_tax)
        assert result.roth_draw == pytest.approx(80.0)

    def test_penalty_and_tax_both_gross_up(self) -> None:
        result = solve_waterfall(
            150.0, self._accounts(), _no_tax, 50, 0, roth_tax_of=self._flat_tax
        )
        earnings = result.roth_draw - 100.0
        net = result.roth_draw - 0.10 * earnings - 0.20 * earnings
        assert net == pytest.approx(150.0)


class TestEarningsTaxEndToEnd:
    @staticmethod
    def _run(first_year: int) -> YearResult:
        hh = _household(
            your_age=65,
            spouse_age=65,
            your_roth=1_000_000.0,
            spouse_roth=0.0,
            living_expenses=600_000.0,
            your_roth_growth=GrowthProfile(default_rate=1.0, yield_rate=0.0),
            your_first_roth_year=first_year,
        )
        return run_scenario(hh, ConversionPlan(), end_age=67).years[1]

    def test_taxable_earnings_are_grossed_up_and_funded(self) -> None:
        yr = self._run(first_year=2026)
        assert yr.forced_your_roth_earnings_draw > 100_000.0
        assert yr.unfunded_need == pytest.approx(0.0, abs=1.0)
        after_tax = yr.forced_your_roth_draw - yr.federal_tax_amt - yr.niit_cost
        assert after_tax == pytest.approx(yr.living_expenses, abs=2.0)

    def test_taxable_draw_is_larger_than_the_qualified_one(self) -> None:
        taxable = self._run(first_year=2026)
        qualified = self._run(first_year=2015)
        assert qualified.federal_tax_amt == pytest.approx(0.0)
        assert qualified.forced_your_roth_draw == pytest.approx(qualified.living_expenses, abs=2.0)
        assert taxable.forced_your_roth_draw > qualified.forced_your_roth_draw + 10_000.0

    def test_roth_earnings_are_not_net_investment_income(self) -> None:
        taxable = self._run(first_year=2026)
        # MAGI is far past the NIIT threshold, but with no NII there is no tax.
        assert taxable.magi > 250_000.0
        assert taxable.niit_cost == 0.0


class TestProjectedYear:
    def test_earnings_are_reported_per_person(self) -> None:
        hh = _household()
        # Your Roth grew to 2,000 over 1,000 of principal; the spouse's is all principal.
        yr = _year_with_roth_draw(hh, 2_500.0, your_roth=2_000.0, spouse_roth=500.0)
        assert yr.forced_your_roth_draw == 2_000.0
        assert yr.forced_your_roth_earnings_draw == pytest.approx(1_000.0)
        assert yr.forced_spouse_roth_draw == 500.0
        assert yr.forced_spouse_roth_earnings_draw == 0.0

    def test_spouse_earnings_are_not_your_earnings(self) -> None:
        hh = _household()
        yr = _year_with_roth_draw(hh, 2_600.0, your_roth=1_000.0, spouse_roth=2_000.0)
        assert yr.forced_your_roth_earnings_draw == 0.0
        assert yr.forced_spouse_roth_earnings_draw == pytest.approx(1_600.0 - 500.0)

    def test_earnings_before_qualifying_are_gross_income_and_magi(self) -> None:
        hh = _household()
        principal_only = _year_with_roth_draw(hh, 1_000.0, your_roth=2_000.0, spouse_roth=500.0)
        with_earnings = _year_with_roth_draw(hh, 1_300.0, your_roth=2_000.0, spouse_roth=500.0)
        assert with_earnings.forced_your_roth_earnings_draw == pytest.approx(300.0)
        assert with_earnings.combined_gross - principal_only.combined_gross == pytest.approx(300.0)
        assert with_earnings.magi - principal_only.magi == pytest.approx(300.0)
        assert with_earnings.aca_magi - principal_only.aca_magi == pytest.approx(300.0)

    def test_qualified_earnings_are_not_income(self) -> None:
        # 65, and the five years began in 2015: a qualified distribution.
        hh = _household(your_age=65, spouse_age=65, your_first_roth_year=2015)
        principal_only = _year_with_roth_draw(hh, 1_000.0, your_roth=2_000.0, spouse_roth=500.0)
        with_earnings = _year_with_roth_draw(hh, 1_300.0, your_roth=2_000.0, spouse_roth=500.0)
        assert with_earnings.forced_your_roth_earnings_draw == pytest.approx(300.0)
        assert with_earnings.combined_gross == principal_only.combined_gross
        assert with_earnings.magi == principal_only.magi

    def test_first_roth_year_inside_the_period_makes_earnings_income(self) -> None:
        hh = _household(your_age=65, spouse_age=65, your_first_roth_year=2026)
        principal_only = _year_with_roth_draw(hh, 1_000.0, your_roth=2_000.0, spouse_roth=500.0)
        with_earnings = _year_with_roth_draw(hh, 1_300.0, your_roth=2_000.0, spouse_roth=500.0)
        assert with_earnings.combined_gross - principal_only.combined_gross == pytest.approx(300.0)

    def test_no_draw_reports_no_earnings(self) -> None:
        yr = run_scenario(_household(), ConversionPlan(), end_age=52).years[0]
        assert yr.forced_your_roth_earnings_draw == 0.0
        assert yr.forced_spouse_roth_earnings_draw == 0.0


class TestIrmaaLookbackMagi:
    def test_first_two_years_use_the_year_own_magi(self) -> None:
        years = run_scenario(_household(your_ira=100_000.0), ConversionPlan(), end_age=54).years
        assert years[0].irmaa_lookback_magi == years[0].magi
        assert years[1].irmaa_lookback_magi == years[1].magi

    def test_later_years_use_the_magi_from_two_years_before(self) -> None:
        years = run_scenario(_household(your_ira=100_000.0), ConversionPlan(), end_age=54).years
        assert years[2].irmaa_lookback_magi == years[0].magi
        assert years[3].irmaa_lookback_magi == years[1].magi

    def test_filed_magi_anchor_is_the_lookback(self) -> None:
        hh = _household(prior_year_magi={2024: 12_345.0})
        years = run_scenario(hh, ConversionPlan(), end_age=52).years
        assert years[0].irmaa_lookback_magi == 12_345.0

    def test_conversion_year_without_history_uses_the_zero_conversion_magi(self) -> None:
        hh = _household(your_ira=100_000.0)
        plan = ConversionPlan(your_conversions={2026: 10_000.0}, bracket_cap=False)
        first = run_scenario(hh, plan, end_age=52).years[0]
        assert first.irmaa_lookback_magi == pytest.approx(first.magi - 10_000.0)
