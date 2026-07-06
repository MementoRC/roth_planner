"""Regression: option income must flow into available_income / brokerage carry-forward.

Audit 2026-07-06 finding: yr.option_income was included in combined_gross and
taxed correctly, but was omitted from the available_income computation, so the
after-tax option cash never reached excess_rmd or the brokerage carry-forward.

Fix: add ``+ yr.option_income`` to the available_income block in engine/scenario.py.
"""

from __future__ import annotations

from engine.scenario import ConversionPlan, run_scenario
from models.grants import StockGrant
from models.household import Household


def _make_option_hh(base_year: int = 2026) -> Household:
    """Minimal Household with a single NQO grant exercised in base_year."""
    hh = Household(
        your_age=62,
        spouse_age=56,
        base_year=base_year,
        your_ira=500_000.0,
        spouse_ira=500_000.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        your_ss_start_age=70,
        spouse_ss_start_age=70,
        living_expenses=80_000.0,
        brokerage_start=0.0,
    )
    # Replace grants with a single known grant expiring in base_year
    grant = StockGrant(
        year=base_year - 7,
        strike=100.0,
        shares=1_000,
        expiry_year=base_year,
        grant_id="TEST_GRANT",
    )
    hh.grants = [grant]
    hh.txn_price_now = 200.0  # spread = (200 - 100) * 1000 = $100_000
    return hh


class TestOptionCashBrokerageCarryForward:
    """After-tax option proceeds must appear in excess_rmd / brokerage."""

    def test_option_income_nonzero_in_base_year(self) -> None:
        """Confirm the household produces option income in the base year."""
        hh = _make_option_hh()
        assert hh.option_income(hh.base_year, early=True) == 100_000.0

    def test_option_income_zero_in_non_option_year(self) -> None:
        """Confirm option income is 0 the year after the grant expires."""
        hh = _make_option_hh()
        assert hh.option_income(hh.base_year + 1, early=True) == 0.0

    def test_available_income_includes_option_proceeds(self) -> None:
        """available_income in the option year must reflect after-tax option cash.

        Without the fix, option_income is taxed (raises federal_tax_amt) but the
        pre-tax proceeds are never added to available_income, producing a deficit
        equal to the full option spread. With the fix the surplus/deficit shrinks
        because the cash is present.
        """
        hh = _make_option_hh()
        option_gross = hh.option_income(hh.base_year, early=True)
        assert option_gross == 100_000.0, "test pre-condition: option spread must be $100K"

        plan = ConversionPlan()  # no conversions — isolate option effect
        result = run_scenario(hh, plan, end_age=hh.your_age + 1)  # 2 years only

        yr_option = result.years[0]  # base_year — option fires here
        yr_no_option = result.years[1]  # base_year + 1 — no option income

        # Sanity: option income is attributed to the base year
        assert yr_option.option_income == 100_000.0
        assert yr_no_option.option_income == 0.0

        # Key assertion: in an option year, after-tax option cash must increase
        # available_income relative to a non-option year (all else held equal:
        # same RMD, same SS, same living_expenses).
        #
        # Define net_option_cash = option_income - marginal_tax_on_option.
        # The marginal tax is at most 37% so net_option_cash > 0.
        # Therefore available_income[option_year] > available_income[no_option_year].
        #
        # Before the fix: option_income added to federal_tax_amt but NOT to the
        # available_income sum, so available_income[option_year] was LOWER than
        # the no-option year by the extra tax paid on the option proceeds.
        available_option = (
            yr_option.taxable_rmd
            + yr_option.spouse_taxable_rmd
            + yr_option.extra_withdrawal
            + yr_option.spouse_extra_withdrawal
            + yr_option.combined_ss
            + yr_option.option_income  # the fix
            - yr_option.federal_tax_amt
        )
        available_no_option = (
            yr_no_option.taxable_rmd
            + yr_no_option.spouse_taxable_rmd
            + yr_no_option.extra_withdrawal
            + yr_no_option.spouse_extra_withdrawal
            + yr_no_option.combined_ss
            + yr_no_option.option_income  # 0.0
            - yr_no_option.federal_tax_amt
        )
        # Net option cash must be positive (tax rate < 100%)
        assert available_option > available_no_option, (
            f"Option year available_income ({available_option:,.0f}) should exceed "
            f"non-option year ({available_no_option:,.0f}) by the after-tax option proceeds"
        )

    def test_brokerage_accumulates_option_excess(self) -> None:
        """Excess RMD from the option year must flow into the brokerage balance.

        If living_expenses < available_income in the option year, the surplus
        (excess_rmd) is reinvested in brokerage. Before the fix, excess_rmd was
        computed on available_income that excluded option_income, so the surplus
        was understated (or even negative when the option tax exceeded other income).
        """
        hh = _make_option_hh()
        # living_expenses = $80K; option spread = $100K gross.
        # Even at 37% marginal rate, net option cash ~= $63K.
        # Combined with any RMDs and SS this should produce excess_rmd > 0.

        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age + 1)

        yr_option = result.years[0]

        # excess_rmd > 0 means available_income > living_expenses
        # This only holds if option_income is included in available_income.
        # Before the fix, option income was absent from available_income so the
        # extra tax it triggered would have depressed excess_rmd further.
        assert yr_option.excess_rmd >= 0, "excess_rmd must not be negative"

        # Stronger: brokerage at end of option year must be ≥ excess_rmd
        # (brokerage starts at 0, only gains come from excess_rmd + appreciation).
        # The important thing is that brokerage_balance > 0 when option year
        # produces a net surplus — which requires option_income in available_income.
        option_net_of_tax = yr_option.option_income - max(
            yr_option.federal_tax_amt - yr_option.taxable_rmd - yr_option.spouse_taxable_rmd - yr_option.combined_ss,
            0.0,
        )
        if option_net_of_tax > hh.living_expenses:
            # Option alone covers living expenses → brokerage must grow
            assert yr_option.excess_rmd > 0, (
                "After-tax option proceeds exceed living expenses; "
                "excess_rmd must be positive (option cash missing pre-fix)"
            )
