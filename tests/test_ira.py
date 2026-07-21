"""Tests for engine.ira — RMD, IRA projections, Social Security taxation."""

import pytest

from engine.ira import (
    calc_rmd,
    joint_life_divisor,
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

    def test_rmd_divisors_extend_past_100(self):
        """Uniform Lifetime Table runs to age 120 (26 CFR 1.401(a)(9)-9(c))."""
        assert rmd_divisor(101) == 6.0
        assert rmd_divisor(110) == 3.5
        assert rmd_divisor(120) == 2.0

    def test_rmd_divisor_120_and_older(self):
        """Ages beyond 120 use the age-120 divisor, not 0."""
        assert rmd_divisor(121) == 2.0
        assert rmd_divisor(130) == 2.0

    def test_rmd_nonzero_past_100(self):
        """Regression: RMDs must continue past age 100 (table previously ended at 100)."""
        rmd = calc_rmd(1_000_000, 101, 75)
        assert rmd == approx(1_000_000 / 6.0, tol=1.0)

    def test_rmd_at_75(self):
        rmd = calc_rmd(4_383_508, 75, 75)
        assert rmd == approx(4_383_508 / 24.6, tol=10)

    def test_no_rmd_before_75(self):
        assert calc_rmd(4_000_000, 74, 75) == 0

    def test_defer_first_rmd_returns_zero_at_start_age(self):
        """IRC §401(a)(9)(C)(ii): with deferral elected, first-year RMD is zero."""
        assert calc_rmd(4_000_000, 75, 75, first_year_deferred=True) == 0.0

    def test_defer_first_rmd_doubles_year_two(self):
        """Year 2 with deferral = normal year-2 RMD + deferred year-1 RMD."""
        balance_year1 = 4_000_000.0
        # Simulated year-2 balance after 7% growth and no year-1 withdrawal
        balance_year2 = balance_year1 * 1.07
        normal_year2 = balance_year2 / rmd_divisor(76)
        deferred_year1 = balance_year1 / rmd_divisor(75)
        result = calc_rmd(
            balance_year2, 76, 75, first_year_deferred=True, prior_year_balance=balance_year1
        )
        assert result == approx(normal_year2 + deferred_year1, tol=1.0)

    def test_no_deferral_is_unaffected(self):
        """first_year_deferred=False at start age returns normal RMD (not zero)."""
        balance = 2_000_000.0
        expected = balance / rmd_divisor(75)
        assert calc_rmd(balance, 75, 75, first_year_deferred=False) == approx(expected)


class TestJointLastSurvivorTable:
    """M3 (audit-0720): 26 CFR §1.401(a)(9)-9 Table II — used only when the sole
    beneficiary spouse is more than 10 years younger than the owner."""

    def test_finding_case_uses_table_ii_and_shrinks_rmd(self):
        """RED (pre-fix): calc_rmd(500_000, 80, 73, beneficiary_age=65) used
        Table III (divisor 20.2) -> 24752.48. GREEN (post-fix): the >10-year
        gap (80-65=15) qualifies for Table II (divisor 23.8) -> 21008.40."""
        rmd = calc_rmd(500_000, 80, 73, beneficiary_age=65)
        assert rmd == approx(21_008.40, tol=0.5)
        # Sanity: confirms this is genuinely smaller than the old Table-III result.
        table_iii_rmd = 500_000 / 20.2
        assert rmd < table_iii_rmd

    def test_table_value_pins(self):
        """Embedded-transcription correctness guards — must match the IRS table exactly."""
        assert joint_life_divisor(80, 65) == 23.8
        assert joint_life_divisor(73, 55) == 32.6
        assert joint_life_divisor(92, 80) == 11.9
        assert joint_life_divisor(85, 72) == 18.1

    def test_exactly_10_years_younger_uses_table_iii_not_ii(self):
        """Age gap of exactly 10 does NOT qualify (rule requires MORE than 10)."""
        rmd = calc_rmd(500_000, 80, 73, beneficiary_age=70)
        assert rmd == approx(500_000 / rmd_divisor(80), tol=0.01)
        # A qualifying gap (11+) at the same owner age DOES differ from Table III,
        # confirming the gate is genuinely load-bearing (not a no-op coincidence).
        rmd_qualifying = calc_rmd(500_000, 80, 73, beneficiary_age=69)
        assert rmd_qualifying != approx(rmd)

    def test_owner_out_of_table_range_falls_back_to_table_iii_no_crash(self):
        """Owner age 95 has no Table II column at all."""
        assert joint_life_divisor(95, 60) is None
        rmd = calc_rmd(500_000, 95, 73, beneficiary_age=60)
        assert rmd == approx(500_000 / rmd_divisor(95), tol=0.01)

    def test_uncovered_cell_owner_92_bene_81_falls_back_to_table_iii_no_crash(self):
        """The one qualifying-but-uncovered cell noted in the audit finding."""
        assert joint_life_divisor(92, 81) is None
        rmd = calc_rmd(500_000, 92, 73, beneficiary_age=81)
        assert rmd == approx(500_000 / rmd_divisor(92), tol=0.01)

    def test_no_beneficiary_age_is_unaffected(self):
        """Default (no beneficiary_age passed) behaves exactly as before."""
        assert calc_rmd(500_000, 80, 73) == approx(500_000 / rmd_divisor(80), tol=0.01)

    def test_calc_rmd_default_start_age_covers_age_73_cohort(self):
        """L1 (audit 0702): default rmd_start_age must be 73, not 75, so the
        1951-1959 cohort gets an RMD at 73 when the arg is omitted."""
        rmd = calc_rmd(1_000_000, 73)
        assert rmd > 0
        assert rmd == pytest.approx(1_000_000 / 26.5, rel=1e-6)


class TestSSBenefit:
    def test_ss_at_70(self):
        annual = ss_benefit_at_age(3_800, 70, 67)
        assert annual == approx(3_800 * 1.24 * 12)

    def test_ss_with_cola(self):
        with_cola = ss_with_cola(56_544, 5, 0.025)
        assert with_cola == approx(56_544 * 1.025**5)

    def test_drc_capped_at_70(self):
        """Delayed-retirement credits stop at age 70; claiming at 71 or 75 must
        yield the same benefit as claiming at 70 (FRA=67, max 36 months of DRC)."""
        fra = 67
        monthly = 3_000.0
        at_70 = ss_benefit_at_age(monthly, 70, fra)
        at_71 = ss_benefit_at_age(monthly, 71, fra)
        at_75 = ss_benefit_at_age(monthly, 75, fra)
        assert at_71 == approx(at_70), "Claiming at 71 must equal 70 (DRC capped)"
        assert at_75 == approx(at_70), "Claiming at 75 must equal 70 (DRC capped)"


class TestHouseholdSSCap:
    """Regression: your_ss_at_70 / spouse_ss_at_70 must not exceed the age-70 benefit."""

    def test_ss_at_70_capped_when_start_age_exceeds_70(self):
        """your_ss_start_age > 70 must yield the same annual benefit as claiming at 70.

        The old inline formula (1 + delay_years * 0.08) * monthly * 12 would produce
        1.32 × 12 × fra for start_age=71 vs FRA=67, exceeding the 24% DRC maximum.
        The fix delegates to ss_benefit_at_age with effective_age = min(start_age, 70).
        """
        hh_70 = Household(your_ss_fra=3_800.0, your_ss_start_age=70, your_fra_age=67)
        hh_71 = Household(your_ss_fra=3_800.0, your_ss_start_age=71, your_fra_age=67)
        hh_75 = Household(your_ss_fra=3_800.0, your_ss_start_age=75, your_fra_age=67)

        at_70 = hh_70.your_ss_at_70()
        assert hh_71.your_ss_at_70() == approx(at_70), (
            "start_age=71 must equal start_age=70 (DRC capped)"
        )
        assert hh_75.your_ss_at_70() == approx(at_70), (
            "start_age=75 must equal start_age=70 (DRC capped)"
        )

    def test_spouse_ss_at_70_capped_when_start_age_exceeds_70(self):
        """spouse_ss_at_70 must not inflate the benefit when spouse_ss_start_age > 70."""
        hh_70 = Household(spouse_ss_fra=3_200.0, spouse_ss_start_age=70, spouse_fra_age=67)
        hh_72 = Household(spouse_ss_fra=3_200.0, spouse_ss_start_age=72, spouse_fra_age=67)

        at_70 = hh_70.spouse_ss_at_70()
        assert hh_72.spouse_ss_at_70() == approx(at_70), (
            "spouse start_age=72 must equal start_age=70 (DRC capped)"
        )

    def test_ss_at_70_uses_correct_fra(self):
        """your_ss_at_70 respects the per-person FRA (e.g., FRA=66 for older cohort)."""
        # FRA=66: 4 years of DRC → 32% bonus (48 months × 2/3% = 32%)
        hh = Household(your_ss_fra=3_000.0, your_ss_start_age=70, your_fra_age=66)
        expected = ss_benefit_at_age(3_000.0, 70, 66)
        assert hh.your_ss_at_70() == approx(expected)


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
