"""Test suite — validates engine against known verified numbers from spreadsheets."""

import pytest

from engine.aca import aca_applies, aca_subsidy
from engine.ira import calc_rmd, project_ira, rmd_divisor, ss_benefit_at_age, ss_with_cola
from engine.irmaa import irmaa_next_threshold, irmaa_surcharge
from engine.niit import niit, niit_from_conversion
from engine.scenario import add_bracket_fill_withdrawals, auto_fill_12, auto_fill_22, auto_fill_irmaa_safe, run_no_conversion, run_scenario
from engine.tax import (
    deductions,
    federal_tax,
    marginal_rate,
    room_to_12,
    room_to_22,
    taxable_ss,
)
from models.household import Household


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
        assert hh.grants[0].spread(212) == approx((212 - 104.41) * 650)
        assert hh.grants[1].spread(212) == approx((212 - 130.52) * 763)
        assert hh.grants[2].spread(212) == approx((212 - 169.23) * 450)

    def test_total_spread(self):
        hh = Household()
        total = sum(g.spread(212) for g in hh.grants)
        assert total == approx(151_349, tol=10)

    def test_option_income_by_year(self):
        hh = Household()
        assert hh.option_income(2026, True) == approx(hh.grants[0].spread(212))
        assert hh.option_income(2027, True) == approx(hh.grants[1].spread(212))
        assert hh.option_income(2028, True) == approx(hh.grants[2].spread(212))
        assert hh.option_income(2029, True) == 0


class TestIRMAA:
    def test_below_threshold(self):
        assert irmaa_surcharge(200_000) == 0

    def test_above_tier1(self):
        assert irmaa_surcharge(220_000) > 0

    def test_room_to_next(self):
        assert irmaa_next_threshold(200_000) == approx(18_000)


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

    def test_conversion_increases_niit(self):
        # Base MAGI $200K (below threshold), $100K conversion pushes to $300K
        incremental = niit_from_conversion(200_000, 100_000, 30_000)
        assert incremental == approx(30_000 * 0.038)


class TestACA:
    def test_applies_pre_medicare(self):
        assert aca_applies(61) is True
        assert aca_applies(64) is True
        assert aca_applies(65) is False

    def test_low_income_subsidy(self):
        assert aca_subsidy(30_000) > 15_000

    def test_high_income_subsidy(self):
        aca_subsidy(300_000)  # just verify no error


class TestHouseholdProperties:
    def test_age_gap(self):
        hh = Household()
        assert hh.age_gap == 6

    def test_conv_window(self):
        hh = Household()
        assert hh.your_conv_window == 14

    def test_ss_at_70(self):
        hh = Household()
        assert hh.your_ss_at_70() == approx(56_544)
        assert hh.spouse_ss_at_70() == approx(56_544)


class TestScenarios:
    def test_no_conversion_ira_at_75(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        assert yr75.your_ira_begin == approx(4_383_508, tol=500)

    def test_no_conversion_rmd_at_75(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        assert yr75.your_rmd == approx(4_383_508 / 24.6, tol=100)

    def test_no_conversion_ss_at_75(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        ss75 = 56_544 * 1.025**5
        assert yr75.your_ss == approx(ss75, tol=100)

    def test_no_conversion_spouse_ss_starts_at_70(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        yr76 = next(yr for yr in result.years if yr.your_age == 76)
        assert yr75.spouse_ss == 0
        assert yr76.spouse_ss > 0

    def test_12pct_fill_reduces_ira(self):
        hh = Household()
        plan = auto_fill_12(hh)
        result = run_scenario(hh, plan, "Fill 12%", end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        assert yr75.your_ira_begin < 4_000_000

    def test_22pct_fill_more_aggressive(self):
        hh = Household()
        plan_12 = auto_fill_12(hh)
        plan_22 = auto_fill_22(hh)
        total_12 = sum(plan_12.your_conversions.values()) + sum(plan_12.spouse_conversions.values())
        total_22 = sum(plan_22.your_conversions.values()) + sum(plan_22.spouse_conversions.values())
        assert total_22 > total_12

    def test_22pct_fill_reduces_ira_more(self):
        hh = Household()
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
        hh = Household()
        base = auto_fill_12(hh)
        plan_bf = add_bracket_fill_withdrawals(hh, base, target_bracket=0.22)
        r12 = run_scenario(hh, base, "12%", end_age=95)
        r_bf = run_scenario(hh, plan_bf, "BF", end_age=95)
        yr90_12 = next(yr for yr in r12.years if yr.your_age == 90)
        yr90_bf = next(yr for yr in r_bf.years if yr.your_age == 90)
        assert yr90_bf.your_ira_begin < yr90_12.your_ira_begin

    def test_bracket_fill_has_extra_withdrawals(self):
        hh = Household()
        base = auto_fill_12(hh)
        plan_bf = add_bracket_fill_withdrawals(hh, base, target_bracket=0.22)
        assert len(plan_bf.extra_withdrawals) > 0
        # Extra withdrawals should only be post-RMD (age 75+)
        for year in plan_bf.extra_withdrawals:
            assert hh.your_age_in(year) >= 75


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
        assert base["ya"] == 61
        assert base["combined_ss"] == 0  # SS starts at 70

    def test_base_income_has_options(self):
        from views.sweet_spot import _base_income_for_year

        hh = Household()
        base = _base_income_for_year(hh, 2026)
        assert base["opt"] == approx(hh.grants[0].spread(212))

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

        hh = Household()
        base = _base_income_for_year(hh, 2029)  # age 64, no options
        # Find conversion just below and above IRMAA tier 1
        below = max(218_000 - base["base_magi"] - 1_000, 0)
        above = 218_000 - base["base_magi"] + 1_000
        if below > 0 and above > 0:
            r_below = _all_in_at_conversion(hh, base, below, 0)
            r_above = _all_in_at_conversion(hh, base, above, 0)
            assert r_above["irmaa_delta"] > r_below["irmaa_delta"]
