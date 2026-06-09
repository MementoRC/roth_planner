"""Test suite — validates engine against known verified numbers from spreadsheets."""

import pytest

from config.defaults import DEFAULTS
from engine.aca import aca_applies, aca_subsidy, aca_subsidy_loss
from engine.ira import calc_rmd, project_ira, rmd_divisor, ss_benefit_at_age, ss_with_cola
from engine.irmaa import irmaa_next_threshold, irmaa_surcharge
from engine.niit import niit
from engine.scenario import (
    ConversionPlan,
    add_bracket_fill_withdrawals,
    auto_fill_12,
    auto_fill_22,
    auto_fill_irmaa_safe,
    run_no_conversion,
    run_scenario,
)
from engine.tax import (
    deductions,
    federal_tax,
    marginal_rate,
    room_to_12,
    room_to_22,
    senior_bonus_deduction,
    taxable_ss,
)
from models.household import GrowthProfile, Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestTaxEngine:
    def test_tax_on_zero(self):
        assert federal_tax(0) == 0

    def test_tax_top_of_10pct(self):
        assert federal_tax(24_800) == approx(24_800 * 0.10)

    def test_tax_top_of_12pct(self):
        t = 24_800 * 0.10 + (100_800 - 24_800) * 0.12
        assert federal_tax(100_800) == approx(t)

    def test_tax_top_of_22pct(self):
        t = 24_800 * 0.10 + (100_800 - 24_800) * 0.12 + (211_400 - 100_800) * 0.22
        assert federal_tax(211_400) == approx(t)

    def test_marginal_rates(self):
        assert marginal_rate(50_000) == 0.12
        assert marginal_rate(150_000) == 0.22
        assert marginal_rate(300_000) == 0.24

    def test_room_to_12_no_income(self):
        assert room_to_12(0, 32_200) == approx(133_000)

    def test_room_to_12_with_options(self):
        assert room_to_12(69_934, 32_200) == approx(63_066)

    def test_room_to_22_no_income(self):
        assert room_to_22(0, 32_200) == approx(243_600)


class TestSSTaxation:
    def test_mid_tier(self):
        tss = taxable_ss(40_000, 20_000)
        expected = 0.5 * (40_000 - 32_000)
        assert tss == approx(expected)

    def test_85pct_tier(self):
        tss = taxable_ss(100_000, 200_000)
        expected = min(0.85 * (250_000 - 44_000) + 6_000, 0.85 * 100_000)
        assert tss == approx(expected)


class TestDeductions:
    def test_under_65(self):
        assert deductions(61, 55) == 32_200

    def test_one_senior(self):
        assert deductions(65, 59) == 32_200 + 1_650

    def test_both_senior(self):
        assert deductions(75, 69) == 32_200 + 2 * 1_650


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


class TestGrants:
    def test_grant_spreads(self):
        hh = Household()
        price = DEFAULTS["stock_price_now"]
        for i, g in enumerate(hh.grants):
            expected = DEFAULTS["grants"][i]
            assert g.spread(price) == approx(expected.spread(price))

    def test_total_spread(self):
        hh = Household()
        price = DEFAULTS["stock_price_now"]
        total = sum(g.spread(price) for g in hh.grants)
        expected = sum(g.spread(price) for g in DEFAULTS["grants"])
        assert total == approx(expected, tol=10)

    def test_option_income_by_year(self):
        hh = Household()
        assert hh.option_income(2026, True) == approx(hh.grants[0].spread(hh.txn_price_now))
        assert hh.option_income(2027, True) == approx(hh.grants[1].spread(hh.txn_price_now))
        assert hh.option_income(2028, True) == approx(hh.grants[2].spread(hh.txn_price_now))
        assert hh.option_income(2029, True) == 0


class TestIRMAA:
    def test_below_threshold(self):
        assert irmaa_surcharge(200_000) == 0

    def test_above_tier1(self):
        assert irmaa_surcharge(220_000) > 0

    def test_room_to_next(self):
        assert irmaa_next_threshold(200_000) == approx(18_000)

    def test_part_b_base_default_unchanged(self):
        """Explicit default (202.90/mo) produces the same surcharge as the module constant."""
        magi = 220_000
        assert irmaa_surcharge(magi) == irmaa_surcharge(magi, base_part_b=202.90 * 12)

    def test_part_b_base_higher_reduces_surcharge(self):
        """Higher base_part_b → smaller surcharge: tier premium - higher_base < tier premium - lower_base."""
        magi = 220_000  # above Tier 1 threshold
        default_surcharge = irmaa_surcharge(magi)
        higher_base_surcharge = irmaa_surcharge(magi, base_part_b=300.0 * 12)
        assert higher_base_surcharge < default_surcharge

    def test_household_field_wires_through_scenario(self):
        """medicare_part_b_base_monthly on Household reaches irmaa_for_year via run_scenario.

        Use a large conversion at age 63 so MAGI exceeds the $218K Tier 1 threshold.
        Both spouses are 63, so the 2-year lookback puts them on Medicare at 65.
        With default base ($202.90/mo) a positive surcharge is expected.
        With a raised base ($300/mo) the per-tier delta shrinks, so total IRMAA is lower.
        """
        hh_default = Household(your_age=63, spouse_age=63)
        hh_high_base = Household(
            your_age=63, spouse_age=63, medicare_part_b_base_monthly=300.0
        )
        # Conversion large enough to push MAGI above Tier 1 ($218K)
        plan = ConversionPlan(your_conversions={2026: 250_000})
        r_default = run_scenario(hh_default, plan, end_age=68)
        r_high = run_scenario(hh_high_base, plan, end_age=68)
        irmaa_default = sum(yr.irmaa_cost for yr in r_default.years)
        irmaa_high = sum(yr.irmaa_cost for yr in r_high.years)
        assert irmaa_default > 0, "Sanity: default base must trigger IRMAA"
        assert irmaa_high < irmaa_default

    # --- PR5: prior_year_magi anchor + proper temporal accounting ---

    def test_irmaa_default_year_0_unchanged_from_old_engine(self):
        """With no prior_year_magi, year-0 IRMAA falls back to yr.magi (same as pre-PR5).

        The fallback branch is reached because income_year = base_year - 2 is neither
        in prior_year_magi nor in magi_history (which only accumulates during the loop).
        """
        from engine.irmaa import irmaa_for_year

        hh = Household(your_age=63, spouse_age=63)
        plan = ConversionPlan(your_conversions={2026: 250_000})
        result = run_scenario(hh, plan, end_age=66)
        yr0 = result.years[0]

        # Compute expected IRMAA using yr0.magi directly (old-engine behaviour)
        expected_cost, _ = irmaa_for_year(
            yr0.magi, yr0.your_age, yr0.spouse_age,
            base_part_b=hh.medicare_part_b_base_monthly * 12,
        )
        assert yr0.irmaa_cost == approx(expected_cost)
        assert yr0.irmaa_cost > 0, "Sanity: high-MAGI year 0 must produce nonzero IRMAA"

    def test_irmaa_year_2_uses_year_0_magi(self):
        """Year-2 IRMAA is anchored to year-0 MAGI (2-year lookback), not year-2 MAGI.

        Build a scenario where year 0 has a large conversion (high MAGI) and
        year 2 has no conversion (low MAGI).  Under the new semantics year-2
        IRMAA must equal irmaa_for_year(year-0 MAGI) and differ from
        irmaa_for_year(year-2 MAGI).
        """
        from engine.irmaa import irmaa_for_year

        hh = Household(your_age=63, spouse_age=63)
        # Large conversion in year 0 only — year 2 has no conversion
        plan = ConversionPlan(your_conversions={2026: 300_000})
        result = run_scenario(hh, plan, end_age=68)

        yr0 = result.years[0]
        yr2 = result.years[2]

        # Year-2 IRMAA should reflect year-0 MAGI (high — above tier 1)
        expected_from_yr0, _ = irmaa_for_year(
            yr0.magi, yr2.your_age, yr2.spouse_age,
            base_part_b=hh.medicare_part_b_base_monthly * 12,
        )
        # Year-2 MAGI (no conversion) should produce a lower IRMAA
        expected_from_yr2, _ = irmaa_for_year(
            yr2.magi, yr2.your_age, yr2.spouse_age,
            base_part_b=hh.medicare_part_b_base_monthly * 12,
        )
        assert yr2.irmaa_cost == approx(expected_from_yr0), (
            "PR5: year-2 IRMAA must use year-0 projected MAGI"
        )
        assert expected_from_yr0 > expected_from_yr2, (
            "Sanity: year-0 high-MAGI should produce more IRMAA than year-2 low-MAGI"
        )

    def test_prior_year_magi_anchor_drives_year_0_irmaa(self):
        """prior_year_magi[base_year-2] anchors year-0 IRMAA.

        When the user provides an actual filed MAGI for the lookback year the
        engine must use it instead of the same-year fallback.
        """
        from engine.irmaa import irmaa_for_year

        base_year = 2026
        filed_magi = 300_000.0  # above IRMAA Tier 1 ($218K)

        hh_no_anchor = Household(your_age=63, spouse_age=63)
        hh_anchored = Household(
            your_age=63, spouse_age=63,
            prior_year_magi={base_year - 2: filed_magi},
        )
        plan = ConversionPlan()  # no conversions — year-0 MAGI low without anchor
        r_no = run_scenario(hh_no_anchor, plan, end_age=66)
        r_anc = run_scenario(hh_anchored, plan, end_age=66)

        yr0_no = r_no.years[0]
        yr0_anc = r_anc.years[0]

        expected_anchored, _ = irmaa_for_year(
            filed_magi, yr0_anc.your_age, yr0_anc.spouse_age,
            base_part_b=hh_anchored.medicare_part_b_base_monthly * 12,
        )
        assert yr0_anc.irmaa_cost == approx(expected_anchored), (
            "Anchored IRMAA must equal irmaa_for_year(filed_magi)"
        )
        assert yr0_anc.irmaa_cost != approx(yr0_no.irmaa_cost, tol=1.0), (
            "Anchor must change year-0 IRMAA vs no-anchor baseline"
        )

    def test_prior_year_magi_doesnt_affect_year_2_onwards(self):
        """prior_year_magi anchor only applies to lookback years present in the dict.

        Year-2 IRMAA is based on year-0 projected MAGI (magi_history), not on
        any prior_year_magi value (which keys are base_year-2 and base_year-1,
        both predating the projection window).
        """
        from engine.irmaa import irmaa_for_year

        base_year = 2026
        hh_anchored = Household(
            your_age=63, spouse_age=63,
            prior_year_magi={base_year - 2: 300_000.0, base_year - 1: 310_000.0},
        )
        plan = ConversionPlan(your_conversions={2026: 250_000})
        result = run_scenario(hh_anchored, plan, end_age=68)

        yr0 = result.years[0]
        yr2 = result.years[2]

        # Year-2 income_year = 2028 - 2 = 2026 = base_year, which IS in magi_history
        expected_from_yr0_magi, _ = irmaa_for_year(
            yr0.magi, yr2.your_age, yr2.spouse_age,
            base_part_b=hh_anchored.medicare_part_b_base_monthly * 12,
        )
        assert yr2.irmaa_cost == approx(expected_from_yr0_magi), (
            "Year-2 IRMAA must use year-0 projected MAGI, not prior_year_magi"
        )


class TestNIIT:
    def test_below_threshold(self):
        assert niit(200_000, 50_000) == 0

    def test_above_threshold(self):
        # MAGI $300K, NII $50K → excess = $50K, min(50K, 50K) = $50K × 3.8%
        assert niit(300_000, 50_000) == approx(50_000 * 0.038)

    def test_nii_less_than_excess(self):
        # MAGI $400K, NII $20K → excess = $150K, min(20K, 150K) = $20K × 3.8%
        assert niit(400_000, 20_000) == approx(20_000 * 0.038)

    def test_excess_less_than_nii(self):
        # MAGI $260K, NII $50K → excess = $10K, min(50K, 10K) = $10K × 3.8%
        assert niit(260_000, 50_000) == approx(10_000 * 0.038)

    def test_zero_investment_income(self):
        assert niit(500_000, 0) == 0


class TestACA:
    def test_applies_pre_medicare(self):
        assert aca_applies(61) is True
        assert aca_applies(64) is True
        assert aca_applies(65) is False

    def test_low_income_subsidy(self):
        assert aca_subsidy(30_000) > 15_000

    def test_high_income_subsidy(self):
        aca_subsidy(300_000)  # just verify no error

    def test_benchmark_premium_default_unchanged(self):
        """Default benchmark (21600) must produce same subsidy loss as the old hardcoded constant."""
        base_magi = 60_000.0
        new_magi = 80_000.0
        default_loss = aca_subsidy_loss(base_magi, new_magi, 21_600.0)
        assert default_loss == aca_subsidy_loss(base_magi, new_magi)

    def test_benchmark_premium_doubled_increases_loss(self):
        """Doubling the benchmark raises subsidy loss when new_magi crosses 400% FPL cliff.

        Pre-ARP: subsidies cut off above 400% FPL ($84,600 for family of 2).
        base_magi (60k) stays below the cliff (subsidy positive).
        new_magi (100k) is above the cliff (subsidy = 0 by rule).
        Loss = aca_subsidy(base) - 0; a higher benchmark raises aca_subsidy(base).
        """
        base_magi = 60_000.0
        new_magi = 100_000.0  # above 400% FPL — pre-ARP subsidy = 0
        loss_default = aca_subsidy_loss(base_magi, new_magi, 21_600.0)
        loss_double = aca_subsidy_loss(base_magi, new_magi, 43_200.0)
        assert loss_double > loss_default

    def test_household_benchmark_field_wires_through_scenario(self):
        """Household.aca_benchmark_premium_annual flows into run_scenario aca_loss.

        Uses a low base income so the household is below 400% FPL in base year
        and the 30k conversion pushes new_magi above the cliff.
        """
        from dataclasses import replace

        hh_default = Household(
            your_age=61,
            spouse_age=65,  # spouse already on Medicare — only "you" trigger ACA
            your_ira=200_000,
            spouse_ira=200_000,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            your_aca_enrolled=True,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        hh_double = replace(hh_default, aca_benchmark_premium_annual=43_200.0)

        # Conversion of 100k in base year — pushes MAGI above 400% FPL ($84,600)
        # while base MAGI (no conversion) is 0 → subsidy loss = aca_subsidy(0) - 0
        # With higher benchmark, aca_subsidy(0) is larger → loss_double > loss_default
        plan = ConversionPlan(your_conversions={2026: 100_000})
        result_default = run_scenario(hh_default, plan)
        result_double = run_scenario(hh_double, plan)

        loss_default = sum(yr.aca_loss for yr in result_default.years)
        loss_double = sum(yr.aca_loss for yr in result_double.years)
        assert loss_double > loss_default

    def test_enhanced_subsidies_default_off_pre_arp_cliff(self):
        """With enhanced_subsidies_active=False, subsidy = 0 above 400% FPL (pre-ARP cliff)."""
        from engine.aca import FPL_2

        above_cliff = 4.1 * FPL_2  # above 400% FPL
        assert aca_subsidy(above_cliff, enhanced_subsidies_active=False) == 0.0

    def test_enhanced_subsidies_on_no_cliff(self):
        """With enhanced_subsidies_active=True, subsidy > 0 above 400% FPL (8.5% cap, no cliff)."""
        from engine.aca import BENCHMARK_PREMIUM_ANNUAL, FPL_2

        above_cliff = 4.1 * FPL_2  # above 400% FPL
        sub = aca_subsidy(above_cliff, enhanced_subsidies_active=True)
        # Enhanced: subsidy = benchmark - income * 8.5% (no cliff)
        expected = max(BENCHMARK_PREMIUM_ANNUAL - above_cliff * 0.085, 0)
        assert sub > 0.0
        assert sub == pytest.approx(expected)

    def test_household_aca_toggle_wires_through_scenario(self):
        """Household.aca_enhanced_subsidies_active flows into run_scenario aca_loss.

        At MAGI above 400% FPL, pre-ARP returns 0 loss (cliff absorbs it);
        enhanced returns positive loss (8.5% cap applies with no cliff).
        """
        from dataclasses import replace

        hh_base = Household(
            your_age=61,
            spouse_age=65,  # spouse on Medicare — only "you" triggers ACA
            your_ira=2_000_000,
            spouse_ira=0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            your_aca_enrolled=True,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            aca_enhanced_subsidies_active=False,
        )
        # Conversion of 200k pushes MAGI well above 400% FPL ($84,600).
        # base_magi (0) is below cliff so pre-ARP subsidy(base) > 0,
        # but new_magi (200k) is above cliff so pre-ARP subsidy(new) = 0 → loss > 0.
        # With enhanced=True, subsidy(new) uses 8.5% cap → smaller loss.
        plan = ConversionPlan(your_conversions={2026: 200_000})
        result_pre_arp = run_scenario(hh_base, plan)
        result_enhanced = run_scenario(replace(hh_base, aca_enhanced_subsidies_active=True), plan)

        loss_pre_arp = result_pre_arp.years[0].aca_loss
        loss_enhanced = result_enhanced.years[0].aca_loss
        # Pre-ARP: new_magi above cliff → subsidy(new) = 0 → loss = subsidy(base_magi=0)
        # Enhanced: subsidy(new) > 0 (8.5% cap) → loss is smaller
        assert loss_pre_arp > loss_enhanced


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

        hh = replace(Household(), your_age=61, spouse_age=55, rmd_start_age=75)
        assert hh.your_conv_window == max(75 - 1 - 61 + 1, 0)  # 14
        assert hh.spouse_conv_window == max(75 - 1 - 55 + 1, 0)  # 20
        assert hh.spouse_conv_window > hh.your_conv_window

    def test_conv_windows_symmetric_under_swap(self):
        """Under me<->spouse swap, the windows swap correctly."""
        from dataclasses import replace

        hh = replace(Household(), your_age=61, spouse_age=55, rmd_start_age=75)
        hh_sw = replace(hh, your_age=hh.spouse_age, spouse_age=hh.your_age)
        assert hh_sw.your_conv_window == hh.spouse_conv_window
        assert hh_sw.spouse_conv_window == hh.your_conv_window

    def test_ss_at_70(self):
        hh = Household()
        expected_annual = DEFAULTS["your_ss_fra"] * 1.24 * 12
        assert hh.your_ss_at_70() == approx(expected_annual)
        expected_spouse = DEFAULTS["spouse_ss_fra"] * 1.24 * 12
        assert hh.spouse_ss_at_70() == approx(expected_spouse)


class TestScenarios:
    def test_no_conversion_ira_at_75(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        years_to_75 = 75 - DEFAULTS["your_age"]
        expected_ira = project_ira(DEFAULTS["your_ira"], 0.07, years_to_75)
        assert yr75.your_ira_begin == approx(expected_ira, tol=500)

    def test_no_conversion_rmd_at_75(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        years_to_75 = 75 - DEFAULTS["your_age"]
        expected_ira = project_ira(DEFAULTS["your_ira"], 0.07, years_to_75)
        assert yr75.your_rmd == approx(expected_ira / 24.6, tol=100)

    def test_no_conversion_ss_at_75(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        ss_at_70 = DEFAULTS["your_ss_fra"] * 1.24 * 12
        years_cola = 75 - 70
        ss75 = ss_at_70 * 1.025**years_cola
        assert yr75.your_ss == approx(ss75, tol=100)

    def test_no_conversion_spouse_ss_starts_at_70(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        # Spouse reaches 70 when you are (your_age + (70 - spouse_age)) years old
        your_age_when_spouse_70 = DEFAULTS["your_age"] + (70 - DEFAULTS["spouse_age"])
        yr_before = next(yr for yr in result.years if yr.your_age == your_age_when_spouse_70 - 1)
        yr_start = next(yr for yr in result.years if yr.your_age == your_age_when_spouse_70)
        assert yr_before.spouse_ss == 0
        assert yr_start.spouse_ss > 0

    def test_12pct_fill_reduces_ira(self):
        hh = Household()
        plan = auto_fill_12(hh)
        result = run_scenario(hh, plan, "Fill 12%", end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        assert yr75.your_ira_begin < 4_000_000

    def test_22pct_fill_more_aggressive(self):
        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        plan_12 = auto_fill_12(hh)
        plan_22 = auto_fill_22(hh)
        total_12 = sum(plan_12.your_conversions.values()) + sum(plan_12.spouse_conversions.values())
        total_22 = sum(plan_22.your_conversions.values()) + sum(plan_22.spouse_conversions.values())
        assert total_22 > total_12

    def test_22pct_fill_reduces_ira_more(self):
        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        r12 = run_scenario(hh, auto_fill_12(hh), "12%", end_age=95)
        r22 = run_scenario(hh, auto_fill_22(hh), "22%", end_age=95)
        yr75_12 = next(yr for yr in r12.years if yr.your_age == 75)
        yr75_22 = next(yr for yr in r22.years if yr.your_age == 75)
        assert yr75_22.your_ira_begin < yr75_12.your_ira_begin

    def test_irmaa_safe_stays_under_threshold(self):
        hh = Household()
        plan = auto_fill_irmaa_safe(hh)
        result = run_scenario(hh, plan, "IRMAA-Safe", end_age=95)
        # During conversion years (pre-75), MAGI should stay under $218K
        for yr in result.years:
            if yr.your_age <= 74 and yr.your_conversion > 0:
                assert yr.magi <= 220_000  # small tolerance for SS taxation effects

    def test_bracket_fill_reduces_late_ira(self):
        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        base = auto_fill_12(hh)
        plan_bf = add_bracket_fill_withdrawals(hh, base, target_bracket=0.22)
        r12 = run_scenario(hh, base, "12%", end_age=95)
        r_bf = run_scenario(hh, plan_bf, "BF", end_age=95)
        yr90_12 = next(yr for yr in r12.years if yr.your_age == 90)
        yr90_bf = next(yr for yr in r_bf.years if yr.your_age == 90)
        assert yr90_bf.your_ira_begin < yr90_12.your_ira_begin

    def test_bracket_fill_has_extra_withdrawals(self):
        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        base = auto_fill_12(hh)
        plan_bf = add_bracket_fill_withdrawals(hh, base, target_bracket=0.22)
        assert len(plan_bf.extra_withdrawals) > 0
        # Extra withdrawals should only be post-RMD (age 75+)
        for year in plan_bf.extra_withdrawals:
            assert hh.your_age_in(year) >= 75


class TestAutoFillCharacterization:
    """Pin per-year output of the three auto_fill_* functions before refactor.

    These snapshots characterize today's behavior. If the upcoming
    _auto_fill_core extraction produces different per-year amounts,
    these tests will catch the drift even if end-to-end totals match.

    Captured against development @ ecbc49d (post-PR #41).
    """

    def _fixture_household(self) -> Household:
        """Mirror the fixture used by the existing auto_fill_* behavioral tests."""
        return Household()

    def test_auto_fill_12_year_by_year_snapshot(self):
        hh = self._fixture_household()
        plan = auto_fill_12(hh)
        # Per-year tuples: (year, your_conv, spouse_conv)
        rows = [
            (
                yr,
                round(plan.your_conversions.get(yr, 0.0)),
                round(plan.spouse_conversions.get(yr, 0.0)),
            )
            for yr in sorted(set(plan.your_conversions) | set(plan.spouse_conversions))
        ]
        # Captured against development @ ecbc49d (post-PR #41)
        expected: list[tuple[int, int, int]] = [
            (2026, 83000, 0),
            (2027, 113000, 0),
            (2028, 120500, 0),
            (2029, 133000, 0),
            (2030, 127902, 5098),
            (2031, 0, 133000),
            (2032, 0, 133000),
            (2033, 0, 133000),
            (2034, 0, 133000),
            (2035, 0, 133000),
            (2036, 0, 140650),
            (2037, 0, 18069),
            (2038, 0, 0),
            (2039, 0, 0),
            (2040, 0, 0),
            (2041, 0, 0),
            (2042, 0, 0),
            (2043, 0, 0),
            (2044, 0, 0),
            (2045, 0, 0),
            (2046, 0, 0),
            (2047, 0, 0),
        ]
        assert rows == expected

    def test_auto_fill_22_year_by_year_snapshot(self):
        hh = self._fixture_household()
        plan = auto_fill_22(hh)
        rows = [
            (
                yr,
                round(plan.your_conversions.get(yr, 0.0)),
                round(plan.spouse_conversions.get(yr, 0.0)),
            )
            for yr in sorted(set(plan.your_conversions) | set(plan.spouse_conversions))
        ]
        # Captured against development @ ecbc49d (post-PR #41)
        expected: list[tuple[int, int, int]] = [
            (2026, 193600, 0),
            (2027, 223600, 0),
            (2028, 111545, 119555),
            (2029, 0, 243600),
            (2030, 0, 243600),
            (2031, 0, 15267),
            (2032, 0, 0),
            (2033, 0, 0),
            (2034, 0, 0),
            (2035, 0, 0),
            (2036, 0, 0),
            (2037, 0, 0),
            (2038, 0, 0),
            (2039, 0, 0),
            (2040, 0, 0),
            (2041, 0, 0),
            (2042, 0, 0),
            (2043, 0, 0),
            (2044, 0, 0),
            (2045, 0, 0),
            (2046, 0, 0),
            (2047, 0, 0),
        ]
        assert rows == expected

    def test_auto_fill_irmaa_safe_year_by_year_snapshot(self):
        hh = self._fixture_household()
        plan = auto_fill_irmaa_safe(hh)
        rows = [
            (
                yr,
                round(plan.your_conversions.get(yr, 0.0)),
                round(plan.spouse_conversions.get(yr, 0.0)),
            )
            for yr in sorted(set(plan.your_conversions) | set(plan.spouse_conversions))
        ]
        # Captured against development @ ecbc49d (post-PR #41)
        # IRMAA-safe diverges from fill_12/fill_22 in base_magi computation:
        # uses full combined_ss (not taxable_ss) — these per-year rows capture that.
        expected: list[tuple[int, int, int]] = [
            (2026, 168000, 0),
            (2027, 198000, 0),
            (2028, 168247, 37253),
            (2029, 0, 218000),
            (2030, 0, 218000),
            (2031, 0, 172791),
            (2032, 0, 0),
            (2033, 0, 0),
            (2034, 0, 0),
            (2035, 0, 0),
            (2036, 0, 0),
            (2037, 0, 0),
            (2038, 0, 0),
            (2039, 0, 0),
            (2040, 0, 0),
            (2041, 0, 0),
            (2042, 0, 0),
            (2043, 0, 0),
            (2044, 0, 0),
            (2045, 0, 0),
            (2046, 0, 0),
            (2047, 0, 0),
        ]
        assert rows == expected


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


class TestPortfolioSync:
    """Test portfolio sync parsing and classification logic."""

    def test_classify_brokerage_account(self):
        from engine.portfolio_sync import _classify_account

        acct_type, owner = _classify_account("Claude R. Cirba — Brokerage Account — 39119320*")
        assert acct_type == "brokerage"
        assert owner == "you"

    def test_classify_roth_ira(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Claude R. Cirba — Roth IRA Brokerage Account — 61037368*")
        assert acct_type == "roth_ira"

    def test_classify_trad_ira(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Some Person — Traditional IRA — 12345678*")
        assert acct_type == "trad_ira"

    def test_classify_rollover_ira(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Rollover IRA233813501")
        assert acct_type == "trad_ira"

    def test_classify_403b(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("VANDERBILT 403B59208")
        assert acct_type == "403b"

    def test_classify_hsa(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Health Savings Account178734462")
        assert acct_type == "hsa"

    def test_classify_symbols(self):
        from engine.portfolio_sync import _classify_symbol

        assert _classify_symbol("VTI") == "equity"
        assert _classify_symbol("VXUS") == "equity"
        assert _classify_symbol("BND") == "bond"
        assert _classify_symbol("BNDX") == "bond"
        assert _classify_symbol("ITOT") == "equity"
        assert _classify_symbol("AGG") == "bond"
        assert _classify_symbol("FBTC") == "crypto"
        assert _classify_symbol("SHV") == "cash"
        assert _classify_symbol("Cash HELD IN MONEY MARKET") == "cash"
        assert _classify_symbol("VTTHX") == "target_date"
        assert _classify_symbol("UNKNOWN") == "equity"  # default

    def test_parse_quantity(self):
        from engine.portfolio_sync import _parse_quantity

        assert _parse_quantity(100) == 100.0
        assert _parse_quantity(3.14) == 3.14
        assert _parse_quantity("2,182.861") == approx(2182.861, tol=0.001)
        assert _parse_quantity(None) == 0.0
        assert _parse_quantity("") == 0.0

    def test_account_summary_weighted_return(self):
        from engine.portfolio_sync import AccountSummary

        acct = AccountSummary(
            account_type="brokerage",
            owner="you",
            total_value=100_000,
            equity_value=60_000,
            bond_value=40_000,
        )
        # 60% * 9% + 40% * 4% = 5.4% + 1.6% = 7.0%
        assert acct.weighted_return == approx(0.07, tol=0.001)
        assert acct.equity_pct == approx(0.60, tol=0.001)

    def test_account_summary_with_crypto_and_cash(self):
        from engine.portfolio_sync import AccountSummary

        acct = AccountSummary(
            account_type="trad_ira",
            owner="you",
            total_value=200_000,
            equity_value=80_000,
            bond_value=40_000,
            cash_value=40_000,
            crypto_value=40_000,
        )
        # 80k*9% + 40k*4% + 40k*4.5% + 40k*0% = 7200+1600+1800+0 = 10600
        # 10600/200000 = 5.3%
        assert acct.weighted_return == approx(0.053, tol=0.001)

    def test_account_summary_empty(self):
        from engine.portfolio_sync import AccountSummary

        acct = AccountSummary(account_type="brokerage", owner="you")
        assert acct.weighted_return == 0.0
        assert acct.equity_pct == 0.0

    def test_pretax_accounts(self):
        from engine.portfolio_sync import AccountSummary, PortfolioSnapshot

        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(
                    account_type="trad_ira",
                    owner="you",
                    total_value=1_500_000,
                    equity_value=500_000,
                    bond_value=500_000,
                    cash_value=500_000,
                ),
                AccountSummary(
                    account_type="403b",
                    owner="you",
                    total_value=140_000,
                    equity_value=100_000,
                    bond_value=40_000,
                ),
                AccountSummary(account_type="hsa", owner="you", total_value=60_000),
                AccountSummary(account_type="brokerage", owner="you", total_value=100_000),
            ],
            server_available=True,
        )
        assert len(snap.pretax_accounts) == 2
        assert snap.pretax_total == approx(1_640_000)
        assert snap.pretax_weighted_return > 0


class TestAssetLocation:
    """Test asset location engine — equity-first vs proportional vs bond-first."""

    def test_equity_first_reduces_ira_growth(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = {2026: 100_000, 2027: 100_000, 2028: 100_000}
        eq = project_asset_location(hh, conv, strategy="equity_first")
        prop = project_asset_location(hh, conv, strategy="proportional")
        # After converting equities, IRA growth rate should be lower
        assert eq.ira_growth_at_75 < prop.ira_growth_at_75

    def test_equity_first_smaller_ira_at_85(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = dict.fromkeys(range(2026, 2040), 100000)
        eq = project_asset_location(hh, conv, strategy="equity_first")
        prop = project_asset_location(hh, conv, strategy="proportional")
        bd = project_asset_location(hh, conv, strategy="bond_first")
        # Equity-first should have smallest IRA (slowest remaining growth)
        assert eq.ira_at_85 < prop.ira_at_85
        assert prop.ira_at_85 < bd.ira_at_85

    def test_equity_first_larger_roth(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = dict.fromkeys(range(2026, 2035), 100000)
        eq = project_asset_location(hh, conv, strategy="equity_first")
        bd = project_asset_location(hh, conv, strategy="bond_first")
        # Equity-first Roth should be larger (equities grow faster tax-free)
        eq_roth_85 = next(y for y in eq.years if y.your_age == 85).roth_total
        bd_roth_85 = next(y for y in bd.years if y.your_age == 85).roth_total
        assert eq_roth_85 > bd_roth_85

    def test_same_total_converted(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = dict.fromkeys(range(2026, 2040), 80000)
        eq = project_asset_location(hh, conv, strategy="equity_first")
        bd = project_asset_location(hh, conv, strategy="bond_first")
        assert eq.total_converted == approx(bd.total_converted)

    def test_no_conversion_same_for_all(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        eq = project_asset_location(hh, {}, strategy="equity_first")
        bd = project_asset_location(hh, {}, strategy="bond_first")
        # With no conversions, IRA trajectory should be identical
        assert eq.ira_at_85 == approx(bd.ira_at_85)

    def test_rmd_smaller_with_equity_first(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = dict.fromkeys(range(2026, 2040), 100000)
        eq = project_asset_location(hh, conv, strategy="equity_first")
        bd = project_asset_location(hh, conv, strategy="bond_first")
        assert eq.rmd_at_85 < bd.rmd_at_85


class TestSweetSpot:
    """Test the sweet spot finder computation helpers."""

    @pytest.fixture(autouse=True)
    def _require_plotly(self):
        pytest.importorskip("plotly")
        pytest.importorskip("streamlit")

    def test_base_income_no_ss_before_70(self):
        from views.sweet_spot import _base_income_for_year

        hh = Household()
        base = _base_income_for_year(hh, 2026)
        assert base["ya"] == DEFAULTS["your_age"]
        assert base["combined_ss"] == 0  # SS starts at 70

    def test_base_income_has_options(self):
        from views.sweet_spot import _base_income_for_year

        hh = Household()
        base = _base_income_for_year(hh, 2026)
        assert base["opt"] == approx(hh.grants[0].spread(hh.txn_price_now))

    def test_all_in_zero_conversion(self):
        from views.sweet_spot import _all_in_at_conversion, _base_income_for_year

        hh = Household()
        base = _base_income_for_year(hh, 2026)
        result = _all_in_at_conversion(hh, base, 0, 0)
        assert result["all_in"] == 0
        assert result["conv_tax"] == 0

    def test_all_in_increases_with_conversion(self):
        from views.sweet_spot import _all_in_at_conversion, _base_income_for_year

        hh = Household()
        base = _base_income_for_year(hh, 2026)
        r50k = _all_in_at_conversion(hh, base, 50_000, 0)
        r100k = _all_in_at_conversion(hh, base, 100_000, 0)
        assert r100k["all_in"] > r50k["all_in"]
        assert r50k["conv_tax"] > 0

    def test_irmaa_triggers_at_threshold(self):
        from views.sweet_spot import _all_in_at_conversion, _base_income_for_year

        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        base = _base_income_for_year(hh, 2029)  # age 64, no options
        # Find conversion just below and above IRMAA tier 1
        below = max(218_000 - base["base_magi"] - 1_000, 0)
        above = 218_000 - base["base_magi"] + 1_000
        if below > 0 and above > 0:
            r_below = _all_in_at_conversion(hh, base, below, 0)
            r_above = _all_in_at_conversion(hh, base, above, 0)
            assert r_above["irmaa_delta"] > r_below["irmaa_delta"]


# ============================================================
#  Tax Return Sync (TurboTax via FinExtract)
# ============================================================

# ============================================================
#  YTD Income Tracker & Headroom
# ============================================================


class TestYTDSnapshot:
    """Test YTD income data model properties."""

    def test_ltcg_not_in_ordinary(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(ltcg_ytd=200_000, stcg_ytd=10_000, wages_ytd=50_000)
        # LTCG should NOT be in ordinary income
        assert ytd.total_ordinary_income == approx(60_000)  # wages + stcg only
        # But should be in MAGI
        assert ytd.magi_ytd == approx(260_000)

    def test_stcg_in_ordinary(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(stcg_ytd=30_000)
        assert ytd.total_ordinary_income == approx(30_000)

    def test_magi_includes_all(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            wages_ytd=100_000,
            ltcg_ytd=200_000,
            stcg_ytd=10_000,
            ordinary_dividends_ytd=5_000,
            interest_ytd=3_000,
            ira_conversions_ytd=20_000,
        )
        expected = 100_000 + 200_000 + 10_000 + 5_000 + 3_000 + 20_000
        assert ytd.magi_ytd == approx(expected)

    def test_investment_income_for_niit(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            ltcg_ytd=150_000,
            stcg_ytd=20_000,
            ordinary_dividends_ytd=10_000,
            interest_ytd=5_000,
            wages_ytd=80_000,
        )
        # Investment income: LTCG + STCG + dividends + interest (no wages)
        assert ytd.total_investment_income == approx(185_000)

    def test_gain_event_properties(self):
        from models.ytd_income import RealizedGainEvent

        event = RealizedGainEvent(
            date="2026-03-15",
            description="TXN stop-loss",
            proceeds=250_000,
            cost_basis=150_000,
            holding_period="long",
            account_name="Schwab Brokerage",
        )
        assert event.gain_loss == approx(100_000)
        assert event.is_ltcg is True

        short_event = RealizedGainEvent(
            date="2026-03-15",
            description="AAPL sale",
            proceeds=50_000,
            cost_basis=45_000,
            holding_period="short",
        )
        assert short_event.gain_loss == approx(5_000)
        assert short_event.is_ltcg is False

    def test_total_ordinary_income_includes_ordinary_dividends_not_qualified(self):
        """Ordinary dividends are taxed as ordinary income; qualified are LTCG-rate only."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            wages_ytd=50_000,
            ordinary_dividends_ytd=3_000,
            qualified_dividends_ytd=2_000,
        )
        # ordinary income = wages + ordinary_dividends; qualified excluded
        assert ytd.total_ordinary_income == approx(53_000)
        # sum property still works
        assert ytd.dividends_ytd == approx(5_000)


class TestHeadroom:
    """Test conversion headroom calculations."""

    def test_ltcg_consumes_irmaa_not_brackets(self):
        """The critical test: $200K LTCG eats IRMAA room but not bracket room."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household()
        # No LTCG — full bracket and IRMAA room
        ytd_none = YTDSnapshot(tax_year=2026)
        hr_none = compute_headroom(hh, ytd_none)

        # $200K LTCG — should consume IRMAA but not brackets
        ytd_ltcg = YTDSnapshot(tax_year=2026, ltcg_ytd=200_000)
        hr_ltcg = compute_headroom(hh, ytd_ltcg)

        # Bracket room should be identical (LTCG doesn't stack into brackets)
        assert hr_ltcg.room_to_12pct == approx(hr_none.room_to_12pct)
        assert hr_ltcg.room_to_22pct == approx(hr_none.room_to_22pct)

        # IRMAA room should be much less (LTCG DOES affect MAGI)
        assert hr_ltcg.room_to_irmaa_t1 < hr_none.room_to_irmaa_t1

    def test_stcg_consumes_both(self):
        """STCG is ordinary income — consumes both bracket and IRMAA room."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd_none = YTDSnapshot(tax_year=2026)
        hr_none = compute_headroom(hh, ytd_none)

        ytd_stcg = YTDSnapshot(tax_year=2026, stcg_ytd=50_000)
        hr_stcg = compute_headroom(hh, ytd_stcg)

        # Both bracket AND IRMAA room should decrease
        assert hr_stcg.room_to_12pct < hr_none.room_to_12pct
        assert hr_stcg.room_to_irmaa_t1 < hr_none.room_to_irmaa_t1

    def test_irmaa_not_relevant_before_63(self):
        """Below age 63, IRMAA doesn't apply (Medicare starts at 65, 2-year lookback)."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household()  # age from DEFAULTS["your_age"]
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=200_000)
        hr = compute_headroom(hh, ytd)
        # IRMAA is NOT relevant if current age < 63
        assert hr.irmaa_relevant is False
        assert hr.irmaa_already_triggered is False
        # First relevant year: base_year + (63 - your_age)
        expected_first_year = 2026 + (63 - DEFAULTS["your_age"])
        assert hr.irmaa_first_relevant_year == expected_first_year

    def test_irmaa_triggered_at_63(self):
        """At age 63, IRMAA is relevant (income year + 2 = age 65 = Medicare)."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(your_age=63, base_year=2028)
        # $220K LTCG pushes locked MAGI over $218K threshold
        ytd = YTDSnapshot(tax_year=2028, ltcg_ytd=220_000)
        hr = compute_headroom(hh, ytd)
        assert hr.irmaa_relevant is True
        assert hr.irmaa_tier_current >= 1

    def test_niit_room(self):
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=100_000)
        hr = compute_headroom(hh, ytd)
        # NIIT threshold is $250K, option income ~$70K + $100K LTCG = ~$170K MAGI
        assert hr.room_to_niit > 0

    def test_conversions_done_tracked(self):
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ira_conversions_ytd=50_000)
        hr = compute_headroom(hh, ytd)
        assert hr.conversions_done == approx(50_000)

    def test_irmaa_advisory_uses_earlier_medicare(self):
        """When spouse is older, advisory year should reflect spouse's Medicare start.

        IRMAA advisory year should be min(your_medicare_year, spouse_medicare_year).
        Without the fix, it would incorrectly use only your age.
        """
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        # Case 1: You are 55, spouse is 64 (spouse reaches 65 first in 1 year)
        # Spouse's Medicare start (65) occurs in 1 year from now (2026 + 1 = 2027)
        # IRMAA lookback is 2 years before Medicare start, so first relevant income year
        # is 65 - 2 = 63, which occurs 1 year from now when spouse is 65 (2026 + 0 = 2026... wait, let me recalculate)
        # When spouse is 64 in 2026, they turn 65 in 2027.
        # Income in 2025 affects Medicare premiums starting at 65 (2027).
        # So 2025 is the first relevant income year (lookback from 2027 Medicare start).
        # From 2026: years_until_medicare = max(min(65-2-55, 65-2-64), 0) = max(min(8, -1), 0) = 0
        # So first_relevant_year = 2026 + 0 = 2026.
        # But wait: the income YEAR is 2026. Income in 2026 affects Medicare premiums
        # when spouse turns 65 in 2027? No: Medicare premium lookback is 2 years.
        # Income in 2026 + 2 = 2028 affects premiums at age 65.
        # So spouse reaches "first relevant" when they are age 63 in 2025 (income year),
        # because 2025 + 2 = 2027 = when they turn 65.
        # From 2026 perspective: first relevant is 2026 + (63 - 64) = 2026 - 1 = 2025 (clamped to min).
        # Actually simpler: at base_year 2026, spouse age 64:
        # years_until_spouse_relevant = max(65 - 2 - 64, 0) = max(-1, 0) = 0.
        hh = Household(your_age=55, spouse_age=64, base_year=2026)
        ytd = YTDSnapshot(tax_year=2026)
        hr = compute_headroom(hh, ytd)
        # Spouse reaches 65 at end of 2026 (age 64 → 65).
        # Income in 2024 affects premiums at 65 (2026).
        # So first relevant year is 2024 (which is 2026 - 2 from spouse age perspective).
        # From 2026: years_until_medicare = max(min(65-2-55, 65-2-64), 0) = max(min(8, -1), 0) = 0
        # Expected first_relevant_year = 2026 + 0 = 2026 ✓
        assert hr.irmaa_first_relevant_year == 2026

        # Case 2: Swap — you are 64, spouse is 55
        # years_until_medicare = max(min(65-2-64, 65-2-55), 0) = max(min(-1, 8), 0) = 0
        # Expected: 2026 + 0 = 2026 (same result) ✓
        hh2 = Household(your_age=64, spouse_age=55, base_year=2026)
        hr2 = compute_headroom(hh2, YTDSnapshot(tax_year=2026))
        assert hr2.irmaa_first_relevant_year == 2026


class TestScenarioWithYTD:
    """Test scenario engine with YTD injection."""

    def test_ltcg_in_magi_not_gross(self):
        """LTCG appears in base-year MAGI but NOT in combined_gross."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=200_000)
        plan = ConversionPlan(your_conversions={2026: 50_000})
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # MAGI should include LTCG
        assert yr2026.magi > 200_000

        # combined_gross should NOT include LTCG
        # (only option income + conversion + taxable SS)
        assert yr2026.combined_gross < 200_000

    def test_scenario_combined_gross_includes_ytd_ordinary_dividends(self):
        """Ordinary dividends in YTD snapshot must stack into combined_gross (ordinary income)."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd_no_div = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_div = YTDSnapshot(tax_year=2026, wages_ytd=50_000, ordinary_dividends_ytd=4_000)
        plan = ConversionPlan()

        result_no_div = run_scenario(hh, plan, "no_div", end_age=65, ytd=ytd_no_div)
        result_with_div = run_scenario(hh, plan, "with_div", end_age=65, ytd=ytd_with_div)

        yr_no_div = result_no_div.years[0]
        yr_with_div = result_with_div.years[0]

        # combined_gross in the dividend scenario should be exactly 4_000 higher
        assert yr_with_div.combined_gross - yr_no_div.combined_gross == approx(4_000)

    def test_ytd_does_not_affect_future_years(self):
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=200_000, wages_ytd=100_000)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "test", end_age=70, ytd=ytd)

        yr2026 = next(yr for yr in result.years if yr.year == 2026)
        yr2027 = next(yr for yr in result.years if yr.year == 2027)

        # 2026 should have YTD fields populated
        assert yr2026.ytd_ltcg == approx(200_000)
        assert yr2026.ytd_wages == approx(100_000)

        # 2027 should have zero YTD fields
        assert yr2027.ytd_ltcg == 0
        assert yr2027.ytd_wages == 0

    def test_conversions_done_subtracted(self):
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ira_conversions_ytd=30_000)
        plan = ConversionPlan(your_conversions={2026: 100_000})
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # Planned $100K minus $30K already done = $70K
        assert yr2026.your_conversion == approx(70_000)

    def test_ytd_save_load_roundtrip(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import RealizedGainEvent, YTDSnapshot

        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", tmp_path / "ytd.json")

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000,
            ltcg_ytd=200_000,
            stcg_ytd=10_000,
            ordinary_dividends_ytd=5_000,
            interest_ytd=3_000,
            ira_conversions_ytd=20_000,
            snapshot_date="2026-06-15",
            gain_events=[
                RealizedGainEvent(
                    date="2026-03-15",
                    description="TXN stop-loss",
                    proceeds=250_000,
                    cost_basis=50_000,
                    holding_period="long",
                    account_name="Schwab",
                ),
            ],
        )
        save_ytd_snapshot(ytd)
        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.wages_ytd == 50_000
        assert loaded.ltcg_ytd == 200_000
        assert loaded.stcg_ytd == 10_000
        assert loaded.dividends_ytd == 5_000
        assert loaded.interest_ytd == 3_000
        assert loaded.ira_conversions_ytd == 20_000
        assert len(loaded.gain_events) == 1
        assert loaded.gain_events[0].gain_loss == approx(200_000)


class TestTaxReturnParsing:
    """Test parsing of TurboTax income/deduction rows from FinExtract."""

    def test_parse_income_rows(self):
        from engine.portfolio_sync import _parse_tax_rows

        rows = [
            {
                "form_label": "Wages and Salaries (W-2)",
                "amount_current": 102225,
                "amount_prior": 118161,
            },
            {"form_label": "Form 1099-NEC", "amount_current": 4150, "amount_prior": None},
            {
                "form_label": "Investments and Savings",
                "amount_current": 92429,
                "amount_prior": 165861,
            },
            {
                "form_label": "IRA, 401(k), Pension Plan Withdrawals (1099-R)",
                "amount_current": 7397,
                "amount_prior": None,
            },
            {"form_label": "1099-SA, HSA, MSA", "amount_current": 895, "amount_prior": 583},
            {
                "form_label": "Miscellaneous Income, 1099-A, 1099-C",
                "amount_current": None,
                "amount_prior": 48401,
            },
        ]
        parsed = _parse_tax_rows(rows, "amount_current")
        assert parsed["wages"] == 102225
        assert parsed["nec_income"] == 4150
        assert parsed["investment_income"] == 92429
        assert parsed["ira_distributions"] == 7397
        assert parsed["hsa_distributions"] == 895
        assert "misc_income" not in parsed  # amount_current is None

    def test_parse_deduction_rows(self):
        from engine.portfolio_sync import _parse_tax_rows

        rows = [
            {"form_label": "HSA, MSA Contributions", "amount_current": 5300, "amount_prior": 5150},
            {
                "form_label": "Traditional and Roth IRA Contributions",
                "amount_current": 8000,
                "amount_prior": 16000,
            },
            {"form_label": "Sales Tax", "amount_current": 1686, "amount_prior": 1881},
            {"form_label": "Foreign Tax Credit", "amount_current": 365, "amount_prior": 355},
        ]
        parsed = _parse_tax_rows(rows, "amount_current")
        assert parsed["hsa_contributions"] == 5300
        assert parsed["ira_contributions"] == 8000
        assert parsed["sales_tax"] == 1686
        assert parsed["foreign_tax_credit"] == 365

    def test_parse_prior_year(self):
        from engine.portfolio_sync import _parse_tax_rows

        rows = [
            {
                "form_label": "Wages and Salaries (W-2)",
                "amount_current": 102225,
                "amount_prior": 118161,
            },
            {
                "form_label": "Investments and Savings",
                "amount_current": 92429,
                "amount_prior": 165861,
            },
        ]
        parsed = _parse_tax_rows(rows, "amount_prior")
        assert parsed["wages"] == 118161
        assert parsed["investment_income"] == 165861

    def test_tax_snapshot_estimated_magi(self):
        from engine.portfolio_sync import TaxReturnSnapshot

        snap = TaxReturnSnapshot(
            wages=102225,
            nec_income=4150,
            investment_income=92429,
            ira_distributions=7397,
            hsa_distributions=895,
            hsa_contributions=5300,
        )
        # total_income = 102225 + 4150 + 92429 + 7397 + 895 = 207096
        assert snap.total_income == 207096
        # estimated_magi = total - hsa_contributions - (nec * 0.0765)
        se_ded = 4150 * 0.0765
        expected = 207096 - 5300 - se_ded
        assert snap.estimated_magi == pytest.approx(expected, abs=1)

    def test_tax_snapshot_save_load_roundtrip(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import TaxReturnSnapshot, load_tax_snapshot, save_tax_snapshot

        monkeypatch.setattr(portfolio_sync, "_TAX_CACHE_PATH", tmp_path / "tax.json")

        snap = TaxReturnSnapshot(
            wages=100_000,
            investment_income=50_000,
            hsa_contributions=5_000,
            server_available=True,
        )
        save_tax_snapshot(snap)
        loaded = load_tax_snapshot()
        assert loaded is not None
        assert loaded.wages == 100_000
        assert loaded.investment_income == 50_000
        assert loaded.hsa_contributions == 5_000
        assert loaded.server_available is True


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


class TestDividendForecast:
    """Tests for engine.dividend_forecast."""

    def test_empty_portfolio_returns_zero(self):
        from engine.dividend_forecast import forecast_portfolio

        fcst = forecast_portfolio([], total_balance=0.0)
        assert fcst.yield_rate == 0.0
        assert fcst.qualified_fraction == 1.0

    def test_ttm_strategy(self):
        """TTM derivation: per-position dividends history → yield."""
        from engine.dividend_forecast import Position, forecast_portfolio

        positions = [
            Position(ticker="TXN", shares=1000, balance=200_000, ttm_dividends=5400),
        ]
        fcst = forecast_portfolio(positions, total_balance=200_000)
        # ttm_per_share = 5400/1000 = 5.4; annual_income = 1000 * 5.4 = 5400
        # yield_rate = 5400 / 200_000 = 0.027; TXN is equity → qualified_fraction = 1.0
        assert fcst.yield_rate == pytest.approx(0.027)
        assert fcst.qualified_fraction == pytest.approx(1.0)
        assert fcst.source_counts["ttm"] == 1

    def test_mixed_qualified_classifications(self):
        """REIT contributes ordinary; equity contributes qualified."""
        from engine.dividend_forecast import Position, forecast_portfolio

        positions = [
            Position(ticker="TXN", shares=500, balance=100_000, ttm_dividends=2700),  # 2.7% qual
            Position(ticker="VNQ", shares=100, balance=10_000, ttm_dividends=400),  # 4% ord (REIT)
        ]
        fcst = forecast_portfolio(positions, total_balance=110_000)
        # TXN: annual_income=2700, qual_frac=1.0 → qualified=2700, ordinary=0
        # VNQ: annual_income=400, qual_frac=0.0 → qualified=0, ordinary=400
        # total=3100, yield=3100/110_000, qual_frac=2700/3100
        assert fcst.yield_rate == pytest.approx(3100 / 110_000)
        assert fcst.qualified_fraction == pytest.approx(2700 / 3100)

    def test_no_history_uses_none_strategy(self):
        """No TTM data, no override, no yfinance → none."""
        from engine.dividend_forecast import Position, forecast_portfolio

        positions = [
            Position(ticker="NEWSTOCK", shares=100, balance=10_000, ttm_dividends=0.0),
        ]
        fcst = forecast_portfolio(positions, total_balance=10_000)
        assert fcst.yield_rate == 0.0
        assert fcst.source_counts["none"] == 1


class TestYTDDividendSplit:
    """Tests for the qualified/ordinary YTD dividend split."""

    def test_backward_compat_property(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot(
            qualified_dividends_ytd=500.0,
            ordinary_dividends_ytd=300.0,
        )
        assert snap.dividends_ytd == 800.0

    def test_zero_split(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot()
        assert snap.dividends_ytd == 0.0
        assert snap.qualified_dividends_ytd == 0.0
        assert snap.ordinary_dividends_ytd == 0.0

    def test_niit_includes_both_dividend_types(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot(
            qualified_dividends_ytd=500.0,
            ordinary_dividends_ytd=300.0,
            ltcg_ytd=1000.0,
            interest_ytd=200.0,
        )
        # total_investment_income = ltcg + stcg + dividends (qual + ord) + interest
        # = 1000 + 0 + 800 + 200 = 2000
        assert snap.total_investment_income == pytest.approx(2000.0)

    def test_scenario_year_dividend_split_fields_and_compat(self):
        """YearResult carries split fields; ytd_dividends is backward-compat aggregate."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(
            tax_year=2026,
            qualified_dividends_ytd=1_000,
            ordinary_dividends_ytd=500,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        assert yr2026.ytd_qualified_dividends == approx(1_000)
        assert yr2026.ytd_ordinary_dividends == approx(500)
        # backward-compat aggregate
        assert yr2026.ytd_dividends == approx(1_500)


class TestScenarioDividendProjection:
    """Tests for brokerage dividend projection in scenario engine."""

    def _rmd_household(self, **kwargs) -> Household:
        """Household at RMD age so excess RMD seeds brokerage in year 1."""
        return Household(
            your_age=75,
            spouse_age=69,
            base_year=2026,
            your_ira=4_000_000,
            spouse_ira=1_000_000,
            growth_rate=0.07,
            **kwargs,
        )

    def test_zero_yield_is_backward_compatible(self):
        """GrowthProfile with yield_rate=0 → identical outputs to no GrowthProfile."""
        hh_default = self._rmd_household()
        hh_explicit = self._rmd_household(
            brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.0),
        )
        r_default = run_scenario(hh_default, ConversionPlan(), "default", end_age=80)
        r_explicit = run_scenario(hh_explicit, ConversionPlan(), "explicit", end_age=80)

        for yr_d, yr_e in zip(r_default.years, r_explicit.years, strict=True):
            assert yr_d.magi == pytest.approx(yr_e.magi, abs=1.0)
            assert yr_d.combined_gross == pytest.approx(yr_e.combined_gross, abs=1.0)
            assert yr_d.brokerage_balance == pytest.approx(yr_e.brokerage_balance, abs=1.0)

    def test_yield_pushes_qualified_to_magi(self):
        """qualified_fraction=1.0 → qualified dividends increment MAGI but not combined_gross."""
        # Use brokerage_growth with yield but all-qualified; run two years so brokerage is seeded.
        hh_no_yield = self._rmd_household(
            brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.0),
        )
        hh_yield = self._rmd_household(
            brokerage_growth=GrowthProfile(
                default_rate=0.07, yield_rate=0.03, qualified_fraction=1.0
            ),
        )
        r_no = run_scenario(hh_no_yield, ConversionPlan(), "no_yield", end_age=80)
        r_yes = run_scenario(hh_yield, ConversionPlan(), "with_yield", end_age=80)

        # Find a year where brokerage has accumulated (age 77, 2 years of excess)
        yr_no = next(yr for yr in r_no.years if yr.your_age == 77)
        yr_yes = next(yr for yr in r_yes.years if yr.your_age == 77)

        # With qualified dividends: MAGI should be higher
        assert yr_yes.magi > yr_no.magi
        # combined_gross should be equal (qualified divs don't stack into ordinary brackets)
        assert yr_yes.combined_gross == pytest.approx(yr_no.combined_gross, abs=1.0)
        # Qualified div field should be nonzero in yield scenario
        assert yr_yes.brokerage_qual_div > 0.0
        assert yr_yes.brokerage_ord_div == pytest.approx(0.0)

    def test_yield_pushes_ordinary_to_combined_gross(self):
        """qualified_fraction=0.0 → ordinary dividends increment both MAGI and combined_gross."""
        hh_no_yield = self._rmd_household(
            brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.0),
        )
        hh_ord = self._rmd_household(
            brokerage_growth=GrowthProfile(
                default_rate=0.07, yield_rate=0.03, qualified_fraction=0.0
            ),
        )
        r_no = run_scenario(hh_no_yield, ConversionPlan(), "no_yield", end_age=80)
        r_ord = run_scenario(hh_ord, ConversionPlan(), "ord_yield", end_age=80)

        yr_no = next(yr for yr in r_no.years if yr.your_age == 77)
        yr_ord = next(yr for yr in r_ord.years if yr.your_age == 77)

        # With ordinary dividends: both MAGI and combined_gross should be higher
        assert yr_ord.magi > yr_no.magi
        assert yr_ord.combined_gross > yr_no.combined_gross
        # Ordinary div field should be nonzero; qualified should be zero
        assert yr_ord.brokerage_ord_div > 0.0
        assert yr_ord.brokerage_qual_div == pytest.approx(0.0)


# ============================================================
#  G3 Characterization: deductions / senior_bonus / taxable_ss
# ============================================================


class TestEngineConstantsCharacterization:
    """Pin deductions/senior_bonus/taxable_ss outputs before G3 constants extraction.

    These tests catch any byte-level drift if the extracted constants
    don't EXACTLY match the previous inline values.

    Expected values derived algebraically from the inline formulas in
    engine/tax.py as of development HEAD 3b5772e (G2 merge):

      deductions: std_ded=32_200, senior_extra=1_650
      senior_bonus_deduction: bonus_per_person=6_000, phaseout_start=150_000,
                              phaseout_rate=0.06 (per $1 of excess, not per person)
      taxable_ss: tier1=32_000, tier2=44_000, max_fraction=0.85;
                  tier1→tier2 formula: 0.5*(provisional-32_000)
                  above tier2: 0.85*(provisional-44_000)+6_000
    """

    # --- deductions() ---

    def test_deductions_below_65_no_bonus(self):
        # Both under 65: no senior extras → exactly std_ded
        # 32_200 + 0 = 32_200
        assert deductions(60, 60) == approx(32_200)

    def test_deductions_one_senior(self):
        # ya=65, sa=60: one senior_extra applies
        # 32_200 + 1_650 = 33_850
        assert deductions(65, 60) == approx(33_850)

    def test_deductions_both_seniors(self):
        # ya=67, sa=66: two senior_extras
        # 32_200 + 1_650 + 1_650 = 35_500
        assert deductions(67, 66) == approx(35_500)

    # --- senior_bonus_deduction() ---

    def test_senior_bonus_neither_senior(self):
        # Both under 65: eligible=0 → 0.0
        assert senior_bonus_deduction(60, 60, magi=100_000) == approx(0.0)

    def test_senior_bonus_under_phaseout(self):
        # Both 65+, MAGI=100_000 < 150_000: full bonus
        # eligible=2, total_bonus=12_000, no reduction → 12_000
        assert senior_bonus_deduction(65, 65, magi=100_000) == approx(12_000)

    def test_senior_bonus_at_phaseout_start(self):
        # MAGI exactly 150_000: magi <= phaseout_start branch → full 12_000
        assert senior_bonus_deduction(65, 65, magi=150_000) == approx(12_000)

    def test_senior_bonus_partial_phaseout(self):
        # MAGI=200_000: reduction = (200_000 - 150_000) * 0.06 = 3_000
        # result = max(12_000 - 3_000, 0) = 9_000
        assert senior_bonus_deduction(65, 65, magi=200_000) == approx(9_000)

    def test_senior_bonus_one_person_partial_phaseout(self):
        # ya=65, sa=60: eligible=1, total_bonus=6_000
        # MAGI=200_000: reduction=(200_000-150_000)*0.06=3_000
        # result = max(6_000 - 3_000, 0) = 3_000
        assert senior_bonus_deduction(65, 60, magi=200_000) == approx(3_000)

    def test_senior_bonus_above_phaseout_cap(self):
        # MAGI=500_000: reduction=(500_000-150_000)*0.06=21_000 > 12_000
        # result = max(12_000 - 21_000, 0) = 0.0
        assert senior_bonus_deduction(65, 65, magi=500_000) == approx(0.0)

    # --- taxable_ss() ---

    def test_taxable_ss_zero_ss(self):
        # combined_ss=0: early-exit guard → 0.0
        assert taxable_ss(0, other_income=100_000) == approx(0.0)

    def test_taxable_ss_under_tier_1(self):
        # provisional = 5_000 + 0.5*20_000 = 15_000 <= 32_000 → 0.0
        assert taxable_ss(combined_ss=20_000, other_income=5_000) == approx(0.0)

    def test_taxable_ss_at_tier_1_boundary(self):
        # provisional = 22_000 + 0.5*20_000 = 32_000 → branch is provisional<=32_000 → 0.0
        assert taxable_ss(combined_ss=20_000, other_income=22_000) == approx(0.0)

    def test_taxable_ss_tier_1_to_2(self):
        # provisional = 26_000 + 0.5*20_000 = 36_000, in [32_000, 44_000]
        # taxable = 0.5 * (36_000 - 32_000) = 2_000
        # cap = 0.85 * 20_000 = 17_000 → result = 2_000
        assert taxable_ss(combined_ss=20_000, other_income=26_000) == approx(2_000)

    def test_taxable_ss_above_tier_2(self):
        # provisional = 40_000 + 0.5*40_000 = 60_000 > 44_000
        # taxable = 0.85*(60_000-44_000) + 6_000 = 13_600 + 6_000 = 19_600
        # cap = 0.85*40_000 = 34_000 → result = 19_600
        assert taxable_ss(combined_ss=40_000, other_income=40_000) == approx(19_600)

    def test_taxable_ss_capped_at_85pct(self):
        # Very high other_income forces the 85% cap
        # combined_ss=10_000, other_income=200_000
        # provisional=205_000 >> 44_000
        # taxable=0.85*(205_000-44_000)+6_000 = 0.85*161_000+6_000 = 136_850+6_000=142_850
        # cap=0.85*10_000=8_500 → result=8_500
        assert taxable_ss(combined_ss=10_000, other_income=200_000) == approx(8_500)


class TestQueryResponseShape:
    """Verify _flatten_query_rows handles both FinExtract response shapes."""

    def test_single_institution_legacy_shape(self):
        from engine.portfolio_sync import _flatten_query_rows

        data = {
            "domain": "brokerage",
            "data_type": "holdings",
            "rows": [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
        }
        assert _flatten_query_rows(data) == [{"symbol": "AAPL"}, {"symbol": "MSFT"}]

    def test_multi_institution_current_shape(self):
        from engine.portfolio_sync import _flatten_query_rows

        data = {
            "domain": "brokerage",
            "data_type": "holdings",
            "institutions": {
                "fidelity": {"rows": [{"symbol": "AAPL"}]},
                "schwab": {"rows": [{"symbol": "MSFT"}, {"symbol": "TXN"}]},
            },
        }
        result = _flatten_query_rows(data)
        # Order across institutions is dict-iteration order — assert as a set / sorted
        assert sorted(r["symbol"] for r in result) == ["AAPL", "MSFT", "TXN"]
        assert len(result) == 3

    def test_empty_institutions(self):
        from engine.portfolio_sync import _flatten_query_rows

        data = {"institutions": {}}
        assert _flatten_query_rows(data) == []

    def test_neither_rows_nor_institutions(self):
        from engine.portfolio_sync import _flatten_query_rows

        # FinExtract returning no data at all should yield [] not raise
        data = {"domain": "brokerage", "data_type": "holdings"}
        assert _flatten_query_rows(data) == []

    def test_institutions_value_not_dict(self):
        from engine.portfolio_sync import _flatten_query_rows

        # Robustness: malformed nested batch should be skipped, not raise
        data = {
            "institutions": {"fidelity": "not-a-dict", "schwab": {"rows": [{"symbol": "MSFT"}]}}
        }
        result = _flatten_query_rows(data)
        assert result == [{"symbol": "MSFT"}]

    def test_institution_batch_missing_rows_key(self):
        from engine.portfolio_sync import _flatten_query_rows

        # If one institution's batch has no 'rows' key, skip silently rather than KeyError
        data = {
            "institutions": {
                "fidelity": {"metadata": "blah"},  # no 'rows' key
                "schwab": {"rows": [{"symbol": "MSFT"}]},
            },
        }
        assert _flatten_query_rows(data) == [{"symbol": "MSFT"}]


class TestAccountTypeOverrides:
    """Verify _classify_account honors user-supplied overrides."""

    def test_override_hit_returns_mapped_type(self):
        from engine.portfolio_sync import _classify_account

        assert _classify_account("U1234567", overrides={"U1234567": "trad_ira"}) == (
            "trad_ira",
            "you",
        )

    def test_override_miss_falls_through_to_substring_scan(self):
        from engine.portfolio_sync import _classify_account

        # Override exists for a different account; the queried name has 'ira' → substring match
        result = _classify_account("Rollover IRA233813501", overrides={"U1234567": "trad_ira"})
        assert result == ("trad_ira", "you")

    def test_empty_overrides_preserves_legacy_behavior(self):
        from engine.portfolio_sync import _classify_account

        assert _classify_account("Rollover IRA233813501") == ("trad_ira", "you")
        assert _classify_account("Individual Brokerage Account") == ("brokerage", "you")

    def test_overrides_supports_multiple_ibkr_accounts(self):
        from engine.portfolio_sync import _classify_account

        overrides = {"U1234567": "trad_ira", "U7654321": "roth_ira", "U9999999": "brokerage"}
        assert _classify_account("U1234567", overrides=overrides) == ("trad_ira", "you")
        assert _classify_account("U7654321", overrides=overrides) == ("roth_ira", "you")
        assert _classify_account("U9999999", overrides=overrides) == ("brokerage", "you")

    def test_override_can_force_brokerage_classification(self):
        from engine.portfolio_sync import _classify_account

        # Even an 'ira'-containing name can be overridden to brokerage if user knows better
        result = _classify_account(
            "Inheritance IRA Account",
            overrides={"Inheritance IRA Account": "brokerage"},
        )
        assert result == ("brokerage", "you")


class TestDividendsRollupFetchAndMap:
    """Verify fetch_dividends_rollup + apply_dividends_rollup end-to-end."""

    # ------------------------------------------------------------------
    # fetch_dividends_rollup tests
    # ------------------------------------------------------------------

    def test_fetch_handles_server_unavailable(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_dividends_rollup

        def _raise(*args, **kwargs):
            raise req.exceptions.ConnectionError("refused")

        monkeypatch.setattr(req, "get", _raise)
        result = fetch_dividends_rollup()
        assert result.server_available is False
        assert result.error is not None

    def test_fetch_parses_rollup_payload(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_dividends_rollup

        payload = {
            "rollup": {
                "by_symbol": {
                    "AAPL": {"by_year": {"2024": {"total": 423.50, "count": 4}}},
                    "FBTC": {"by_year": {"2024": {"total": 0.0, "count": 0}}},
                },
                "window": {"from": "2024-06-01", "to": "2025-12-15", "months_covered_approx": 18.5},
                "freshness": {"is_stale": False, "as_of": "2025-12-20T00:00:00Z"},
                "classification": {},
                "per_institution_counts": {},
            }
        }

        class _FakeResp:
            status_code = 200

            def json(self):
                return payload

        monkeypatch.setattr(req, "get", lambda *a, **kw: _FakeResp())
        result = fetch_dividends_rollup()
        assert result.server_available is True
        assert "AAPL" in result.by_symbol
        assert result.window == {
            "from": "2024-06-01",
            "to": "2025-12-15",
            "months_covered_approx": 18.5,
        }
        assert result.freshness["is_stale"] is False

    # ------------------------------------------------------------------
    # apply_dividends_rollup tests
    # ------------------------------------------------------------------

    def _make_snap(self, symbols: list[str]) -> object:
        """Build a minimal PortfolioSnapshot with one holding per symbol."""
        from engine.portfolio_sync import (
            AccountSummary,
            Holding,
            PortfolioSnapshot,
        )

        holdings = [
            Holding(
                symbol=sym,
                description=sym,
                quantity=10.0,
                market_value=1000.0,
                account_name="Test",
                asset_class="equity",
            )
            for sym in symbols
        ]
        acct = AccountSummary(
            account_type="brokerage",
            owner="you",
            account_name="Test",
            total_value=1000.0 * len(symbols),
            holdings=holdings,
        )
        return PortfolioSnapshot(accounts=[acct], server_available=True)

    def _make_rollup(
        self,
        by_symbol: dict,
        window: dict | None = None,
        is_stale: bool = False,
        server_available: bool = True,
    ) -> object:
        from engine.portfolio_sync import DividendsRollupSnapshot

        return DividendsRollupSnapshot(
            server_available=server_available,
            by_symbol=by_symbol,
            window=window or {"from": "2024-06-01", "to": "2025-12-15"},
            freshness={"is_stale": is_stale},
        )

    @property
    def _all_holdings(self):
        """Flatten all holdings from a snapshot for easy assertion."""

        def _get(snap):
            return [h for acct in snap.accounts for h in acct.holdings]

        return _get

    def test_apply_extracts_total_from_value_objects(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        rollup = self._make_rollup(
            by_symbol={"AAPL": {"by_year": {"2024": {"total": 423.5, "count": 4}}}},
        )
        apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(snap)[0]
        assert holding.dividends_by_year == {"2024": 423.5}

    def test_apply_renames_window_from_to_start_end(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        rollup = self._make_rollup(
            by_symbol={"AAPL": {"by_year": {"2024": {"total": 100.0, "count": 2}}}},
            window={"from": "2024-06-01", "to": "2025-12-15"},
        )
        apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(snap)[0]
        assert holding.dividends_window == {"start": "2024-06-01", "end": "2025-12-15"}
        assert "from" not in holding.dividends_window
        assert "to" not in holding.dividends_window

    def test_apply_propagates_freshness_to_all_holdings(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL", "FBTC"])
        rollup = self._make_rollup(
            by_symbol={
                "AAPL": {"by_year": {"2024": {"total": 423.5, "count": 4}}},
                "FBTC": {"by_year": {"2024": {"total": 0.0, "count": 0}}},
            },
            is_stale=True,
        )
        apply_dividends_rollup(snap, rollup)
        holdings = self._all_holdings(snap)
        assert all(h.dividends_is_stale is True for h in holdings)

    def test_apply_skips_holdings_not_in_rollup(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL", "ZZZZ"])
        rollup = self._make_rollup(
            by_symbol={"AAPL": {"by_year": {"2024": {"total": 100.0, "count": 2}}}},
        )
        apply_dividends_rollup(snap, rollup)
        holdings = {h.symbol: h for h in self._all_holdings(snap)}
        assert holdings["AAPL"].dividends_by_year == {"2024": 100.0}
        assert holdings["ZZZZ"].dividends_by_year is None
        assert holdings["ZZZZ"].dividends_window is None
        assert holdings["ZZZZ"].dividends_is_stale is None

    def test_apply_handles_empty_rollup_gracefully(self):
        from engine.portfolio_sync import DividendsRollupSnapshot, apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        rollup = DividendsRollupSnapshot(server_available=True, by_symbol={})
        result = apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(result)[0]
        assert holding.dividends_by_year is None

    def test_apply_handles_server_unavailable_rollup(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        rollup = self._make_rollup(
            by_symbol={"AAPL": {"by_year": {"2024": {"total": 999.0, "count": 4}}}},
            server_available=False,
        )
        result = apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(result)[0]
        # server_available=False → snapshot returned unchanged
        assert holding.dividends_by_year is None

    def test_apply_symbol_lookup_is_case_insensitive(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        # FinExtract emits lowercase symbol
        rollup = self._make_rollup(
            by_symbol={"aapl": {"by_year": {"2024": {"total": 423.5, "count": 4}}}},
        )
        apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(snap)[0]
        assert holding.dividends_by_year == {"2024": 423.5}
