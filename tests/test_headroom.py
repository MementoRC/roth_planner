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
