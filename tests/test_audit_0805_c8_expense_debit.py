"""TDD regression tests for audit-0805 finding C8 (re-filed HIGH from LOW).

engine/scenario.py:769-770 splits one quantity ("cash shortfall/surplus vs
living expenses") into two halves:

    yr.income_needed = max(yr.living_expenses - available_income, 0)   # deficit
    yr.excess_rmd    = max(available_income - yr.living_expenses, 0)   # surplus

but the brokerage update at :824-830 only ever consumes the surplus half
(``+ yr.excess_rmd``). ``income_needed`` is computed, stored on ``YearResult``,
and then consumed NOWHERE in engine/ or views/ -- a deficit year is silently
"free": no balance moves to cover it.

Consequence: ``available_income`` correctly subtracts ``yr.federal_tax_amt``,
so a Roth conversion raises tax and raises ``income_needed`` -- but with no
brokerage debit, the conversion's true cash cost never depletes any balance.
Conversions therefore appear costless in the headline projection.

All hand-derivations below use 2026 (BASE_YEAR) MFJ constants directly from
engine/tax.py, not by calling the tax functions under a different name:
  STD_DEDUCTION_MFJ = 32,200 (no senior extra; both filers are 61, under 65)
  BRACKETS_MFJ       = [(24_800, 0.10), (100_800, 0.12), ...]
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_scenario
from models.household import Household


def approx(expected: float, tol: float = 0.01) -> object:
    return pytest.approx(expected, abs=tol)


def _bare_household(**overrides: object) -> Household:
    """MFJ household with every income source zeroed except what a test
    explicitly opts into: no RMDs (age 61, well below RMD start), no grants
    (no option income), no Social Security, and -- critically for hand-
    derivable brokerage math -- growth_rate=0.0 and brok_turnover=0.0 so
    brokerage growth, forecast dividends, and realized LTCG are all exactly
    0.0 every year (compute_brokerage_dividends returns (0.0, 0.0) whenever
    hh.brokerage_growth is None, and realized_gains = brokerage *
    brok_appreciation_rate * brok_turnover collapses to 0.0 when either
    factor is 0.0). expense_inflation=0.0 keeps living_expenses flat across
    years so multi-year math stays simple.
    """
    base: dict[str, object] = {
        "grants": [],
        "your_age": 61,
        "spouse_age": 61,
        "your_ira": 1_000_000.0,
        "spouse_ira": 0.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "filing_status": "MFJ",
        "base_year": 2026,
        "growth_rate": 0.0,
        "brok_turnover": 0.0,
        "expense_inflation": 0.0,
        "brokerage_start": 0.0,
        "living_expenses": 0.0,
    }
    base.update(overrides)
    return Household(**base)  # type: ignore[arg-type]


class TestDeficitDebitsBrokerage:
    """A household whose living expenses exceed available income must see
    its brokerage balance REDUCED by the shortfall, year over year."""

    def test_year1_brokerage_reflects_prior_year_shortfall_debit(self) -> None:
        """brokerage_start=$500,000, living_expenses=$80,000/yr flat, every
        other income source zero (no RMD/SS/option/inherited/extra
        withdrawal, no conversion) -> available_income=$0 every year, so
        income_needed=$80,000 and excess_rmd=$0 every year.

        Hand-derivation (fixed-point, no growth/div/gains -- all forced to
        0.0 by the fixture):
          year0 (2026) begin brokerage = 500,000.00
          year0 end brokerage (correct) = 500,000 + 0 (growth) - 0 (gain tax)
                                           + 0 (div) + 0 (excess_rmd)
                                           - 80,000 (income_needed) = 420,000.00
          year1 (2027) begin brokerage == year0 end brokerage = 420,000.00
          (year1's own income_needed is irrelevant here -- we only assert
          the CARRIED-FORWARD begin balance, i.e. the debit from year0.)

        Pre-fix, income_needed is never subtracted, so year1 begin brokerage
        stays at the undebited 500,000.00.
        """
        hh = _bare_household(brokerage_start=500_000.0, living_expenses=80_000.0)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "c8-deficit", end_age=62)

        yr0 = result.years[0]
        assert yr0.income_needed == approx(80_000.0)
        assert yr0.excess_rmd == approx(0.0)

        yr1 = result.years[1]
        assert yr1.brokerage_balance == approx(420_000.0), (
            f"Expected year1 begin-brokerage=420000.00 (500000 debited by "
            f"the 80000 year0 shortfall), got {yr1.brokerage_balance:.2f} "
            f"-- income_needed is not being subtracted from the brokerage "
            f"balance (engine/scenario.py:824-830)"
        )


class TestSurplusDeficitSymmetry:
    """Surplus (excess_rmd credits brokerage) and deficit (income_needed
    should debit brokerage) are two sides of ONE rule. A pair of households
    differing only in living_expenses, straddling the available_income
    break-even point, must show a CONTINUOUS brokerage delta -- no
    discontinuity where the deficit side is silently free."""

    def _household_with_expenses(self, living_expenses: float) -> Household:
        # extra_withdrawal=$20,000 is the sole income source. It is fully
        # absorbed by the $32,200 MFJ standard deduction (taxable_income =
        # max(20000 - 32200, 0) = 0), so federal_tax_amt = 0.00 and
        # available_income = 20,000.00 exactly, every year.
        return _bare_household(
            brokerage_start=100_000.0,
            living_expenses=living_expenses,
            your_ira=100_000.0,
        )

    def test_brokerage_delta_continuous_across_break_even(self) -> None:
        plan = ConversionPlan(extra_withdrawals={2026: 20_000.0})

        # Surplus side: living_expenses = 19,000 < available_income (20,000).
        # excess_rmd = 20,000 - 19,000 = 1,000.00; income_needed = 0.00.
        # Correct brokerage_end = 100,000 + 1,000 = 101,000.00.
        hh_surplus = self._household_with_expenses(19_000.0)
        result_surplus = run_scenario(hh_surplus, plan, "c8-surplus", end_age=62)
        yr_surplus = result_surplus.years[0]
        assert yr_surplus.excess_rmd == approx(1_000.0)
        assert yr_surplus.income_needed == approx(0.0)
        delta_surplus = result_surplus.years[1].brokerage_balance - 100_000.0
        assert delta_surplus == approx(1_000.0)

        # Deficit side: living_expenses = 21,000 > available_income (20,000).
        # income_needed = 21,000 - 20,000 = 1,000.00; excess_rmd = 0.00.
        # Correct brokerage_end = 100,000 - 1,000 = 99,000.00.
        hh_deficit = self._household_with_expenses(21_000.0)
        result_deficit = run_scenario(hh_deficit, plan, "c8-deficit-sym", end_age=62)
        yr_deficit = result_deficit.years[0]
        assert yr_deficit.income_needed == approx(1_000.0)
        assert yr_deficit.excess_rmd == approx(0.0)
        delta_deficit = result_deficit.years[1].brokerage_balance - 100_000.0

        # THE SYMMETRY: a $1,000 swing in living_expenses on either side of
        # the break-even point must produce an equal-and-opposite brokerage
        # delta -- +1,000.00 vs -1,000.00. Pre-fix, delta_deficit is 0.00
        # (the deficit is free), breaking symmetry with delta_surplus.
        assert delta_deficit == approx(-1_000.0), (
            f"Expected delta_deficit=-1000.00 (mirroring delta_surplus="
            f"{delta_surplus:.2f}), got {delta_deficit:.2f} -- deficit years "
            f"are not debiting the brokerage balance (engine/scenario.py:"
            f"824-830), breaking symmetry with the surplus-credit side"
        )
        assert delta_surplus == approx(-delta_deficit)


class TestConversionCarriesItsCost:
    """THE HEADLINE DEFECT: two runs of the SAME household differing only in
    whether a conversion happens. The conversion's federal tax must reduce
    terminal brokerage relative to the no-conversion run -- pre-fix it does
    not, because income_needed (which absorbs the tax-driven deficit) is
    never subtracted from brokerage.
    """

    def test_conversion_tax_reduces_terminal_brokerage_vs_no_conversion(self) -> None:
        """brokerage_start=$500,000, living_expenses=$80,000/yr flat, no
        other income source -> available_income (no conversion) = $0.

        No-conversion run:
          income_needed = 80,000 - 0 = 80,000.00
          correct brokerage_end (year0) = 500,000 - 80,000 = 420,000.00

        $100,000 conversion run (year 2026 only):
          combined_gross = 100,000.00 (conversion only)
          taxable_income = max(100,000 - 32,200 std_ded, 0) = 67,800.00
          Hand bracket-walk (2026 MFJ, unindexed since base_year==2026):
            (0, 24_800] @ 10%: 24,800 * 0.10 = 2,480.00
            (24_800, 67_800] @ 12%: (67,800 - 24,800) * 0.12
                                     = 43,000 * 0.12 = 5,160.00
          federal_tax_amt = 2,480.00 + 5,160.00 = 7,640.00
          available_income = 0 - 7,640.00 = -7,640.00
          income_needed = 80,000 - (-7,640.00) = 87,640.00
          correct brokerage_end (year0) = 500,000 - 87,640.00 = 412,360.00

        Both runs' year1 (2027) dynamics are identical (no conversion that
        year, same flat living_expenses/available_income), so the entire
        difference between the two runs' year1-begin brokerage is exactly
        the year0 conversion's federal tax: 420,000 - 412,360 = 7,640.00.

        Pre-fix, income_needed is never subtracted in EITHER run, so both
        runs' year1-begin brokerage is the undebited 500,000.00 -- the
        $7,640.00 conversion tax vanishes with zero balance impact, making
        the conversion appear costless.
        """
        hh = _bare_household(brokerage_start=500_000.0, living_expenses=80_000.0)

        plan_no_conv = ConversionPlan()
        result_no_conv = run_scenario(hh, plan_no_conv, "c8-no-conv", end_age=62)

        plan_conv = ConversionPlan(your_conversions={2026: 100_000.0})
        result_conv = run_scenario(hh, plan_conv, "c8-conv", end_age=62)

        yr0_conv = result_conv.years[0]
        assert yr0_conv.your_conversion == approx(100_000.0)
        assert yr0_conv.federal_tax_amt == approx(7_640.00), (
            "Fixture guard: hand-derived conversion-year federal tax must "
            f"be 7640.00, got {yr0_conv.federal_tax_amt:.2f} -- check the "
            "fixture (deductions/brackets) before trusting the debit "
            "assertions below"
        )
        assert yr0_conv.income_needed == approx(87_640.00)

        terminal_no_conv = result_no_conv.years[1].brokerage_balance
        terminal_conv = result_conv.years[1].brokerage_balance

        assert terminal_no_conv == approx(420_000.0)
        assert terminal_conv == approx(412_360.0), (
            f"Expected conversion-run terminal brokerage=412360.00 (500000 "
            f"- 87640 income_needed), got {terminal_conv:.2f} -- the "
            f"conversion's $7,640 federal tax is not depleting the "
            f"brokerage balance (engine/scenario.py:824-830)"
        )

        # THE HEADLINE ASSERTION: the conversion must cost something.
        assert terminal_conv < terminal_no_conv, (
            f"Conversion run terminal brokerage ({terminal_conv:.2f}) must "
            f"be LESS than the no-conversion run ({terminal_no_conv:.2f}) "
            f"-- the conversion's federal tax must carry a real cash cost. "
            f"Pre-fix both are 500000.00 (identical), making the "
            f"conversion appear costless."
        )
        assert (terminal_no_conv - terminal_conv) == approx(7_640.00), (
            "The terminal-brokerage delta between the two runs must equal "
            f"exactly the conversion-year federal tax (7640.00), got "
            f"{terminal_no_conv - terminal_conv:.2f}"
        )
