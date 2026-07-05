"""Tests for models.household — properties, growth profile, inherited IRA, survivor scenario."""

import pytest

from config.defaults import DEFAULTS
from engine.ira import (
    inherited_ira_drain,
)
from engine.scenario import (
    ConversionPlan,
    run_no_conversion,
    run_scenario,
)
from models.household import GrowthProfile, Household, InheritedIRA, SurvivorScenario


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestHouseholdProperties:
    def test_age_gap(self):
        hh = Household()
        assert hh.age_gap == DEFAULTS["your_age"] - DEFAULTS["spouse_age"]

    def test_conv_window(self):
        hh = Household()
        expected = max(75 - 1 - DEFAULTS["your_age"] + 1, 0)
        assert hh.your_conv_window == expected

    def test_spouse_conv_window_property(self):
        """spouse_conv_window mirrors your_conv_window formula for spouse_age."""
        from dataclasses import replace

        hh = replace(Household(), your_age=61, spouse_age=55)
        assert hh.your_conv_window == max(75 - 1 - 61 + 1, 0)  # 14
        assert hh.spouse_conv_window == max(75 - 1 - 55 + 1, 0)  # 20
        assert hh.spouse_conv_window > hh.your_conv_window

    def test_conv_windows_symmetric_under_swap(self):
        """Under me<->spouse swap, the windows swap correctly."""
        from dataclasses import replace

        hh = replace(Household(), your_age=61, spouse_age=55)
        hh_sw = replace(hh, your_age=hh.spouse_age, spouse_age=hh.your_age)
        assert hh_sw.your_conv_window == hh.spouse_conv_window
        assert hh_sw.spouse_conv_window == hh.your_conv_window

    def test_ss_at_70(self):
        hh = Household()
        expected_annual = DEFAULTS["your_ss_fra"] * 1.24 * 12
        assert hh.your_ss_at_70() == approx(expected_annual)
        expected_spouse = DEFAULTS["spouse_ss_fra"] * 1.24 * 12
        assert hh.spouse_ss_at_70() == approx(expected_spouse)


class TestPerAccountGrowth:
    """Test per-account growth rate profiles."""

    def test_growth_profile_default(self):
        gp = GrowthProfile(default_rate=0.08)
        assert gp.rate_for(2026) == 0.08
        assert gp.rate_for(2030) == 0.08

    def test_growth_profile_yearly_override(self):
        gp = GrowthProfile(default_rate=0.07, yearly_overrides={2026: 0.10, 2027: -0.05})
        assert gp.rate_for(2026) == 0.10
        assert gp.rate_for(2027) == -0.05
        assert gp.rate_for(2028) == 0.07  # falls back to default

    def test_household_falls_back_to_growth_rate(self):
        hh = Household(growth_rate=0.06)
        assert hh.your_ira_rate(2026) == 0.06
        assert hh.spouse_ira_rate(2026) == 0.06
        assert hh.brokerage_rate(2026) == 0.06

    def test_household_per_account_overrides(self):
        hh = Household(
            growth_rate=0.07,
            your_ira_growth=GrowthProfile(default_rate=0.09),
            spouse_ira_growth=GrowthProfile(default_rate=0.05),
            brokerage_growth=GrowthProfile(default_rate=0.06),
        )
        assert hh.your_ira_rate(2026) == 0.09
        assert hh.spouse_ira_rate(2026) == 0.05
        assert hh.brokerage_rate(2026) == 0.06

    def test_different_growth_rates_affect_scenario(self):
        """Higher your_ira growth should produce larger IRA at end."""
        hh_high = Household(your_ira_growth=GrowthProfile(default_rate=0.10))
        hh_low = Household(your_ira_growth=GrowthProfile(default_rate=0.04))
        r_high = run_no_conversion(hh_high, end_age=80)
        r_low = run_no_conversion(hh_low, end_age=80)
        # Your IRA should be larger with higher growth
        yr_high = next(y for y in r_high.years if y.your_age == 80)
        yr_low = next(y for y in r_low.years if y.your_age == 80)
        assert yr_high.your_ira_end > yr_low.your_ira_end

    def test_spouse_independent_growth(self):
        """Spouse IRA grows independently from yours."""
        hh = Household(
            your_ira_growth=GrowthProfile(default_rate=0.10),
            spouse_ira_growth=GrowthProfile(default_rate=0.03),
        )
        r = run_no_conversion(hh, end_age=80)
        yr = next(y for y in r.years if y.your_age == 80)
        # Your IRA grows at 10%, spouse at 3% — yours should be much larger
        # (starting balances are equal per DEFAULTS["your_ira"] / DEFAULTS["spouse_ira"])
        assert yr.your_ira_end > yr.spouse_ira_end * 1.5

    def test_yearly_override_applies(self):
        """A bad year override should reduce the IRA compared to flat growth."""
        hh_flat = Household(growth_rate=0.07)
        hh_crash = Household(
            your_ira_growth=GrowthProfile(
                default_rate=0.07,
                yearly_overrides={2027: -0.20},  # 20% crash in year 2
            ),
        )
        r_flat = run_no_conversion(hh_flat, end_age=70)
        r_crash = run_no_conversion(hh_crash, end_age=70)
        yr_flat = next(y for y in r_flat.years if y.your_age == 70)
        yr_crash = next(y for y in r_crash.years if y.your_age == 70)
        assert yr_crash.your_ira_end < yr_flat.your_ira_end


class TestGrowthProfileYield:
    """Tests for the yield/qualified split on GrowthProfile."""

    def test_default_yield_is_zero(self):
        """Backward compat — default GrowthProfile has zero yield."""
        gp = GrowthProfile(default_rate=0.07)
        assert gp.yield_for(2026) == 0.0
        assert gp.appreciation_for(2026) == 0.07
        assert gp.qualified_div_for(2026, 100_000) == 0.0
        assert gp.ordinary_div_for(2026, 100_000) == 0.0

    def test_yield_split_fully_qualified(self):
        gp = GrowthProfile(default_rate=0.07, yield_rate=0.02, qualified_fraction=1.0)
        assert gp.appreciation_for(2026) == pytest.approx(0.05)
        assert gp.qualified_div_for(2026, 100_000) == pytest.approx(2000.0)
        assert gp.ordinary_div_for(2026, 100_000) == pytest.approx(0.0)

    def test_yield_split_mixed(self):
        gp = GrowthProfile(default_rate=0.06, yield_rate=0.03, qualified_fraction=0.7)
        assert gp.qualified_div_for(2026, 100_000) == pytest.approx(2100.0)
        assert gp.ordinary_div_for(2026, 100_000) == pytest.approx(900.0)

    def test_yield_overrides(self):
        gp = GrowthProfile(
            default_rate=0.07,
            yield_rate=0.02,
            yield_overrides={2027: 0.05},
        )
        assert gp.yield_for(2026) == 0.02
        assert gp.yield_for(2027) == 0.05


class TestInheritedIRA:
    """SECURE Act 10-year rule — engine integration tests."""

    BASE_YEAR = 2026

    def _base_hh(self, **kwargs) -> Household:
        return Household(
            your_age=61,
            spouse_age=55,
            your_ira=500_000,
            spouse_ira=500_000,
            growth_rate=0.07,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Pure helper tests
    # ------------------------------------------------------------------

    def test_drain_helper_year1(self):
        assert inherited_ira_drain(100_000, 10) == pytest.approx(10_000.0)

    def test_drain_helper_final_year(self):
        assert inherited_ira_drain(50_000, 1) == pytest.approx(50_000.0)

    def test_drain_helper_zero_when_exhausted(self):
        assert inherited_ira_drain(50_000, 0) == 0.0
        assert inherited_ira_drain(50_000, -1) == 0.0

    # ------------------------------------------------------------------
    # a) Regression guard: empty inherited_iras changes nothing
    # ------------------------------------------------------------------

    def test_no_inherited_iras_default_unchanged(self):
        hh_base = self._base_hh()
        hh_with_empty = self._base_hh(inherited_iras=[])
        plan = ConversionPlan()
        r_base = run_scenario(hh_base, plan)
        r_empty = run_scenario(hh_with_empty, plan)
        for yr_b, yr_e in zip(r_base.years, r_empty.years, strict=True):
            assert yr_b.magi == pytest.approx(yr_e.magi)
            assert yr_b.your_ira_end == pytest.approx(yr_e.your_ira_end)
            assert yr_e.your_inherited_distribution == 0.0
            assert yr_e.spouse_inherited_distribution == 0.0

    # ------------------------------------------------------------------
    # b) Full 10-year drain: balance reaches zero, sum of distributions >= initial
    # ------------------------------------------------------------------

    def test_inherited_ira_drains_over_10_years(self):
        initial_balance = 100_000.0
        iira = InheritedIRA(
            balance=initial_balance,
            inherited_year=self.BASE_YEAR + 1,
            owner="you",
            growth_rate=0.07,
        )
        hh = self._base_hh(base_year=self.BASE_YEAR, inherited_iras=[iira])
        result = run_scenario(hh, ConversionPlan(), end_age=hh.your_age + 15)

        # Filter to the 10 drain years
        drain_years = [
            yr for yr in result.years if self.BASE_YEAR + 1 <= yr.year <= self.BASE_YEAR + 10
        ]
        assert len(drain_years) == 10

        total_distributed = sum(yr.your_inherited_distribution for yr in drain_years)
        # Total distributions must exceed initial balance (growth during drain window)
        assert total_distributed >= initial_balance

        # Year 1 distribution: 100_000 / 10 = 10_000 exactly (no growth yet: inherited_year=base+1)
        assert drain_years[0].your_inherited_distribution == pytest.approx(10_000.0)

        # After year 10, balance should be fully drained
        post_drain_years = [yr for yr in result.years if yr.year > self.BASE_YEAR + 10]
        for yr in post_drain_years:
            assert yr.your_inherited_distribution == pytest.approx(0.0)
            assert yr.your_inherited_balance_end == pytest.approx(0.0)

    # ------------------------------------------------------------------
    # c) Zero distribution before inherited_year
    # ------------------------------------------------------------------

    def test_inherited_ira_drain_pre_inheritance_year_zero(self):
        iira = InheritedIRA(
            balance=200_000.0,
            inherited_year=self.BASE_YEAR + 5,
            owner="you",
            growth_rate=0.07,
        )
        hh = self._base_hh(base_year=self.BASE_YEAR, inherited_iras=[iira])
        result = run_scenario(hh, ConversionPlan(), end_age=hh.your_age + 20)

        pre_years = [yr for yr in result.years if yr.year < self.BASE_YEAR + 5]
        for yr in pre_years:
            assert yr.your_inherited_distribution == 0.0

        first_drain = next(yr for yr in result.years if yr.year == self.BASE_YEAR + 5)
        assert first_drain.your_inherited_distribution > 0.0

    # ------------------------------------------------------------------
    # d) Distribution appears in MAGI
    # ------------------------------------------------------------------

    def test_inherited_ira_distribution_appears_in_magi(self):
        iira = InheritedIRA(
            balance=100_000.0,
            inherited_year=self.BASE_YEAR,
            owner="you",
            growth_rate=0.0,  # no growth → deterministic drain amounts
        )
        hh_with = self._base_hh(base_year=self.BASE_YEAR, inherited_iras=[iira])
        hh_without = self._base_hh(base_year=self.BASE_YEAR)
        plan = ConversionPlan()
        r_with = run_scenario(hh_with, plan)
        r_without = run_scenario(hh_without, plan)

        for yr_w, yr_wo in zip(r_with.years, r_without.years, strict=True):
            if yr_w.your_inherited_distribution > 0:
                magi_delta = yr_w.magi - yr_wo.magi
                assert magi_delta == pytest.approx(yr_w.your_inherited_distribution, rel=1e-6)

    # ------------------------------------------------------------------
    # e) Owner routing: "you" vs "spouse"
    # ------------------------------------------------------------------

    def test_inherited_ira_owner_routing(self):
        iira_you = InheritedIRA(
            balance=80_000.0,
            inherited_year=self.BASE_YEAR,
            owner="you",
            growth_rate=0.0,
        )
        iira_spouse = InheritedIRA(
            balance=60_000.0,
            inherited_year=self.BASE_YEAR,
            owner="spouse",
            growth_rate=0.0,
        )
        hh = self._base_hh(base_year=self.BASE_YEAR, inherited_iras=[iira_you, iira_spouse])
        result = run_scenario(hh, ConversionPlan())

        first_year = result.years[0]
        # year 0 of 10: 80_000 / 10 = 8_000; 60_000 / 10 = 6_000
        assert first_year.your_inherited_distribution == pytest.approx(8_000.0)
        assert first_year.spouse_inherited_distribution == pytest.approx(6_000.0)

    # ------------------------------------------------------------------
    # f) Multiple inherited IRAs same owner drain independently
    # ------------------------------------------------------------------

    def test_multiple_inherited_iras_same_owner(self):
        iira_a = InheritedIRA(
            balance=100_000.0,
            inherited_year=self.BASE_YEAR,
            owner="you",
            growth_rate=0.0,
        )
        iira_b = InheritedIRA(
            balance=50_000.0,
            inherited_year=self.BASE_YEAR + 2,
            owner="you",
            growth_rate=0.0,
        )
        hh = self._base_hh(base_year=self.BASE_YEAR, inherited_iras=[iira_a, iira_b])
        result = run_scenario(hh, ConversionPlan(), end_age=hh.your_age + 20)

        # Year 0: only iira_a draining (iira_b not yet inherited)
        yr0 = result.years[0]
        assert yr0.your_inherited_distribution == pytest.approx(100_000.0 / 10)

        # Year 2: both draining independently
        yr2 = next(yr for yr in result.years if yr.year == self.BASE_YEAR + 2)
        # iira_a: balance after 2 drain cycles (no growth), years_remaining=8
        # After yr0: balance = (100_000 - 10_000) * 1.0 = 90_000
        # After yr1: balance = (90_000 - 90_000/9) * 1.0 = 80_000
        # yr2 drain from iira_a: 80_000 / 8 = 10_000
        # iira_b: first drain = 50_000 / 10 = 5_000
        assert yr2.your_inherited_distribution == pytest.approx(10_000.0 + 5_000.0)


class TestSurvivorScenario:
    """PR6b: SurvivorScenario dataclass + run_scenario survivor wiring."""

    # --- shared fixture ---

    def _base_hh(self, **kwargs) -> Household:
        """Minimal household with both SS claimed and RMD start at 75.

        Ages: you=61, spouse=55 → death_year=2030 means you are 65 at death,
        spouse is 59 at death. Single filing starts 2031 (you=66, spouse=60).
        """
        return Household(
            your_age=61,
            spouse_age=55,
            your_ira=1_000_000,
            spouse_ira=800_000,
            your_ss_fra=3_000,  # $3K/mo at FRA 67
            spouse_ss_fra=2_500,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.07,
            living_expenses=80_000,
            **kwargs,
        )

    # --- (a) default None — must be a no-op ---

    def test_survivor_default_none_no_behavior_change(self):
        """Household.survivor=None (default) must produce identical results to omitting the field.

        Regression guard: the survivor wiring must be a pure no-op on the default path.
        """
        hh_no_field = self._base_hh()
        hh_explicit_none = self._base_hh(survivor=None)
        plan = ConversionPlan()
        r1 = run_scenario(hh_no_field, plan, end_age=80)
        r2 = run_scenario(hh_explicit_none, plan, end_age=80)
        assert r1.total_your_conv == r2.total_your_conv
        assert abs(r1.years[-1].your_ira_end - r2.years[-1].your_ira_end) < 1.0
        assert abs(r1.total_conv_tax - r2.total_conv_tax) < 1.0

    # --- (b) filing status switches at death_year + 1 ---

    def test_survivor_who_dies_you_switches_filing_status_year_after_death(self):
        """death_year=2030: year 2030 is still MFJ, year 2031 switches to Single.

        Verify that federal_tax in 2031 matches federal_tax_single(taxable_income).
        """
        from engine.tax import federal_tax_single

        surv = SurvivorScenario(who_dies="you", death_year=2030)
        hh = self._base_hh(survivor=surv)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=72)

        yr_death = next(y for y in result.years if y.year == 2030)
        yr_after = next(y for y in result.years if y.year == 2031)

        # Year of death is still MFJ — federal_tax on that year's taxable income
        # must differ from federal_tax_single (unless taxable_income is 0)
        # Year after death: federal_tax_amt must equal federal_tax_single(taxable_income)
        assert yr_after.federal_tax_amt == approx(
            federal_tax_single(yr_after.taxable_income), tol=0.01
        )
        # Sanity: the year-of-death row itself is still MFJ filing
        from engine.tax import federal_tax as federal_tax_mfj

        assert yr_death.federal_tax_amt == approx(
            federal_tax_mfj(yr_death.taxable_income), tol=0.01
        )

    # --- (c) symmetric case: who_dies="spouse" ---

    def test_survivor_who_dies_spouse_symmetric(self):
        """who_dies='spouse': same filing-status switch but you are the survivor."""
        from engine.tax import federal_tax_single

        surv = SurvivorScenario(who_dies="spouse", death_year=2030)
        hh = self._base_hh(survivor=surv)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=72)

        yr_after = next(y for y in result.years if y.year == 2031)
        assert yr_after.federal_tax_amt == approx(
            federal_tax_single(yr_after.taxable_income), tol=0.01
        )

    # --- (d) IRA rollover at death_year + 1 ---

    def test_survivor_rolls_deceased_ira_to_survivor(self):
        """who_dies='spouse': at death_year+1 spouse_ira rolls into your_ira.

        spouse_ira_begin in 2031 must be 0; your_ira_begin must contain
        the combined balance that grew through 2030.
        """
        surv = SurvivorScenario(who_dies="spouse", death_year=2030)
        hh = self._base_hh(survivor=surv)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=72)

        yr_2030 = next(y for y in result.years if y.year == 2030)
        yr_2031 = next(y for y in result.years if y.year == 2031)

        # After rollover, deceased's IRA is 0 at beginning of 2031
        assert yr_2031.spouse_ira_begin == approx(0.0, tol=1.0)
        # Survivor's IRA beginning-of-2031 reflects both balances from end-of-2030
        combined_end_2030 = yr_2030.your_ira_end + yr_2030.spouse_ira_end
        assert yr_2031.your_ira_begin == approx(combined_end_2030, tol=1.0)

        # Rollover is exactly one-time: 2032 spouse_ira_begin stays 0
        yr_2032 = next(y for y in result.years if y.year == 2032)
        assert yr_2032.spouse_ira_begin == approx(0.0, tol=1.0)

    # --- (e) deceased SS zeroed ---

    def test_survivor_zeros_deceased_ss(self):
        """who_dies='spouse': spouse_ss=0 from death_year+1; survivor (you) keeps larger benefit.

        Step-up: yr.your_ss = max(your_ss, spouse_ss) in survivor years.
        """
        surv = SurvivorScenario(who_dies="spouse", death_year=2030)
        # Use ages where both are past SS start age by 2031 so we can observe SS
        # age 70 → birth year 1956 (1951-1959) → SECURE 2.0 statutory RMD start age 73
        hh = Household(
            your_age=70,
            spouse_age=70,
            your_ira=1_000_000,
            spouse_ira=800_000,
            your_ss_fra=3_000,
            spouse_ss_fra=2_500,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            growth_rate=0.07,
            living_expenses=80_000,
            survivor=surv,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=80)

        yr_2030 = next(y for y in result.years if y.year == 2030)
        yr_2031 = next(y for y in result.years if y.year == 2031)

        # Year of death: both SS still active (MFJ year)
        assert yr_2030.spouse_ss > 0
        assert yr_2030.your_ss > 0

        # Year after death: deceased (spouse) SS is 0; survivor (you) keeps max benefit
        assert yr_2031.spouse_ss == approx(0.0, tol=0.01)
        assert yr_2031.your_ss > 0
        # Step-up: your_ss > spouse_ss (you are higher earner), so your_ss is unchanged
        # and combined_ss == your_ss (not your_ss + spouse_ss as in MFJ year)
        assert yr_2031.your_ss > yr_2030.spouse_ss  # kept the larger, not the smaller
        assert yr_2031.combined_ss == approx(yr_2031.your_ss, tol=0.01)

    # --- (e2) SS survivor step-up: higher earner dies, survivor keeps larger benefit ---

    def test_survivor_ss_stepup_higher_earner_dies(self):
        """H2 regression: when the HIGHER earner dies, survivor keeps max(your_ss, spouse_ss).

        Setup: you have higher FRA ($4K vs $2K). who_dies='you' → spouse survives.
        In survivor years:
        - yr.spouse_ss == max(pre-death your_ss, pre-death spouse_ss)  [step-up]
        - yr.your_ss == 0
        - yr.combined_ss > spouse-own-only (old broken behaviour)
        """
        surv = SurvivorScenario(who_dies="you", death_year=2030)
        # age 70 → birth year 1956 (1951-1959) → SECURE 2.0 statutory RMD start age 73
        hh = Household(
            your_age=70,
            spouse_age=70,
            your_ira=1_000_000,
            spouse_ira=800_000,
            your_ss_fra=4_000,  # you are the higher earner
            spouse_ss_fra=2_000,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            growth_rate=0.07,
            living_expenses=80_000,
            survivor=surv,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=82)

        yr_2030 = next(y for y in result.years if y.year == 2030)
        yr_2031 = next(y for y in result.years if y.year == 2031)

        # Pre-death year: both benefits positive, higher earner has larger SS
        assert yr_2030.your_ss > yr_2030.spouse_ss > 0

        # Survivor year: deceased (you) SS zeroed
        assert yr_2031.your_ss == approx(0.0, tol=0.01)

        # Step-up: both SS amounts are COLA-grown to 2031 inside compute_social_security,
        # then max() is taken. Since your FRA ($4K) > spouse FRA ($2K), your 2031 COLA-grown
        # benefit is larger, so spouse_ss_2031 inherits the higher-earner amount.
        # It must be strictly greater than the 2030 higher-earner value (COLA grew it)
        # and far greater than spouse's own-only path (2x FRA ratio).
        assert yr_2031.spouse_ss > yr_2030.your_ss  # COLA grew from 2030 higher-earner base
        assert yr_2031.spouse_ss > yr_2030.spouse_ss * 1.5  # far exceeds own-only

        # combined_ss equals spouse_ss (only survivor benefit remains)
        assert yr_2031.combined_ss == approx(yr_2031.spouse_ss, tol=0.01)

    # --- (f) single std deduction applies post-survivor ---

    def test_survivor_uses_single_std_deduction(self):
        """Post-death year uses STD_DEDUCTION_SINGLE for total_deductions baseline.

        With both ages < 65 in 2031, total_deductions should equal STD_DEDUCTION_SINGLE
        (no senior extras, no OBBBA bonus at this MAGI level — we keep income low).
        """
        from engine.tax import STD_DEDUCTION_SINGLE

        surv = SurvivorScenario(who_dies="spouse", death_year=2030)
        # Use ages so neither is 65 in 2031: you=55 → 60 in 2031
        hh = Household(
            your_age=55,
            spouse_age=50,
            your_ira=500_000,
            spouse_ira=400_000,
            your_ss_fra=3_000,
            spouse_ss_fra=2_500,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.07,
            living_expenses=50_000,
            survivor=surv,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=65)

        yr_2031 = next(y for y in result.years if y.year == 2031)

        # Neither survivor age (60) nor deceased spouse age (55) triggers senior extra
        # and low MAGI means no OBBBA phaseout — deductions = STD_DEDUCTION_SINGLE exactly
        assert yr_2031.total_deductions == approx(STD_DEDUCTION_SINGLE, tol=0.01)

    def test_aca_premium_cap_rate_single_higher_fpl_ratio(self):
        """Single filer at same MAGI has higher FPL ratio → hits higher cap schedule band."""
        from engine.aca import aca_premium_cap_rate

        magi = 60_000
        mfj_rate = aca_premium_cap_rate(magi, filing_status="MFJ")
        single_rate = aca_premium_cap_rate(magi, filing_status="Single")
        # Single filer is further up the FPL scale → equal or higher cap rate
        assert single_rate >= mfj_rate

    # --- audit regression: B1 — irmaa_room uses Single tier in survivor years ---

    def test_survivor_irmaa_room_uses_single_threshold(self):
        """Post-death year: irmaa_room must reflect Single thresholds, not MFJ.

        The MFJ T1 IRMAA threshold is ~$218K; Single T1 is ~$109K.
        A MAGI of $120K exceeds the Single threshold but not MFJ → irmaa_room
        should be 0 (or very small) for Single, not the large MFJ gap.
        """
        from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE

        surv = SurvivorScenario(who_dies="spouse", death_year=2030)
        # age 70 → birth year 1956 (1951-1959) → SECURE 2.0 statutory RMD start age 73
        hh = Household(
            your_age=70,
            spouse_age=70,
            your_ira=1_000_000,
            spouse_ira=800_000,
            your_ss_fra=2_400,  # $2,400/mo → ~$28,800/yr SS
            spouse_ss_fra=2_000,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            growth_rate=0.0,  # no growth so we can reason about MAGI precisely
            living_expenses=60_000,
            survivor=surv,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=82)

        yr_2031 = next(y for y in result.years if y.year == 2031)
        # irmaa_room for Single must be less than MFJ T1 threshold
        mfj_t1 = IRMAA_TIERS_MFJ[0][0]
        single_t1 = IRMAA_TIERS_SINGLE[0][0]
        # If yr.magi > single_t1, irmaa_room should reflect how far we are past that
        if yr_2031.magi > single_t1:
            assert yr_2031.irmaa_room == approx(0.0, abs=1.0)
        else:
            # irmaa_room must be bounded by Single T1, not MFJ T1
            assert yr_2031.irmaa_room < mfj_t1

    # --- audit regression: H1 — survivor IRMAA counts only 1 person ---

    def test_survivor_irmaa_counts_one_person(self):
        """H1 regression: in survivor years IRMAA must charge for 1 person, not 2.

        Both spouses are well past 65 at IRMAA payment time.  The lookback MAGI
        is anchored at $230K — above both Single T1 ($109K) and MFJ T1 ($218K).
        In survivor years the deceased spouse must NOT be counted as a Medicare
        beneficiary, so the engine cost must equal the explicit 1-person cost and
        be strictly less than the explicit 2-person (MFJ) cost at the same MAGI.
        """
        from engine.irmaa import irmaa_for_year

        # Both ages 73 in 2026 → income-year ages (ya-2=71) are ≥65 in 2027.
        # Spouse dies in 2026; first survivor year is 2027.
        # Anchor lookback MAGI at $230K so IRMAA computation is deterministic.
        surv = SurvivorScenario(who_dies="spouse", death_year=2026)
        # age 73 → birth year 1953 (1951-1959) → SECURE 2.0 statutory RMD start age 73
        hh = Household(
            your_age=73,
            spouse_age=73,
            base_year=2026,
            your_ira=2_000_000,
            spouse_ira=0,
            your_ss_fra=2_400,
            spouse_ss_fra=0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            growth_rate=0.0,
            living_expenses=60_000,
            prior_year_magi={2025: 230_000, 2024: 230_000},
            survivor=surv,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=82)

        # 2027 is the first survivor year (death_year + 1).
        yr_survivor = next(y for y in result.years if y.year == 2027)

        # Reference costs — both using Single filing_status (the survivor's status).
        # ya=75 in 2027 → income-year age passed to irmaa_for_year is ya-2 = 73.
        # Fixed code: deceased spouse zeroed → age 0 → not counted as Medicare enrollee.
        one_person_cost, _ = irmaa_for_year(
            230_000,
            73,  # your income-year age (ya - 2 = 75 - 2)
            0,  # deceased spouse zeroed → not counted
            filing_status="Single",
            year=2027,
        )
        # A wrongly double-counted survivor would pay two per-person surcharges. Since audit
        # 0705 #3, irmaa_for_year itself gates the beneficiary count on filing_status, so a
        # Single-filer call can no longer reproduce the 2× cost — the reference is arithmetic.
        two_person_cost = 2 * one_person_cost
        assert one_person_cost > 0, "sanity: $230K MAGI must trigger Single IRMAA"
        assert two_person_cost > one_person_cost, "sanity: a double-counted survivor would pay 2×"
        assert yr_survivor.irmaa_cost == approx(one_person_cost, tol=1.0), (
            f"Survivor IRMAA {yr_survivor.irmaa_cost:.0f} should equal 1-person cost "
            f"{one_person_cost:.0f}, not 2-person cost {two_person_cost:.0f}"
        )

    # --- audit regression: C — aca_magi includes non-taxable SS ---

    def test_aca_magi_includes_nontaxable_ss(self):
        """aca_magi must equal yr.magi + (combined_ss - taxable_ss_amt).

        When combined_ss > 0 and taxable_ss_amt < combined_ss, aca_magi > magi.
        When taxable_ss_amt == 0 (SS below provisional threshold), the full SS
        benefit is added to aca_magi.
        """
        # Low-income scenario: SS provisional income below $32K → taxable_ss=0,
        # so aca_magi = magi + combined_ss (the entire benefit is non-taxable).
        hh = Household(
            your_age=62,
            spouse_age=62,
            your_ira=200_000,
            spouse_ira=200_000,
            your_ss_fra=1_000,  # $1,000/mo
            spouse_ss_fra=800,
            your_ss_start_age=62,
            spouse_ss_start_age=62,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.0,
            living_expenses=40_000,
            your_aca_enrolled=True,
            aca_benchmark_premium_annual=15_000,
        )
        plan = ConversionPlan(your_conversions={hh.base_year: 0})
        result = run_scenario(hh, plan, end_age=63)

        yr = result.years[0]
        expected_aca_magi = yr.magi + (yr.combined_ss - yr.taxable_ss_amt)
        assert yr.aca_magi == approx(expected_aca_magi)
        # When some SS is non-taxable, aca_magi should exceed magi
        if yr.combined_ss > yr.taxable_ss_amt:
            assert yr.aca_magi > yr.magi


    # --- F4: full-actuarial survivor SS reduction tests ---

    def test_survivor_under_60_receives_no_benefit(self):
        """F4: survivor under age 60 in first survivor-active year gets 0 survivor benefit.

        Setup: who_dies='you', death_year=2026 (base year).
        Spouse age=54 in 2026 -> age 55 in first survivor year (2027) -> under 60 -> 0.
        Spouse has no own SS benefit (ss_start_age=67, age 55 in 2027 < 67).
        """
        from engine.scenario_compute import compute_social_security

        surv = SurvivorScenario(who_dies="you", death_year=2026)
        hh = Household(
            your_age=61,
            spouse_age=54,
            base_year=2026,
            your_ira=500_000,
            spouse_ira=400_000,
            your_ss_fra=3_000,
            spouse_ss_fra=2_000,
            your_ss_start_age=62,  # you collecting
            spouse_ss_start_age=67,  # spouse not collecting yet
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.0,
            living_expenses=50_000,
            ss_cola=0.0,
            survivor=surv,
        )
        # ya=62, sa=55 is first survivor-active year (2027)
        your_ss, spouse_ss, combined_ss, _ = compute_social_security(
            hh=hh,
            ya=62,
            sa=55,
            survivor_active=True,
            who_dies="you",
            current_filing_status="Single",
            your_conversion=0.0,
            spouse_conversion=0.0,
            taxable_rmd=0.0,
            spouse_taxable_rmd=0.0,
            extra_withdrawal=0.0,
            spouse_extra_withdrawal=0.0,
            option_income=0.0,
            your_inherited_distribution=0.0,
            spouse_inherited_distribution=0.0,
            ord_div_this_year=0.0,
            ytd_year=None,
            qual_div_this_year=0.0,
            realized_gains=0.0,
            death_year=2026,
        )
        assert your_ss == approx(0.0)
        assert spouse_ss == approx(0.0)
        assert combined_ss == approx(0.0)

    def test_survivor_at_60_receives_71_5_pct(self):
        """F4: survivor claiming at onset age 60 receives exactly 71.5% of deceased benefit.

        Onset age locked at 60 (claim_age=60). ss_cola=0 so no compounding.
        Survivor's own benefit is 0 (not yet at ss_start_age).
        """
        from engine.scenario_compute import compute_social_security

        surv = SurvivorScenario(who_dies="you", death_year=2031)
        # spouse_age=55 in 2026 -> age 60 in 2031 -> onset_age=60 in first survivor year 2032
        Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=500_000,
            spouse_ira=400_000,
            your_ss_fra=3_000,
            spouse_ss_fra=2_000,
            your_ss_start_age=62,
            spouse_ss_start_age=67,  # survivor not yet collecting own
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.0,
            living_expenses=50_000,
            ss_cola=0.0,
            survivor=surv,
        )
        # ya=67, sa=61: first survivor-active year is 2032 (death_year+1).
        # onset_age = spouse_age(55) + (2031+1 - 2026) = 55+6 = 61, claim_age=max(60,61)=61.
        # But let's use onset=60 directly: death_year=2030, spouse_age=55 -> onset=55+(2031-2026)=60.
        surv2 = SurvivorScenario(who_dies="you", death_year=2030)
        hh2 = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=500_000,
            spouse_ira=400_000,
            your_ss_fra=3_000,
            spouse_ss_fra=2_000,
            your_ss_start_age=62,
            spouse_ss_start_age=67,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.0,
            living_expenses=50_000,
            ss_cola=0.0,
            survivor=surv2,
        )
        # sa=60 in first survivor year (2031), onset_age=55+(2031-2026)=60, claim_age=60
        # deceased benefit: your_ss at ya=66, ss_start_age=62, cola=0 -> ss_benefit_at_age * 1.0
        your_ss_full, spouse_ss_out, combined_ss, _ = compute_social_security(
            hh=hh2,
            ya=66,
            sa=60,
            survivor_active=True,
            who_dies="you",
            current_filing_status="Single",
            your_conversion=0.0,
            spouse_conversion=0.0,
            taxable_rmd=0.0,
            spouse_taxable_rmd=0.0,
            extra_withdrawal=0.0,
            spouse_extra_withdrawal=0.0,
            option_income=0.0,
            your_inherited_distribution=0.0,
            spouse_inherited_distribution=0.0,
            ord_div_this_year=0.0,
            ytd_year=None,
            qual_div_this_year=0.0,
            realized_gains=0.0,
            death_year=2030,
        )
        # deceased (you) zeroed
        assert your_ss_full == approx(0.0)
        # survivor benefit = 71.5% of deceased full benefit; own SS = 0 (age 60 < ss_start 67)
        from engine.ira import ss_benefit_at_age

        expected_deceased = ss_benefit_at_age(3_000, 62, 67)  # claimed at 62, fra=67
        assert spouse_ss_out == approx(expected_deceased * 0.715)
        assert combined_ss == approx(spouse_ss_out)

        # Verify: two years later (sa=62) reduction is STILL 71.5% — onset locked, not growing.
        _, spouse_ss_later, _, _ = compute_social_security(
            hh=hh2,
            ya=68,
            sa=62,
            survivor_active=True,
            who_dies="you",
            current_filing_status="Single",
            your_conversion=0.0,
            spouse_conversion=0.0,
            taxable_rmd=0.0,
            spouse_taxable_rmd=0.0,
            extra_withdrawal=0.0,
            spouse_extra_withdrawal=0.0,
            option_income=0.0,
            your_inherited_distribution=0.0,
            spouse_inherited_distribution=0.0,
            ord_div_this_year=0.0,
            ytd_year=None,
            qual_div_this_year=0.0,
            realized_gains=0.0,
            death_year=2030,
        )
        # claim_age is still 60 (onset locked) -> same 71.5% factor -> same nominal amount (cola=0)
        assert spouse_ss_later == approx(spouse_ss_out)

    def test_survivor_at_fra_receives_full_benefit(self):
        """F4: survivor with onset age >= FRA (67) receives 100% of deceased's benefit.

        Both spouses past SS start age; who_dies='spouse', death_year=2026.
        Survivor (you) age 61 in base year -> onset_age = 62 in 2027 < 67 ... use older ages.
        your_age=70: onset = 70+(2030+1-2026)=75 >= fra=67 -> factor=1.0.
        """
        from engine.ira import ss_benefit_at_age
        from engine.scenario_compute import compute_social_security

        surv = SurvivorScenario(who_dies="spouse", death_year=2030)
        hh = Household(
            your_age=70,
            spouse_age=68,
            base_year=2026,
            your_ira=500_000,
            spouse_ira=400_000,
            your_ss_fra=3_000,
            spouse_ss_fra=4_000,  # deceased (spouse) is higher earner
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            growth_rate=0.0,
            living_expenses=50_000,
            ss_cola=0.0,
            survivor=surv,
        )
        # First survivor year: 2031. ya=75, sa=73, onset=70+(2031-2026)=75 >= fra=67.
        your_ss_out, spouse_ss_out, combined_ss, _ = compute_social_security(
            hh=hh,
            ya=75,
            sa=73,
            survivor_active=True,
            who_dies="spouse",
            current_filing_status="Single",
            your_conversion=0.0,
            spouse_conversion=0.0,
            taxable_rmd=0.0,
            spouse_taxable_rmd=0.0,
            extra_withdrawal=0.0,
            spouse_extra_withdrawal=0.0,
            option_income=0.0,
            your_inherited_distribution=0.0,
            spouse_inherited_distribution=0.0,
            ord_div_this_year=0.0,
            ytd_year=None,
            qual_div_this_year=0.0,
            realized_gains=0.0,
            death_year=2030,
        )
        # deceased (spouse) zeroed; survivor (you) gets 100% of deceased benefit
        assert spouse_ss_out == approx(0.0)
        deceased_benefit = ss_benefit_at_age(4_000, 70, 67)  # claimed at 70, fra=67
        own_benefit = ss_benefit_at_age(3_000, 70, 67)
        # max(own, 100% deceased); deceased > own -> survivor_total = deceased_benefit
        expected = max(own_benefit, deceased_benefit)
        assert your_ss_out == approx(expected)
        assert combined_ss == approx(your_ss_out)

    def test_survivor_own_benefit_wins_over_reduced_survivor(self):
        """F4: when survivor's own retirement benefit > reduced survivor benefit, own wins.

        Survivor claims own SS at FRA; deceased had small benefit; survivor's own > 71.5%.
        """
        from engine.ira import ss_benefit_at_age
        from engine.scenario_compute import compute_social_security

        surv = SurvivorScenario(who_dies="spouse", death_year=2030)
        hh = Household(
            your_age=55,
            spouse_age=55,
            base_year=2026,
            your_ira=500_000,
            spouse_ira=400_000,
            your_ss_fra=5_000,   # survivor (you) has large own benefit
            spouse_ss_fra=1_000,  # deceased had small benefit
            your_ss_start_age=60,  # survivor collecting since 60
            spouse_ss_start_age=62,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.0,
            living_expenses=50_000,
            ss_cola=0.0,
            survivor=surv,
        )
        # First survivor year: 2031. ya=60, sa=60. onset=55+(2031-2026)=60, claim_age=60.
        your_ss_out, spouse_ss_out, combined_ss, _ = compute_social_security(
            hh=hh,
            ya=60,
            sa=60,
            survivor_active=True,
            who_dies="spouse",
            current_filing_status="Single",
            your_conversion=0.0,
            spouse_conversion=0.0,
            taxable_rmd=0.0,
            spouse_taxable_rmd=0.0,
            extra_withdrawal=0.0,
            spouse_extra_withdrawal=0.0,
            option_income=0.0,
            your_inherited_distribution=0.0,
            spouse_inherited_distribution=0.0,
            ord_div_this_year=0.0,
            ytd_year=None,
            qual_div_this_year=0.0,
            realized_gains=0.0,
            death_year=2030,
        )
        own_benefit = ss_benefit_at_age(5_000, 60, 67)
        deceased_benefit = ss_benefit_at_age(1_000, 62, 67)
        survivor_benefit = deceased_benefit * 0.715  # onset=60, fra=67
        expected = max(own_benefit, survivor_benefit)
        # own_benefit >> survivor_benefit (large own vs small deceased reduced by 28.5%)
        assert own_benefit > survivor_benefit
        assert your_ss_out == approx(expected)
        assert spouse_ss_out == approx(0.0)
        assert combined_ss == approx(your_ss_out)


class TestRMDStartAge:
    """rmd-1: default_rmd_age helper and Household.__post_init__ derivation.

    SECURE 2.0 §107 / IRC §401(a)(9)(C)(v): born 1951-1959 → 73; born 1960+ → 75.
    """

    def test_default_rmd_age_boundary_1950(self):
        from models.household import default_rmd_age

        assert default_rmd_age(1950) == 75

    def test_default_rmd_age_boundary_1951(self):
        from models.household import default_rmd_age

        assert default_rmd_age(1951) == 73

    def test_default_rmd_age_boundary_1959(self):
        from models.household import default_rmd_age

        assert default_rmd_age(1959) == 73

    def test_default_rmd_age_boundary_1960(self):
        from models.household import default_rmd_age

        assert default_rmd_age(1960) == 75

    def test_default_rmd_age_later(self):
        from models.household import default_rmd_age

        assert default_rmd_age(1965) == 75

    def test_post_init_derives_73_for_1955_cohort(self):
        """Both spouses born 1955 (age 71 in 2026) → statutory age 73."""
        hh = Household(base_year=2026, your_age=71, spouse_age=71)
        assert hh.your_rmd_start_age == 73
        assert hh.spouse_rmd_start_age == 73

    def test_post_init_stays_75_for_1960_plus_cohort(self):
        """Born 1965/1971 (ages 61/55 in 2026) → statutory age 75."""
        hh = Household(base_year=2026, your_age=61, spouse_age=55)
        assert hh.your_rmd_start_age == 75
        assert hh.spouse_rmd_start_age == 75

    def test_post_init_explicit_non75_preserved(self):
        """Explicit rmd_start_age != 75 must not be overridden by __post_init__."""
        hh = Household(base_year=2026, your_age=71, your_rmd_start_age=74)
        assert hh.your_rmd_start_age == 74


class TestRMDCohortBirthYear:
    """ira-rmd-3: explicit birth-year overrides the age-derived cohort.

    SECURE 2.0 §107 / IRC §401(a)(9)(C)(v): born 1951-1959 → 73; born 1960+ → 75.

    The age-only path (base_year - your_age) has a one-year off-by-one for people
    born late in a year: someone born Nov 1959 who is 66 at the start of 2026
    yields birth_year = 2026 - 66 = 1960 → 75, suppressing two RMD years.
    Providing your_birth_year=1959 forces the correct cohort (73).
    """

    def test_explicit_birth_year_1959_yields_73(self):
        """Person born late 1959 is age 66 at start of 2026.

        Age-only path: 2026 - 66 = 1960 → 75 (wrong cohort, suppresses RMDs at 73-74).
        Explicit your_birth_year=1959 must resolve to 73.
        """
        hh = Household(base_year=2026, your_age=66, your_birth_year=1959)
        assert hh.your_rmd_start_age == 73

    def test_explicit_birth_year_1960_yields_75(self):
        """Person born 1960 (age 65 in 2026) → cohort 75 (correct either way)."""
        hh = Household(base_year=2026, your_age=65, your_birth_year=1960)
        assert hh.your_rmd_start_age == 75

    def test_explicit_birth_year_1951_yields_73(self):
        """Lower boundary of SECURE 2.0 73-cohort: born 1951 → 73."""
        hh = Household(base_year=2026, your_age=75, your_birth_year=1951)
        assert hh.your_rmd_start_age == 73

    def test_spouse_explicit_birth_year_1959_yields_73(self):
        """Spouse born 1959 (age 66 in 2026) → cohort 73 via spouse_birth_year."""
        hh = Household(base_year=2026, your_age=68, spouse_age=66, spouse_birth_year=1959)
        assert hh.spouse_rmd_start_age == 73

    def test_no_birth_year_preserves_age_only_derivation(self):
        """Omitting birth year uses the existing base_year - age convention (backward compat)."""
        hh = Household(base_year=2026, your_age=71)  # 2026 - 71 = 1955 → 73
        assert hh.your_rmd_start_age == 73

    def test_explicit_rmd_start_age_not_overridden_by_birth_year(self):
        """Explicit rmd_start_age != 75 is preserved even when birth_year is also supplied."""
        hh = Household(base_year=2026, your_age=66, your_birth_year=1959, your_rmd_start_age=72)
        assert hh.your_rmd_start_age == 72
