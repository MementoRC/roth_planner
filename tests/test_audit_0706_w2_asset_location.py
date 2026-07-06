"""Audit 0706 Wave-2 — engine/asset_location.py findings.

asset-location-0: per-owner balance attribution when one owner's RMD exceeds
                  their tracked balance inflates the other owner's end balance.
asset-location-4: existing Roth balances (hh.your_roth / hh.spouse_roth)
                  are silently discarded; Roth projection is understated.
asset-location-5: milestone age lookup returns 0 when starting age exceeds
                  the milestone; should return float('nan') so callers can
                  distinguish "in the past" from "zero assets".
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from models.household import Household

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hh(**kwargs) -> Household:
    return replace(Household(), **kwargs)


# ---------------------------------------------------------------------------
# asset-location-0: ownership-fraction RMD attribution
# ---------------------------------------------------------------------------

class TestOwnershipFractionAttribution:
    """Per-owner balance attribution must preserve ownership fractions.

    The old pool_post/realized_growth formula inflates the surviving owner's
    end balance when the other owner's RMD+conv_share causes the floor to fire.

    The FIXED formula:
        your_ira_end   = (your_begin / prior_total) * combined_after
        spouse_ira_end = (spouse_begin / prior_total) * combined_after
    preserves ownership fractions and always sums to combined_after.
    """

    def test_ownership_fractions_preserved_zero_growth_no_rmd(self):
        """With zero growth and no RMD, each owner's end fraction == begin fraction."""
        from engine.asset_location import project_asset_location

        hh = _make_hh(
            your_age=61,
            your_ira=100.0,
            spouse_ira=900_000.0,
            your_rmd_start_age=99,
            spouse_rmd_start_age=99,
        )
        result = project_asset_location(
            hh,
            {},
            equity_return=0.0,
            bond_return=0.0,
            strategy="proportional",
        )
        yr0 = result.years[0]
        expected_your_frac = 100.0 / 900_100.0
        actual_your_frac = yr0.your_ira_end / (yr0.your_ira_end + yr0.spouse_ira_end)
        assert abs(actual_your_frac - expected_your_frac) < 1e-9, (
            f"Ownership fraction wrong: expected {expected_your_frac:.8f}, "
            f"got {actual_your_frac:.8f}"
        )

    def test_ownership_fractions_preserved_with_growth(self):
        """With uniform growth and no RMD, each owner's balance grows proportionally."""
        from engine.asset_location import project_asset_location

        hh = _make_hh(
            your_age=61,
            your_ira=300_000.0,
            spouse_ira=700_000.0,
            your_rmd_start_age=99,
            spouse_rmd_start_age=99,
        )
        result = project_asset_location(
            hh,
            {},
            equity_pct=0.5,
            equity_return=0.07,
            bond_return=0.07,
            strategy="proportional",
        )
        yr0 = result.years[0]
        assert yr0.your_ira_end == pytest.approx(300_000.0 * 1.07, rel=1e-6)
        assert yr0.spouse_ira_end == pytest.approx(700_000.0 * 1.07, rel=1e-6)

    def test_rmd_reduces_correct_owner_end_balance(self):
        """Primary's RMD drains only primary's balance; spouse end balance is unchanged.

        With zero growth and no conversion:
          growth_factor = pool_before_growth / pool_before_growth = 1.0
          your_ira_end  = (your_begin - your_rmd) * 1.0
          spouse_ira_end = (spouse_begin - 0) * 1.0 = spouse_begin
        """
        from engine.asset_location import project_asset_location
        from engine.ira import calc_rmd

        hh = _make_hh(
            your_age=75,
            spouse_age=69,
            your_ira=500_000.0,
            spouse_ira=500_000.0,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
        )
        result = project_asset_location(
            hh,
            {},
            equity_return=0.0,
            bond_return=0.0,
            strategy="proportional",
        )
        yr0 = result.years[0]

        expected_your_rmd = calc_rmd(500_000.0, 75, 75, first_year_deferred=False)
        # Spouse has no RMD at 69 — their balance is intact
        assert yr0.spouse_ira_end == pytest.approx(500_000.0, abs=1.0), (
            f"Spouse end balance should be unchanged (no RMD at 69), "
            f"got {yr0.spouse_ira_end:.2f}"
        )
        # Primary's balance reduced by their RMD only
        assert yr0.your_ira_end == pytest.approx(500_000.0 - expected_your_rmd, abs=1.0), (
            f"Primary end balance should be begin - rmd = "
            f"{500_000.0 - expected_your_rmd:.2f}, got {yr0.your_ira_end:.2f}"
        )

    def test_sum_invariant_holds_per_year(self):
        """your_ira_end + spouse_ira_end == ira_total_end for every year."""
        from engine.asset_location import project_asset_location

        hh = _make_hh(
            your_age=73,
            spouse_age=67,
            your_ira=400_000.0,
            spouse_ira=600_000.0,
            your_rmd_start_age=73,
            spouse_rmd_start_age=75,
        )
        result = project_asset_location(
            hh,
            {2026: 50_000, 2027: 50_000, 2028: 50_000},
            strategy="proportional",
        )
        for yr in result.years:
            assert yr.your_ira_end + yr.spouse_ira_end == pytest.approx(
                yr.ira_total_end, abs=1.0
            ), (
                f"Sum invariant broken at age {yr.your_age}: "
                f"{yr.your_ira_end:.2f} + {yr.spouse_ira_end:.2f} != "
                f"{yr.ira_total_end:.2f}"
            )

    def test_floor_case_non_negative_and_sum_holds(self):
        """End balances are non-negative and sum to ira_total_end for all years."""
        from engine.asset_location import project_asset_location

        hh = _make_hh(
            your_age=61,
            your_ira=50_000.0,
            spouse_ira=1_000_000.0,
            your_rmd_start_age=99,
            spouse_rmd_start_age=99,
        )
        result = project_asset_location(
            hh,
            dict.fromkeys(range(2026, 2040), 900_000),
            equity_pct=1.0,
            equity_return=0.07,
            bond_return=0.07,
            strategy="proportional",
        )
        for yr in result.years:
            assert yr.your_ira_end >= 0.0, f"your_ira_end negative at age {yr.your_age}"
            assert yr.spouse_ira_end >= 0.0, f"spouse_ira_end negative at age {yr.your_age}"
            assert yr.your_ira_end + yr.spouse_ira_end == pytest.approx(
                yr.ira_total_end, abs=1.0
            )


# ---------------------------------------------------------------------------
# asset-location-4: seed existing Roth balances
# ---------------------------------------------------------------------------

class TestExistingRothSeeded:
    """hh.your_roth + hh.spouse_roth must seed the Roth projection."""

    def test_existing_roth_reflected_in_year0_roth_total(self):
        """With no conversions, roth_total at start of year 0 == your_roth + spouse_roth."""
        from engine.asset_location import project_asset_location

        hh = _make_hh(your_roth=150_000.0, spouse_roth=80_000.0)
        result = project_asset_location(hh, {}, strategy="proportional")

        yr0 = result.years[0]
        assert yr0.roth_total == pytest.approx(230_000.0, abs=1.0), (
            f"Expected roth_total=230_000, got {yr0.roth_total:.0f} — "
            "existing Roth balances appear to have been ignored"
        )

    def test_zero_roth_baseline_unchanged(self):
        """Default hh (your_roth=0, spouse_roth=0) still starts at 0."""
        from engine.asset_location import project_asset_location

        hh = Household()
        assert hh.your_roth == 0.0
        assert hh.spouse_roth == 0.0
        result = project_asset_location(hh, {}, strategy="proportional")
        yr0 = result.years[0]
        assert yr0.roth_total == pytest.approx(0.0, abs=0.01)

    def test_existing_roth_grows_each_year(self):
        """Roth total grows at blended rate each year when no conversions added."""
        from engine.asset_location import project_asset_location

        hh = _make_hh(
            your_roth=100_000.0,
            spouse_roth=0.0,
            your_rmd_start_age=99,
            spouse_rmd_start_age=99,
        )
        result = project_asset_location(
            hh,
            {},
            equity_pct=1.0,
            equity_return=0.07,
            bond_return=0.04,
            strategy="proportional",
        )
        yr0 = result.years[0]
        yr1 = result.years[1]
        assert yr0.roth_total == pytest.approx(100_000.0, abs=1.0)
        assert yr1.roth_total == pytest.approx(107_000.0, abs=1.0)

    def test_roth_end_includes_existing_plus_conv_growth(self):
        """roth_total_end = (existing_roth + conv) * growth."""
        from engine.asset_location import project_asset_location

        existing = 200_000.0
        hh = _make_hh(
            your_roth=existing,
            spouse_roth=0.0,
            your_rmd_start_age=99,
            spouse_rmd_start_age=99,
        )
        conv_amount = 50_000.0
        result = project_asset_location(
            hh,
            {hh.base_year: conv_amount},
            equity_pct=1.0,
            equity_return=0.07,
            bond_return=0.04,
            strategy="proportional",
        )
        yr0 = result.years[0]
        expected = (existing + conv_amount) * 1.07
        assert yr0.roth_total_end == pytest.approx(expected, rel=1e-6), (
            f"Expected roth_total_end≈{expected:.0f}, got {yr0.roth_total_end:.0f}"
        )


# ---------------------------------------------------------------------------
# asset-location-5: past-milestone returns nan not 0
# ---------------------------------------------------------------------------

class TestPastMilestoneNan:
    """When starting age > milestone age, result fields must be nan."""

    def test_ira_at_75_is_nan_when_starting_age_76(self):
        from engine.asset_location import project_asset_location

        hh = _make_hh(your_age=76, your_ira=500_000.0)
        result = project_asset_location(hh, {}, strategy="proportional")

        assert math.isnan(result.ira_at_75), (
            f"Expected ira_at_75=nan when starting_age=76, got {result.ira_at_75}"
        )

    def test_rmd_at_75_is_nan_when_starting_age_76(self):
        from engine.asset_location import project_asset_location

        hh = _make_hh(your_age=76, your_ira=500_000.0)
        result = project_asset_location(hh, {}, strategy="proportional")

        assert math.isnan(result.rmd_at_75), (
            f"Expected rmd_at_75=nan when starting_age=76, got {result.rmd_at_75}"
        )

    def test_ira_growth_at_75_is_nan_when_starting_age_76(self):
        from engine.asset_location import project_asset_location

        hh = _make_hh(your_age=76, your_ira=500_000.0)
        result = project_asset_location(hh, {}, strategy="proportional")

        assert math.isnan(result.ira_growth_at_75), (
            f"Expected ira_growth_at_75=nan when starting_age=76, got {result.ira_growth_at_75}"
        )

    def test_ira_at_85_is_nan_when_starting_age_86(self):
        from engine.asset_location import project_asset_location

        hh = _make_hh(your_age=86, your_ira=500_000.0)
        result = project_asset_location(hh, {}, end_age=95, strategy="proportional")

        assert math.isnan(result.ira_at_85), (
            f"Expected ira_at_85=nan when starting_age=86, got {result.ira_at_85}"
        )

    def test_milestone_present_returns_numeric_not_nan(self):
        """Sanity: when milestone is within projection window, result is numeric."""
        from engine.asset_location import project_asset_location

        hh = _make_hh(your_age=61, your_ira=500_000.0)
        result = project_asset_location(hh, {}, strategy="proportional")

        assert not math.isnan(result.ira_at_75), "ira_at_75 should be numeric for age-61 start"
        assert not math.isnan(result.ira_at_85), "ira_at_85 should be numeric for age-61 start"
        assert not math.isnan(result.rmd_at_75), "rmd_at_75 should be numeric for age-61 start"
        assert not math.isnan(result.rmd_at_85), "rmd_at_85 should be numeric for age-61 start"
        assert not math.isnan(result.ira_growth_at_75), "ira_growth_at_75 should be numeric"
