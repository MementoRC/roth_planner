"""TDD tests for audit-2026-07-13 findings in models/household.py.

Findings covered:
- household-grant-match-1 (high): Household.option_income indexed hh.grants
  by list POSITION (year - base_year), not by grant identity. When
  portfolio-sync compact-skips a fully-exercised / no-strike grant (see
  app.py's `hh.grants = merged_grants`), the list shrinks and every later
  grant shifts one position earlier, causing option_income to read the wrong
  grant's spread or (once the shift runs past the end of the list) silently
  return 0.0.
- growthprofile-bounds-1 (low): GrowthProfile.qualified_fraction/yield_rate
  had no bounds validation; an out-of-range qualified_fraction (e.g. from a
  bad dividend-forecast blend) silently drove ordinary_div_for negative.
"""

from __future__ import annotations

import pytest

from models.grants import StockGrant
from models.household import GrowthProfile, Household


class TestOptionIncomeGrantMatchAfterCompaction:
    """household-grant-match-1 (high): option_income must match grants by
    StockGrant.year, not by raw list position, so a compact-skip merge that
    drops an earlier grant doesn't shift later grants into the wrong year.
    """

    def _three_grant_household(self) -> Household:
        grants = [
            StockGrant(year=2019, strike=104.0, shares=100, expiry_year=2029),
            StockGrant(year=2020, strike=130.0, shares=100, expiry_year=2030),
            StockGrant(year=2021, strike=169.0, shares=100, expiry_year=2031),
        ]
        return Household(grants=grants, base_year=2026, txn_price_now=200.0)

    def test_baseline_three_grants_map_sequentially(self) -> None:
        """Before any compaction: 2026->grant0(2019), 2027->grant1(2020), 2028->grant2(2021)."""
        hh = self._three_grant_household()
        assert hh.option_income(2026) == pytest.approx(hh.grants[0].spread(200.0))
        assert hh.option_income(2027) == pytest.approx(hh.grants[1].spread(200.0))
        assert hh.option_income(2028) == pytest.approx(hh.grants[2].spread(200.0))

    def test_compacted_grant_list_reflows_from_base_year(self) -> None:
        """Simulate portfolio-sync compact-skip: the fully-exercised 2019 grant
        is dropped, leaving merged_grants = [2020-grant, 2021-grant] --
        exactly as app.py's merge does via `hh.grants = merged_grants`.

        Matching by StockGrant.year (recomputed from whichever grants are
        CURRENTLY present, anchored on the oldest survivor) makes the two
        remaining grants exercise in base_year and base_year+1: the oldest
        surviving grant is always exercised first. This also fixes the old
        out-of-bounds artifact -- with only 2 grants left there IS no third
        exercise year, so base_year+2 correctly (not incidentally) returns 0.
        """
        hh = self._three_grant_household()
        remaining = [hh.grants[1], hh.grants[2]]
        hh.grants = remaining  # simulate app.py's `hh.grants = merged_grants`

        assert hh.option_income(2026) == pytest.approx(
            remaining[0].spread(hh.txn_price_now)
        ), "oldest surviving grant (2020) must be exercised first, in base_year"
        assert hh.option_income(2027) == pytest.approx(
            remaining[1].spread(hh.txn_price_now)
        ), "second surviving grant (2021) must be exercised next"
        assert hh.option_income(2028) == 0.0, "only 2 grants remain -- nothing left to exercise"

    def test_order_independent_matching(self) -> None:
        """household-grant-match-1 core regression: the old code indexed
        self.grants[year - base_year] POSITIONALLY, so a merge that doesn't
        preserve chronological order silently picks the wrong grant. Matching
        by StockGrant.year fixes this: the OLDEST grant present is always
        exercised first in base_year, regardless of list order.
        """
        grants_reversed = [
            StockGrant(year=2021, strike=169.0, shares=100, expiry_year=2031),
            StockGrant(year=2020, strike=130.0, shares=100, expiry_year=2030),
            StockGrant(year=2019, strike=104.0, shares=100, expiry_year=2029),
        ]
        hh = Household(grants=grants_reversed, base_year=2026, txn_price_now=200.0)
        oldest = grants_reversed[2]  # 2019 grant, listed LAST
        # Old positional bug would return grants_reversed[0] (the 2021 grant)
        # for year 2026. The fix must return the 2019 grant instead.
        assert hh.option_income(2026) == pytest.approx(oldest.spread(200.0))
        assert hh.option_income(2026) != pytest.approx(grants_reversed[0].spread(200.0))

    def test_fresh_two_grant_household_anchors_to_its_own_oldest_grant(self) -> None:
        """A Household constructed FROM SCRATCH with only 2 grants (no prior
        3-grant history to preserve) anchors sequentially from its own oldest
        grant, starting at base_year -- the sensible behavior when there is
        no compaction event to account for."""
        grants = [
            StockGrant(year=2020, strike=130.0, shares=100, expiry_year=2030),
            StockGrant(year=2021, strike=169.0, shares=100, expiry_year=2031),
        ]
        hh = Household(grants=grants, base_year=2026, txn_price_now=200.0)
        assert hh.option_income(2026) == pytest.approx(grants[0].spread(200.0))
        assert hh.option_income(2027) == pytest.approx(grants[1].spread(200.0))
        assert hh.option_income(2028) == 0.0


class TestGrowthProfileBounds:
    """growthprofile-bounds-1 (low): qualified_fraction/yield_rate must be
    bounded so ordinary_div_for/qualified_div_for never go negative.
    """

    def test_qualified_fraction_above_one_is_clamped(self) -> None:
        gp = GrowthProfile(default_rate=0.06, yield_rate=0.03, qualified_fraction=1.5)
        assert gp.qualified_fraction == 1.0
        assert gp.ordinary_div_for(2026, 1_000_000) == pytest.approx(0.0)
        assert gp.ordinary_div_for(2026, 1_000_000) >= 0.0

    def test_qualified_fraction_below_zero_is_clamped(self) -> None:
        gp = GrowthProfile(default_rate=0.06, yield_rate=0.03, qualified_fraction=-0.5)
        assert gp.qualified_fraction == 0.0
        assert gp.qualified_div_for(2026, 1_000_000) == pytest.approx(0.0)
        assert gp.qualified_div_for(2026, 1_000_000) >= 0.0

    def test_qualified_fraction_in_range_is_unchanged(self) -> None:
        gp = GrowthProfile(default_rate=0.06, yield_rate=0.03, qualified_fraction=0.7)
        assert gp.qualified_fraction == 0.7

    def test_negative_yield_rate_is_clamped_to_zero(self) -> None:
        gp = GrowthProfile(default_rate=0.06, yield_rate=-0.02)
        assert gp.yield_rate == 0.0
