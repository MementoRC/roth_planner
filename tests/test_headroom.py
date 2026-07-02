"""Tests for engine.headroom — bracket/IRMAA room calculation."""

import pytest

from config.defaults import DEFAULTS
from models.grants import StockGrant
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


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
        # Spouse's Medicare start (65) occurs in 1 year from now (2026 + 1 = 2027).
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


class TestHeadroomOptionIncomeSubtract:
    """Tests for YTD-realized NQO spread subtraction from planned option income."""

    def test_planned_greater_than_realized_subtracts(self):
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026)],
            txn_price_now=200.0,
        )
        # planned option income = (200 - 104) * 2000 = 192_000
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=50_000)
        result = compute_headroom(hh, ytd)
        assert result.realized_option_income_ytd == approx(50_000)
        assert result.planned_option_income == approx(192_000 - 50_000)

    def test_with_planned_room_nets_realized_nqo(self):
        """C2/headroom-1: the WITH-PLANNED headroom path must add only the
        still-to-realize option income (planned_option_income), NOT the full opt
        on top of magi_ytd / niit_magi_ytd / total_ordinary_income — all of which
        already contain nqo_exercise_ytd. The pre-fix code added the full opt,
        understating every *_with_planned room by the already-realized amount.
        """
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026)],
            txn_price_now=200.0,
        )
        # opt = (200 - 104) * 2000 = 192_000; $50k of it already exercised this year.
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=50_000)
        result = compute_headroom(hh, ytd)

        # Remaining lever = full opt minus already-realized.
        assert result.planned_option_income == approx(142_000)

        # MAGI (headroom.py:194): planned MAGI = locked MAGI + only the remaining
        # option income (age 61 → no SS → planned_tss == locked_tss == 0).
        assert result.projected_magi_base == approx(
            result.locked_magi + result.planned_option_income
        )

        # NIIT room (headroom.py:195): with-planned room drops by only the remaining
        # option income, not the full opt (which would double-count the realized $50k).
        assert result.room_to_niit_with_planned == approx(
            max(result.room_to_niit - result.planned_option_income, 0.0)
        )

    def test_planned_equal_realized_zero_remaining(self):
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026)],
            txn_price_now=200.0,
        )
        # planned = 192_000; realized = 192_000 → result = 0
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=192_000)
        result = compute_headroom(hh, ytd)
        assert result.planned_option_income == approx(0.0)
        assert result.realized_option_income_ytd == approx(192_000)

    def test_realized_exceeds_planned_floors_at_zero(self):
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026)],
            txn_price_now=200.0,
        )
        # planned = 192_000; realized = 300_000 → floor at 0 (no negative)
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=300_000)
        result = compute_headroom(hh, ytd)
        assert result.planned_option_income == approx(0.0)
        assert result.realized_option_income_ytd == approx(300_000)

    def test_zero_realized_unchanged_planned(self):
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026)],
            txn_price_now=200.0,
        )
        # planned = 192_000; realized = 0 → planned unchanged
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=0)
        result = compute_headroom(hh, ytd)
        assert result.planned_option_income == approx(192_000)
        assert result.realized_option_income_ytd == approx(0.0)

    def test_total_subtract_uses_nqo_exercise_ytd(self):
        """Total realized always comes from nqo_exercise_ytd (not per-grant)."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[
                StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026, grant_id="GR-2019")
            ],
            txn_price_now=200.0,
        )
        # planned option income = (200 - 104) * 2000 = 192_000; realized total = 80_000
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=80_000)
        ytd._option_exercises_by_grant = {"GR-2019": 80_000}  # noqa: SLF001
        result = compute_headroom(hh, ytd, early_exercise=True)
        assert result.realized_option_income_ytd == approx(80_000)
        assert result.planned_option_income == approx(192_000 - 80_000)

    def test_total_subtract_ignores_by_grant_contents(self):
        """by_grant breakdown does not affect headroom math; only nqo_exercise_ytd does."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[
                StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026, grant_id="GR-2019")
            ],
            txn_price_now=200.0,
        )
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=80_000)
        # by_grant has a different id — with total subtract, headroom only sees nqo_exercise_ytd
        ytd._option_exercises_by_grant = {"GR-OTHER": 80_000}  # noqa: SLF001
        result = compute_headroom(hh, ytd, early_exercise=True)
        assert result.realized_option_income_ytd == approx(80_000)
        assert result.planned_option_income == approx(192_000 - 80_000)


# ---------------------------------------------------------------------------
# Helpers used across F8/F18/F26 tests
# ---------------------------------------------------------------------------


def _ss_household(your_ss_monthly: float = 2_000, spouse_ss_monthly: float = 2_000) -> Household:
    """Household where both spouses claim SS at 65 (FRA=67 → 86.67% of FRA benefit).

    your_ss_fra / spouse_ss_fra are in $/month.
    annual benefit each ≈ monthly * 0.8667 * 12 ≈ $20,800/yr at $2K/month.
    Combined ≈ $41,600/yr.
    """
    return Household(
        your_age=65,
        spouse_age=65,
        base_year=2026,
        your_ss_fra=your_ss_monthly,
        spouse_ss_fra=spouse_ss_monthly,
        your_ss_start_age=65,
        spouse_ss_start_age=65,
        your_fra_age=67,
        spouse_fra_age=67,
    )


class TestHeadroomSSMAGIFixes:
    """Tests for F8 (taxable SS in MAGI), F18 (LTCG/qual-divs in SS provisional income),
    and F26 (executed IRA conversions in SS provisional income)."""

    # ------------------------------------------------------------------
    # F8: MAGI uses taxable SS only, not gross SS
    # ------------------------------------------------------------------

    def test_f8_magi_uses_taxable_ss_not_gross(self):
        """F8: locked_magi = magi_ytd + taxable_ss, NOT magi_ytd + combined_ss.

        With $100K wages and ~$41.6K combined SS:
          provisional = 100K + 0.5 * 41.6K = 121.6K > tier2 ($44K MFJ) → 85% cap
          taxable SS ≈ 0.85 * 41.6K ≈ $35.4K (< gross $41.6K)
          locked_magi = 100K + 35.4K = ~$135.4K (NOT 100K + 41.6K = ~$141.6K)
        """
        from engine.headroom import compute_headroom
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss
        from models.ytd_income import YTDSnapshot

        monthly = 2_000.0
        hh = _ss_household(your_ss_monthly=monthly, spouse_ss_monthly=monthly)
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=100_000)
        hr = compute_headroom(hh, ytd)

        # Compute expected values independently
        annual_ss_each = ss_benefit_at_age(monthly, claim_age=65, fra_age=67)
        combined_ss = 2 * annual_ss_each
        # magi_ytd = wages only (no SS in YTD snapshot)
        expected_tss = taxable_ss(combined_ss, 100_000, filing_status="MFJ")
        expected_locked_magi = 100_000 + expected_tss

        assert hr.locked_magi == approx(expected_locked_magi, tol=10)
        # Confirm taxable SS < gross SS (85% cap applies here)
        assert expected_tss < combined_ss
        # Also confirm the old (wrong) value would have been higher
        assert hr.locked_magi < 100_000 + combined_ss

    def test_f8_zero_ss_locked_magi_equals_magi_ytd(self):
        """F8: with zero SS (ages below start age), locked_magi = magi_ytd exactly."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        # Default household: your_ss_start_age=70 > your_age default → no SS
        hh = Household(your_age=55, spouse_age=55, base_year=2026)
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=80_000)
        hr = compute_headroom(hh, ytd)
        assert hr.locked_magi == approx(80_000)

    def test_f8_low_ss_below_tier1_taxable_ss_is_zero(self):
        """F8: when provisional income < MFJ tier1 ($32K), taxable SS = 0.

        Provisional = other_income + 0.5 * SS.
        If wages=$0 and SS=$40K → provisional = 0 + 20K = 20K < $32K → taxable = 0.
        locked_magi = magi_ytd (0) + 0 = 0.
        """
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        # Very small SS so provisional stays below $32K MFJ threshold
        # monthly=$600/person → annual ≈ $600 * 0.8667 * 12 ≈ $6,240/yr each → $12.5K combined
        # provisional = 0 + 0.5 * 12.5K = 6.25K < 32K → taxable SS = 0
        hh = _ss_household(your_ss_monthly=600.0, spouse_ss_monthly=600.0)
        ytd = YTDSnapshot(tax_year=2026)  # zero wages
        hr = compute_headroom(hh, ytd)
        # taxable SS = 0 → locked_magi = magi_ytd = 0
        assert hr.locked_magi == approx(0.0)

    # ------------------------------------------------------------------
    # F18: LTCG and qualified dividends included in SS provisional income
    # ------------------------------------------------------------------

    def test_f18_ltcg_raises_taxable_ss(self):
        """F18: LTCG flows through magi_ytd into provisional income → taxable SS increases.

        Pre-fix: locked_other excluded LTCG (used total_ordinary_income only).
        Post-fix: locked_other = magi_ytd, which includes ltcg_ytd.
        """
        from engine.headroom import compute_headroom
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss
        from models.ytd_income import YTDSnapshot

        monthly = 1_500.0  # modest SS to stay in the middle range
        hh = _ss_household(your_ss_monthly=monthly, spouse_ss_monthly=monthly)

        ytd_no_ltcg = YTDSnapshot(tax_year=2026, wages_ytd=10_000)
        ytd_with_ltcg = YTDSnapshot(tax_year=2026, wages_ytd=10_000, ltcg_ytd=60_000)

        hr_no_ltcg = compute_headroom(hh, ytd_no_ltcg)
        hr_with_ltcg = compute_headroom(hh, ytd_with_ltcg)

        # LTCG adds to magi_ytd (70K vs 10K), so provisional income is higher
        # → taxable SS is higher in the with-LTCG case
        # → locked_magi exceeds the no-LTCG case by MORE than $60K (SS contribution rises too)
        assert hr_with_ltcg.locked_magi > hr_no_ltcg.locked_magi + 60_000

        # Confirm the exact taxable SS matches our own calculation with LTCG included
        annual_ss_each = ss_benefit_at_age(monthly, claim_age=65, fra_age=67)
        combined_ss = 2 * annual_ss_each
        expected_tss = taxable_ss(combined_ss, 70_000, filing_status="MFJ")
        expected_locked_magi = 70_000 + expected_tss
        assert hr_with_ltcg.locked_magi == approx(expected_locked_magi, tol=10)

    def test_f18_qual_divs_raise_taxable_ss(self):
        """F18: qualified dividends flow through magi_ytd (via dividends_ytd) into
        provisional income, raising taxable SS above what ordinary-income-only base gives.
        """
        from engine.headroom import compute_headroom
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss
        from models.ytd_income import YTDSnapshot

        monthly = 1_500.0
        hh = _ss_household(your_ss_monthly=monthly, spouse_ss_monthly=monthly)

        ytd_no_divs = YTDSnapshot(tax_year=2026, wages_ytd=10_000)
        ytd_with_divs = YTDSnapshot(tax_year=2026, wages_ytd=10_000, qualified_dividends_ytd=40_000)

        hr_no_divs = compute_headroom(hh, ytd_no_divs)
        hr_with_divs = compute_headroom(hh, ytd_with_divs)

        # Qualified dividends go into magi_ytd → provisional income rises
        # locked_magi with divs must exceed that without by more than $40K
        assert hr_with_divs.locked_magi > hr_no_divs.locked_magi + 40_000

        annual_ss_each = ss_benefit_at_age(monthly, claim_age=65, fra_age=67)
        combined_ss = 2 * annual_ss_each
        # magi_ytd with divs = wages + qual_divs = 50K (dividends_ytd = ordinary + qualified)
        expected_tss = taxable_ss(combined_ss, 50_000, filing_status="MFJ")
        assert hr_with_divs.locked_magi == approx(50_000 + expected_tss, tol=50)

    # ------------------------------------------------------------------
    # F26: Executed IRA conversions remain in provisional income
    # ------------------------------------------------------------------

    def test_f26_conversions_in_provisional_income(self):
        """F26: ira_conversions_ytd is in magi_ytd and must NOT be subtracted from
        provisional income. Conversions ARE ordinary income under §1.408A-4 Q&A 7.

        Equivalence test: $50K wages vs $20K wages + $30K conversion should produce
        the same locked_magi (both have magi_ytd=$50K → identical provisional income
        and taxable SS).
        """
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        monthly = 1_500.0
        hh = _ss_household(your_ss_monthly=monthly, spouse_ss_monthly=monthly)

        ytd_wages_only = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_wages_plus_conv = YTDSnapshot(
            tax_year=2026, wages_ytd=20_000, ira_conversions_ytd=30_000
        )

        hr_wages = compute_headroom(hh, ytd_wages_only)
        hr_with_conv = compute_headroom(hh, ytd_wages_plus_conv)

        # Same magi_ytd → same provisional income → same taxable SS → same locked_magi
        assert hr_with_conv.locked_magi == approx(hr_wages.locked_magi, tol=1.0)

    def test_f26_conversion_raises_ss_vs_pre_fix_behavior(self):
        """F26: old code subtracted conversions from provisional income base.

        Old: locked_other = total_ordinary_income - ira_conversions_ytd = wages
        New: locked_other = magi_ytd = wages + conversions

        With wages=$10K + $40K conversion and SS present, new code yields
        higher taxable SS (more provisional income) than old code would have.
        """
        from engine.tax import taxable_ss

        combined_ss = 40_000.0  # approximate combined SS

        # Old code's provisional base (excluded conversions)
        old_provisional_base = 10_000  # wages only
        # New code's provisional base (includes conversions)
        new_provisional_base = 50_000  # wages + conversions

        old_tss = taxable_ss(combined_ss, old_provisional_base, filing_status="MFJ")
        new_tss = taxable_ss(combined_ss, new_provisional_base, filing_status="MFJ")

        assert new_tss > old_tss

    # ------------------------------------------------------------------
    # Combined: F8 + F18 + F26 all together
    # ------------------------------------------------------------------

    def test_f8_f18_f26_combined_consistency(self):
        """Combined fix: wages + LTCG + conversion all in magi_ytd; taxable SS (not gross) in MAGI.

        Scenario:
          wages=$30K, LTCG=$50K, IRA conversion=$20K (ages 65, claiming SS at 65).
          monthly SS = $2,000/person (FRA=67 → factor ≈ 0.8667)
          annual SS each ≈ $20,800 → combined ≈ $41,600

          magi_ytd = 30K + 50K + 20K = $100K
          provisional = 100K + 0.5 * 41.6K = 120.8K > tier2 ($44K) → 85% cap
          taxable SS = 0.85 * 41.6K ≈ $35,360
          locked_magi = 100K + 35,360 ≈ $135,360

        Old code would have used:
          locked_other = total_ordinary_income - conversions = (30K + 20K) - 20K = 30K
          provisional = 30K + 0.5 * 41.6K ≈ 50.8K → 85% → taxable SS ≈ $35,360 (similar here)
          BUT locked_magi = magi_ytd + combined_ss = 100K + 41.6K = $141.6K (gross SS)

        New code: locked_magi = magi_ytd + taxable_ss = 100K + 35,360 = $135,360 ✓
        """
        from engine.headroom import compute_headroom
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss
        from models.ytd_income import YTDSnapshot

        monthly = 2_000.0
        hh = _ss_household(your_ss_monthly=monthly, spouse_ss_monthly=monthly)
        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=30_000,
            ltcg_ytd=50_000,
            ira_conversions_ytd=20_000,
        )
        hr = compute_headroom(hh, ytd)

        annual_ss_each = ss_benefit_at_age(monthly, claim_age=65, fra_age=67)
        combined_ss = 2 * annual_ss_each
        magi_ytd = 100_000  # 30K + 50K + 20K
        expected_tss = taxable_ss(combined_ss, magi_ytd, filing_status="MFJ")
        expected_locked_magi = magi_ytd + expected_tss

        assert hr.locked_magi == approx(expected_locked_magi, tol=100)

        # F8 check: locked_magi must be less than magi_ytd + combined_ss (gross)
        assert hr.locked_magi < magi_ytd + combined_ss

        # IRMAA room must be positive (threshold ~$212K indexed, locked_magi ~$135K)
        assert hr.room_to_irmaa_t1 > 0

    def test_irmaa_tier_current_uses_payment_year_indexing(self):
        """compute_headroom indexes irmaa_tier_current to payment year (_year + 2).

        Setup: base_year=2030, cpi_assumption=0.025, ages 55/55 (no SS → locked_magi = magi_ytd).

        MFJ tier-1 base $218k indexed to:
          income year 2030 → 218_000 * (1.025)^4 ≈ $240,631
          payment year 2032 → 218_000 * (1.025)^6 ≈ $252,813

        locked_magi = $246,000 sits between the two thresholds.

        Discriminator: income-year indexing gives tier 1; payment-year gives tier 0.
        The fixed engine must produce tier 0 (payment-year) for irmaa_tier_current.
        """
        from engine.headroom import compute_headroom
        from engine.irmaa import irmaa_tier
        from models.ytd_income import YTDSnapshot

        # Ages 55: no SS (start age defaults to 70) → locked_tss = 0 → locked_magi = magi_ytd
        hh = Household(your_age=55, spouse_age=55, base_year=2030, cpi_assumption=0.025)
        # _year = hh.base_year = 2030; _cpi = hh.cpi_assumption = 0.025
        _year = hh.base_year
        _cpi = hh.cpi_assumption

        magi_target = 246_000
        ytd = YTDSnapshot(tax_year=2030, ltcg_ytd=magi_target)

        hr = compute_headroom(hh, ytd)

        # Sanity: locked_magi really is at the target (zero SS)
        assert abs(hr.locked_magi - magi_target) < 1.0, (
            f"Expected locked_magi ≈ {magi_target}, got {hr.locked_magi}"
        )

        # Discriminator: the two indexing conventions must disagree at this MAGI.
        tier_income_year = irmaa_tier(
            hr.locked_magi, filing_status=hh.filing_status, year=_year, cpi=_cpi
        )
        tier_payment_year = irmaa_tier(
            hr.locked_magi, filing_status=hh.filing_status, year=_year + 2, cpi=_cpi
        )
        assert tier_income_year != tier_payment_year, (
            f"Discriminator failed: both conventions give tier {tier_income_year} at MAGI={hr.locked_magi}. "
            "Choose a different magi_target that sits between the income-year and payment-year thresholds."
        )

        # Primary assertion: headroom uses payment-year indexing
        assert hr.irmaa_tier_current == tier_payment_year, (
            f"Expected payment-year tier {tier_payment_year}, got {hr.irmaa_tier_current}"
        )
        assert hr.irmaa_tier_current != tier_income_year, (
            "irmaa_tier_current must NOT equal income-year tier (that is the old broken behavior)"
        )


class TestHeadroomACACliffMAGI:
    """Regression tests for audit C7 / headroom-2: ACA MAGI uses FULL SS benefit.

    IRC §36B(d)(2)(B)(iii) requires adding back the FULL Social Security benefit
    (taxable + non-taxable) to compute ACA MAGI. Prior code used locked_magi which
    carries only the taxable portion, under-counting ACA MAGI and overstating cliff room.
    """

    def test_c7_aca_cliff_room_uses_full_ss(self):
        """C7 / headroom-2: room_to_aca_cliff reflects FULL combined_ss, not just taxable SS.

        Setup:
          - ACA-enrolled person aged 62 (ACA-age, not yet Medicare)
          - combined_ss chosen so taxable SS < combined_ss (partial taxability)
            → non-taxable SS > 0, which is the condition the bug suppressed
          - magi_ytd low enough that household is below 400% FPL without SS

        Expected post-fix: room uses (magi_ytd + combined_ss) as ACA MAGI, yielding
        LESS cliff room than the pre-fix formula (magi_ytd + taxable_ss).
        """
        from engine.aca import FPL_2, aca_applies
        from engine.headroom import compute_headroom
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss
        from engine.tax_indexing import index_value
        from models.ytd_income import YTDSnapshot

        # ACA-enrolled at 62, spouse 55 (no ACA). Both below SS start age by default
        # (your_ss_start_age defaults to 70), so we set a low start age and FRA benefit
        # to get meaningful SS that is only partially taxable.
        # monthly=$500/person → annual ≈ $500 * 0.8667 * 12 ≈ $5,200/yr each
        # → combined_ss ≈ $10,400/yr (low enough for partial taxability)
        monthly = 500.0
        hh = Household(
            your_age=62,
            spouse_age=55,
            base_year=2026,
            your_aca_enrolled=True,
            your_ss_fra=monthly,
            your_ss_start_age=62,
            your_fra_age=67,
            # spouse not claiming SS
            spouse_ss_fra=0.0,
            spouse_ss_start_age=70,
            spouse_fra_age=67,
        )

        # Confirm ACA applies for this household (guards the ACA cliff block)
        ya = hh.your_age
        assert aca_applies(ya, hh.your_aca_enrolled), "Test setup: ACA should apply at age 62"

        # Low YTD income so household is well below 400% FPL
        magi_ytd = 30_000.0
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=magi_ytd)

        hr = compute_headroom(hh, ytd)

        # Independently compute the same values the engine uses
        _year = hh.base_year
        _cpi = hh.cpi_assumption
        fpl = index_value(FPL_2, _year, _cpi)  # MFJ base (2-person family)
        aca_cliff = 4.0 * fpl

        annual_ss = ss_benefit_at_age(monthly, claim_age=62, fra_age=67)
        combined_ss = annual_ss  # spouse has no SS

        # Confirm partial taxability: taxable SS must be strictly less than combined_ss
        tss = taxable_ss(combined_ss, magi_ytd, filing_status="MFJ")
        assert tss < combined_ss, (
            f"Test setup: expected partial taxability (tss={tss:.0f} < combined_ss={combined_ss:.0f})"
        )

        # Post-fix formula: ACA MAGI = magi_ytd + FULL combined_ss
        expected_room = max(aca_cliff - (magi_ytd + combined_ss), 0.0)
        assert hr.room_to_aca_cliff == approx(expected_room, tol=1.0)

        # Strict inequality: post-fix room must be LESS than pre-fix room
        # (because combined_ss > taxable_ss, so ACA MAGI is higher → less cliff room)
        pre_fix_room = max(aca_cliff - (magi_ytd + tss), 0.0)
        assert hr.room_to_aca_cliff < pre_fix_room, (
            f"Post-fix room ({hr.room_to_aca_cliff:.0f}) must be strictly less than "
            f"pre-fix room ({pre_fix_room:.0f}) when non-taxable SS exists"
        )
