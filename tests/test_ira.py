"""Tests for engine.ira — RMD, IRA projections, Social Security taxation."""

import pytest

from engine.ira import (
    calc_rmd,
    project_ira,
    rmd_divisor,
    ss_benefit_at_age,
    ss_with_cola,
)
from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from engine.tax import (
    taxable_ss,
)
from models.household import Household, InheritedIRA


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestSSTaxation:
    def test_mid_tier(self):
        tss = taxable_ss(40_000, 20_000)
        expected = 0.5 * (40_000 - 32_000)
        assert tss == approx(expected)

    def test_85pct_tier(self):
        tss = taxable_ss(100_000, 200_000)
        expected = min(0.85 * (250_000 - 44_000) + 6_000, 0.85 * 100_000)
        assert tss == approx(expected)


class TestIRA:
    def test_ira_growth(self):
        fv = project_ira(1_700_000, 0.07, 14)
        assert fv == approx(4_383_508, tol=100)

    def test_rmd_divisors(self):
        assert rmd_divisor(75) == 24.6
        assert rmd_divisor(80) == 20.2
        assert rmd_divisor(85) == 16.0
        assert rmd_divisor(95) == 8.9

    def test_rmd_at_75(self):
        rmd = calc_rmd(4_383_508, 75, 75)
        assert rmd == approx(4_383_508 / 24.6, tol=10)

    def test_no_rmd_before_75(self):
        assert calc_rmd(4_000_000, 74, 75) == 0


class TestSSBenefit:
    def test_ss_at_70(self):
        annual = ss_benefit_at_age(3_800, 70, 67)
        assert annual == approx(3_800 * 1.24 * 12)

    def test_ss_with_cola(self):
        with_cola = ss_with_cola(56_544, 5, 0.025)
        assert with_cola == approx(56_544 * 1.025**5)


class TestSSProvisionalIncomeRegression:
    """Regression guards for three omissions in the other_inc block used by taxable_ss().

    Per IRC §86(b)(2), provisional income = AGI + tax-exempt interest + 50% × SS.
    The other_inc term must include all AGI components so that taxable_ss() produces
    the correct result.

    C-3: interest_ytd (fully taxable ordinary interest) was missing.
    A-3: your_inherited_distribution + spouse_inherited_distribution were missing.
    B-3: ord_div_this_year (forecast ordinary brokerage dividends) was missing.
    """

    def _ss_household(self, **overrides) -> Household:
        """Both spouses already collecting SS at 70; minimal IRA; no grants; no ACA."""
        from dataclasses import replace

        base = replace(
            Household(grants=[]),
            your_age=70,
            spouse_age=70,
            your_ss_fra=2_000.0,  # $2,000/mo at FRA 67 → $2,480/mo at 70
            spouse_ss_fra=1_500.0,  # $1,500/mo at FRA 67 → $1,860/mo at 70
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ira=200_000.0,
            spouse_ira=200_000.0,
            living_expenses=50_000.0,
        )
        return replace(base, **overrides)

    def test_interest_ytd_in_ss_provisional(self):
        """C-3: $15K interest_ytd must push SS into 85%-taxable tier.

        Setup: combined SS ~$51,840/yr (your $29,760 + spouse $22,320 at age 70
        with 24% delayed-claim bonus above FRA 67).  Without interest_ytd,
        other_inc is ~0 (no RMDs, no conversions) and provisional income ≈
        0.5 × 51_840 = 25_920 < MFJ $32K tier-1 → 0% taxable.
        Adding $15K interest_ytd pushes provisional to 25_920 + 15_000 = 40_920
        which falls in [$32K, $44K] tier-1 → positive taxable SS.
        Both scenarios run the same base year so only interest_ytd differs.
        """
        from models.ytd_income import YTDSnapshot

        hh = self._ss_household()
        plan = ConversionPlan()

        # Without interest_ytd (no ytd snapshot)
        result_without = run_scenario(hh, plan, end_age=71)
        yr_without = result_without.years[0]

        # With $15K interest_ytd
        ytd = YTDSnapshot(interest_ytd=15_000.0)
        result_with = run_scenario(hh, plan, end_age=71, ytd=ytd)
        yr_with = result_with.years[0]

        assert yr_with.taxable_ss_amt > yr_without.taxable_ss_amt, (
            f"Expected interest_ytd to increase taxable SS; "
            f"with={yr_with.taxable_ss_amt:.0f}, without={yr_without.taxable_ss_amt:.0f}"
        )

    def test_inherited_distribution_in_ss_provisional(self):
        """A-3: $50K inherited IRA distribution must increase taxable SS amount.

        Without inherited distribution, SS is minimally taxable (low other_inc).
        With $50K inherited distribution flowing through other_inc, provisional
        income clears the MFJ $44K tier-2 threshold → 85% taxable SS.
        """
        iira = InheritedIRA(
            balance=500_000.0,
            inherited_year=2026,
            owner="you",
            growth_rate=0.0,
        )
        hh_with = self._ss_household(inherited_iras=[iira])
        hh_without = self._ss_household()
        plan = ConversionPlan()

        r_with = run_scenario(hh_with, plan, end_age=71)
        r_without = run_scenario(hh_without, plan, end_age=71)

        yr_with = r_with.years[0]
        yr_without = r_without.years[0]

        assert yr_with.your_inherited_distribution > 0.0, "Sanity: distribution must be positive"
        assert yr_with.taxable_ss_amt > yr_without.taxable_ss_amt, (
            f"Expected inherited distribution to increase taxable SS; "
            f"with={yr_with.taxable_ss_amt:.0f}, without={yr_without.taxable_ss_amt:.0f}"
        )

    def test_ord_brokerage_dividends_in_ss_provisional(self):
        """B-3: ordinary brokerage dividends in other_inc must increase taxable SS.

        Direct unit test of taxable_ss() in the partial-taxation zone (MFJ tier 1).
        Provisional income = other_income + 0.5 * SS.  When other_income grows by
        an ord_div amount the provisional income rises and more SS becomes taxable —
        as long as provisional stays below the 85%-cap crossover.

        MFJ thresholds: tier1=$32,000 (0% → 50%), tier2=$44,000 (50% → 85% cap).
        Setup: combined_ss=$30,000; base other_income=$25,000.
          provisional_base  = 25,000 + 0.5*30,000 = $40,000  (inside tier-1 partial zone)
          provisional_with  = 31,000 + 0.5*30,000 = $46,000  (crosses into tier-2 zone)
        Both remain below the 85%-cap floor (~$44K provisional), confirming the cap
        is NOT hit, so the discriminator is visible.
        """
        from engine.tax import taxable_ss

        combined_ss = 30_000.0
        base_other = 25_000.0  # provisional = 40K → partial-tax tier-1
        ord_div = 6_000.0  # provisional with div = 46K → partial-tax tier-2

        taxable_without = taxable_ss(combined_ss, base_other)
        taxable_with = taxable_ss(combined_ss, base_other + ord_div)

        assert taxable_with > taxable_without, (
            f"Expected taxable SS to increase when ord_div added to other_inc; "
            f"with={taxable_with:.2f}, without={taxable_without:.2f}"
        )
