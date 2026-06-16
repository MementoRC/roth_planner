"""Tests for engine.scenario — multi-year run, auto-fill, sweet spot, YTD wiring."""

import pytest

from config.defaults import DEFAULTS
from engine.ira import (
    project_ira,
)
from engine.scenario import (
    ConversionPlan,
    add_bracket_fill_withdrawals,
    auto_fill_12,
    auto_fill_22,
    auto_fill_irmaa_safe,
    run_no_conversion,
    run_scenario,
)
from models.grants import StockGrant
from models.household import GrowthProfile, Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


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
            (2036, 0, 134650),
            (2037, 0, 24489),
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


class TestSweetSpot:
    """Test the sweet spot finder computation helpers."""

    @pytest.fixture(autouse=True)
    def _require_plotly(self):
        pytest.importorskip("plotly")
        pytest.importorskip("streamlit")

    def test_base_income_no_ss_before_70(self):
        from engine.sweet_spot_compute import base_income_for_year

        hh = Household()
        base = base_income_for_year(hh, 2026)
        assert base.ya == DEFAULTS["your_age"]
        assert base.combined_ss == 0  # SS starts at 70

    def test_base_income_has_options(self):
        from engine.sweet_spot_compute import base_income_for_year

        hh = Household()
        base = base_income_for_year(hh, 2026)
        assert base.opt == approx(hh.grants[0].spread(hh.txn_price_now))

    def test_all_in_zero_conversion(self):
        from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year

        hh = Household()
        base = base_income_for_year(hh, 2026)
        result = all_in_at_conversion(hh, base, 0, 0)
        assert result.all_in == 0
        assert result.conv_tax == 0

    def test_all_in_increases_with_conversion(self):
        from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year

        hh = Household()
        base = base_income_for_year(hh, 2026)
        r50k = all_in_at_conversion(hh, base, 50_000, 0)
        r100k = all_in_at_conversion(hh, base, 100_000, 0)
        assert r100k.all_in > r50k.all_in
        assert r50k.conv_tax > 0

    def test_irmaa_triggers_at_threshold(self):
        from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year

        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        base = base_income_for_year(hh, 2029)  # age 64, no options
        # Find conversion just below and above IRMAA tier 1
        below = max(218_000 - base.base_magi - 1_000, 0)
        above = 218_000 - base.base_magi + 1_000
        if below > 0 and above > 0:
            r_below = all_in_at_conversion(hh, base, below, 0)
            r_above = all_in_at_conversion(hh, base, above, 0)
            assert r_above.irmaa_delta > r_below.irmaa_delta


# ============================================================
#  Tax Return Sync (TurboTax via FinExtract)
# ============================================================

# ============================================================
#  YTD Income Tracker & Headroom
# ============================================================


class TestAutoFillCoreOrdinaryDividendsYTD:
    """Regression tests: _auto_fill_core must include ordinary_dividends_ytd in fixed_gross.

    Prior to the fix (math audit 2026-06-12 Priority 3), _auto_fill_core added only
    wages_ytd and stcg_ytd from the YTD snapshot, omitting ordinary_dividends_ytd
    (and nec_income_ytd, ira_conversions_ytd, ira_distributions_ytd). This caused
    bracket room to be overstated by the omitted ordinary income amount.
    """

    def _base_hh(self) -> Household:
        return Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=1_700_000,
            spouse_ira=1_700_000,
        )

    def test_ordinary_dividends_reduce_room_base_year(self):
        """ordinary_dividends_ytd must reduce base-year bracket room and conversion amount."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_div = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_div = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000,
            ordinary_dividends_ytd=10_000,
        )

        plan_no_div = auto_fill_12(hh, ytd=ytd_no_div)
        plan_with_div = auto_fill_12(hh, ytd=ytd_with_div)

        base_conv = plan_no_div.your_conversions.get(2026, 0.0)
        div_conv = plan_with_div.your_conversions.get(2026, 0.0)

        # ordinary_dividends_ytd consumes bracket room → fewer conversions in base year
        assert div_conv < base_conv, (
            f"Expected ordinary_dividends_ytd to reduce base-year conversion, "
            f"got no_div={base_conv:.0f} vs with_div={div_conv:.0f}"
        )
        # Difference should match the dividend amount (ordinary income fills bracket space)
        assert base_conv - div_conv == approx(10_000, tol=200)

    def test_nec_income_reduces_room_base_year(self):
        """nec_income_ytd (1099-NEC) must also reduce base-year bracket room."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_nec = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_nec = YTDSnapshot(tax_year=2026, wages_ytd=50_000, nec_income_ytd=8_000)

        plan_no_nec = auto_fill_12(hh, ytd=ytd_no_nec)
        plan_with_nec = auto_fill_12(hh, ytd=ytd_with_nec)

        base_conv = plan_no_nec.your_conversions.get(2026, 0.0)
        nec_conv = plan_with_nec.your_conversions.get(2026, 0.0)

        assert nec_conv < base_conv
        assert base_conv - nec_conv == approx(8_000, tol=200)

    def test_ira_conversions_done_reduce_room_base_year(self):
        """ira_conversions_ytd already done must reduce remaining planned room."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_done = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_done = YTDSnapshot(tax_year=2026, wages_ytd=50_000, ira_conversions_ytd=15_000)

        plan_no_done = auto_fill_12(hh, ytd=ytd_no_done)
        plan_done = auto_fill_12(hh, ytd=ytd_done)

        base_conv = plan_no_done.your_conversions.get(2026, 0.0)
        done_conv = plan_done.your_conversions.get(2026, 0.0)

        assert done_conv < base_conv
        assert base_conv - done_conv == approx(15_000, tol=200)

    def test_future_years_unaffected(self):
        """YTD snapshot only applies to base year; future years must be identical."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000,
            ordinary_dividends_ytd=10_000,
        )

        plan_no_ytd = auto_fill_12(hh)
        plan_with_ytd = auto_fill_12(hh, ytd=ytd)

        # All years after 2026 must be identical
        future_years_no = {y: v for y, v in plan_no_ytd.your_conversions.items() if y > 2026}
        future_years_with = {y: v for y, v in plan_with_ytd.your_conversions.items() if y > 2026}
        assert future_years_no == pytest.approx(future_years_with, abs=1.0)

    def test_total_subtract_grant_id_empty_uses_total(self):
        """Total subtract applies even when StockGrant.grant_id is empty (legacy fixture)."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026, grant_id="")],
            txn_price_now=200.0,
        )
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=80_000)
        ytd._option_exercises_by_grant = {"GR-2019": 80_000}  # noqa: SLF001
        result = compute_headroom(hh, ytd, early_exercise=True)
        # Total subtract: realized = ytd.nqo_exercise_ytd regardless of grant_id
        assert result.realized_option_income_ytd == approx(80_000)
        assert result.planned_option_income == approx(192_000 - 80_000)

    def test_magi_ytd_includes_tax_exempt_interest(self):
        """Tax-exempt (muni) interest must appear in IRMAA MAGI even though it is federally exempt."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=80_000, tax_exempt_interest_ytd=5_000)
        # MAGI = wages + tax_exempt_interest
        assert ytd.magi_ytd == approx(85_000)

    def test_tax_exempt_interest_not_in_total_ordinary_income(self):
        """Tax-exempt interest is federally exempt — it must NOT stack into ordinary brackets."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=80_000, tax_exempt_interest_ytd=5_000)
        # ordinary income = wages only; muni interest is excluded
        assert ytd.total_ordinary_income == approx(80_000)

    def test_interest_ytd_reduces_room_base_year(self):
        """Regression C-4: interest_ytd must reduce base-year bracket room and conversion amount.

        Prior to the fix, _auto_fill_core omitted interest_ytd from both other_fixed
        (provisional income for SS taxability) and fixed_gross (bracket math), causing
        conversion room to be overstated by the full interest amount.
        """
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_int = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_int = YTDSnapshot(tax_year=2026, wages_ytd=50_000, interest_ytd=12_000)

        plan_no_int = auto_fill_22(hh, ytd=ytd_no_int)
        plan_with_int = auto_fill_22(hh, ytd=ytd_with_int)

        base_conv = plan_no_int.your_conversions.get(2026, 0.0)
        int_conv = plan_with_int.your_conversions.get(2026, 0.0)

        # interest_ytd is fully taxable ordinary income → consumes bracket room → fewer conversions
        assert int_conv < base_conv, (
            f"Expected interest_ytd=12_000 to reduce base-year conversion, "
            f"got no_int={base_conv:.0f} vs with_int={int_conv:.0f}"
        )
        # Difference should be approximately the interest amount
        assert base_conv - int_conv == approx(12_000, tol=300)


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

    def test_run_scenario_includes_ytd_conversions_in_base_magi(self):
        """ira_conversions_ytd must appear in base-year MAGI even though it
        reduces the remaining planned conversion amount."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        conversions_done = 35_000
        ytd = YTDSnapshot(tax_year=2026, ira_conversions_ytd=conversions_done)
        # Plan more than what's already done so yr.your_conversion > 0
        plan = ConversionPlan(your_conversions={2026: 100_000})
        result_with = run_scenario(hh, plan, "with_conv", end_age=65, ytd=ytd)
        result_without = run_scenario(hh, plan, "without_conv", end_age=65, ytd=None)

        yr_with = result_with.years[0]
        yr_without = result_without.years[0]

        # Invariant: both scenarios plan the same $100K total conversion; ira_conversions_ytd
        # merely shifts income from yr.your_conversion (planned remaining) to magi_ytd (already
        # done). The SUM must be equal — the absolute value depends on default Household
        # option_income which varies with tax-year defaults (e.g. TXN NQO grants in 2026).
        assert yr_with.magi == approx(yr_without.magi)

    def test_run_scenario_includes_nec_in_base_magi(self):
        """nec_income_ytd (1099-NEC) must appear in base-year MAGI."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        nec = 28_000
        ytd_with = YTDSnapshot(tax_year=2026, nec_income_ytd=nec)
        ytd_none = YTDSnapshot(tax_year=2026)
        plan = ConversionPlan()

        yr_with = run_scenario(hh, plan, "nec", end_age=65, ytd=ytd_with).years[0]
        yr_none = run_scenario(hh, plan, "no_nec", end_age=65, ytd=ytd_none).years[0]

        assert yr_with.magi - yr_none.magi == approx(nec)

    def test_run_scenario_includes_distributions_in_base_magi(self):
        """ira_distributions_ytd (non-conversion IRA withdrawals) must appear
        in base-year MAGI."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        distrib = 42_000
        ytd_with = YTDSnapshot(tax_year=2026, ira_distributions_ytd=distrib)
        ytd_none = YTDSnapshot(tax_year=2026)
        plan = ConversionPlan()

        yr_with = run_scenario(hh, plan, "dist", end_age=65, ytd=ytd_with).years[0]
        yr_none = run_scenario(hh, plan, "no_dist", end_age=65, ytd=ytd_none).years[0]

        assert yr_with.magi - yr_none.magi == approx(distrib)

    def test_run_scenario_matches_canonical_magi_ytd(self):
        """Base-year MAGI must equal canonical YTDSnapshot.magi_ytd plus the
        projected income components (remaining planned conversion, option
        income, SS, RMD) — verifying parity with _auto_fill_core."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=60_000,
            nec_income_ytd=10_000,
            ira_conversions_ytd=25_000,
            ira_distributions_ytd=15_000,
            ltcg_ytd=50_000,
            stcg_ytd=5_000,
            qualified_dividends_ytd=3_000,
            ordinary_dividends_ytd=2_000,
            interest_ytd=1_000,
        )
        planned_conversion = 80_000
        plan = ConversionPlan(your_conversions={2026: planned_conversion})
        result = run_scenario(hh, plan, "canonical", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # Projected components not in magi_ytd.
        # D-1: uses taxable_ss_amt (not full combined_ss) — per §1395r(i)(4).
        # C-7: option_income contribution is net of nqo_exercise_ytd (no NQO in this ytd → zero).
        # E-3: includes realized_gains (brokerage_growth * brok_turnover).
        projected_components = (
            yr2026.option_income  # no nqo_exercise_ytd in this ytd, so no dedup delta
            + yr2026.your_conversion  # remaining after subtracting ira_conversions_ytd
            + yr2026.spouse_conversion
            + yr2026.taxable_rmd
            + yr2026.spouse_taxable_rmd
            + yr2026.extra_withdrawal
            + yr2026.spouse_extra_withdrawal
            + yr2026.taxable_ss_amt  # D-1: was combined_ss; zero here (age 61, no SS)
            + yr2026.your_inherited_distribution
            + yr2026.spouse_inherited_distribution
            + yr2026.brokerage_qual_div
            + yr2026.brokerage_ord_div
            + yr2026.brokerage_growth * hh.brok_turnover  # E-3: realized_gains
        )
        expected_magi = projected_components + ytd.magi_ytd
        assert yr2026.magi == approx(expected_magi)

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

    def test_ytd_ltcg_bracket_walk_zero_percent_band(self):
        """YTD LTCG fully inside the 0% band must produce zero LTCG tax.

        Regression for audit A-5/D-5: flat-rate hh.ltcg_rate was applied,
        yielding $6,000 instead of $0 for a household with taxable_ordinary
        well below the MFJ 0%-band ceiling (~$96,700 for 2026).
        """
        from models.ytd_income import YTDSnapshot

        # Wages $30K → std deduction $30,000 (MFJ both <65) → taxable ~$0,
        # well below LTCG_THRESHOLDS_MFJ[0] (~$96,700). $40K LTCG stays in 0% band.
        hh = Household(your_age=61, spouse_age=55, base_year=2026)
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=30_000, ltcg_ytd=40_000)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "ltcg_bracket", end_age=62, ytd=ytd)
        yr2026 = result.years[0]

        # taxable_income (ordinary) should be well below ~$98,900 threshold
        assert yr2026.taxable_income < 98_900
        # All $40K LTCG sits in the 0% band — no LTCG tax owed
        assert yr2026.ytd_ltcg_tax == approx(0.0)


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


class TestSpouseRMDBrokerageAccumulation:
    """Regression: available_income must include spouse RMD and spouse extra_withdrawal.

    Bug (audit C-2): lines 561-562 of engine/scenario.py computed after_tax_rmd and
    available_income using only the "your" side — omitting yr.spouse_taxable_rmd and
    yr.spouse_extra_withdrawal.  When both spouses are in RMD, the spouse contribution
    can exceed $60K/yr, causing brokerage accumulation to be understated by $500K+
    over a 10-year window.
    """

    def _rmd_household(self, your_ira: float, spouse_ira: float) -> Household:
        """Both spouses already 75 (in RMD), no conversions, modest SS."""
        from dataclasses import replace

        return replace(
            Household(grants=[]),
            your_age=75,
            spouse_age=75,
            your_ira=your_ira,
            spouse_ira=spouse_ira,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            living_expenses=60_000.0,
        )

    def test_spouse_rmd_increases_brokerage_balance(self):
        """With spouse IRA active, year-10 brokerage must exceed the no-spouse baseline.

        Setup: both spouses 75 with $1.5M trad IRAs each (~$60K RMD/yr each at 75,
        divisor ≈25).  No conversions.  Living expenses $60K.  With fix, both RMDs
        flow into available_income; excess accumulates in brokerage.
        """
        plan = ConversionPlan()

        # Baseline: your IRA only (spouse IRA zeroed out → spouse_taxable_rmd ≈ 0)
        hh_yours_only = self._rmd_household(your_ira=1_500_000.0, spouse_ira=0.0)
        result_yours = run_scenario(hh_yours_only, plan, end_age=85)

        # With spouse: both IRAs $1.5M → spouse_taxable_rmd ≈ $60K extra each year
        hh_both = self._rmd_household(your_ira=1_500_000.0, spouse_ira=1_500_000.0)
        result_both = run_scenario(hh_both, plan, end_age=85)

        brok_yours = result_yours.years[-1].brokerage_balance
        brok_both = result_both.years[-1].brokerage_balance

        # The spouse RMD (~$60K/yr after-tax) compounded over 10 years at a
        # brokerage rate ≈7% produces well over $800K extra.  A conservative
        # floor of $500K guards against this regression without being brittle.
        assert brok_both > brok_yours + 500_000, (
            f"Expected brokerage with spouse RMD to exceed baseline by >$500K; "
            f"got brok_both={brok_both:,.0f}, brok_yours={brok_yours:,.0f}, "
            f"delta={brok_both - brok_yours:,.0f}"
        )

    def test_spouse_rmd_zero_equals_baseline(self):
        """When spouse IRA is zero, available_income must match the pre-fix behaviour.

        Ensures the fix is additive and does not corrupt the single-earner path.
        """
        plan = ConversionPlan()
        hh = self._rmd_household(your_ira=1_500_000.0, spouse_ira=0.0)
        result = run_scenario(hh, plan, end_age=85)

        for yr in result.years:
            if yr.your_age >= 75:
                assert yr.spouse_taxable_rmd == pytest.approx(0.0), (
                    f"year {yr.year}: spouse_taxable_rmd should be 0 when spouse IRA=0"
                )
