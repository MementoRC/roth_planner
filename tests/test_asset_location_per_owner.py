"""Behavioral tests for per-owner IRA balance tracking in project_asset_location.

These tests guard against the proportional-split bug where one spouse's RMD
incorrectly drained the other spouse's IRA balance.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.asset_location import project_asset_location
from engine.ira import calc_rmd
from models.household import Household

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _age_gapped_hh() -> Household:
    """Primary age 75 (RMD-age), spouse age 69 (below RMD age). Equal IRAs."""
    return replace(
        Household(),
        your_age=75,
        spouse_age=69,
        your_ira=1_000_000.0,
        spouse_ira=1_000_000.0,
        your_rmd_start_age=75,
        spouse_rmd_start_age=75,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPerOwnerIRATracking:
    """Per-owner IRA balance bug fix: RMDs must only drain the owning spouse."""

    def test_per_owner_sum_equals_pool(self):
        """your_ira_end + spouse_ira_end must equal ira_total_end every year."""
        hh = _age_gapped_hh()
        result = project_asset_location(hh, {}, strategy="proportional")
        for yr in result.years:
            assert yr.your_ira_end + yr.spouse_ira_end == pytest.approx(
                yr.ira_total_end, rel=1e-9
            ), (
                f"Age {yr.your_age}: per-owner sum "
                f"{yr.your_ira_end + yr.spouse_ira_end:.2f} != "
                f"pool {yr.ira_total_end:.2f}"
            )

    def test_non_rmd_spouse_not_drained_by_your_rmd(self):
        """During the window only YOU take RMDs, spouse/you ratio must strictly increase.

        Under the old proportional code the ratio was constant (both were drained
        by ownership fraction).  Under the fix, only your balance is drained by
        RMDs so the spouse's share grows relative to yours.
        """
        hh = _age_gapped_hh()
        result = project_asset_location(hh, {}, strategy="proportional")

        # Window: years where only primary is RMD-age (your_age 75-80, spouse 69-74)
        window = [yr for yr in result.years if 75 <= yr.your_age <= 80]
        assert len(window) >= 2, "Need at least 2 years in the window"

        ratios = [yr.spouse_ira_end / yr.your_ira_end for yr in window if yr.your_ira_end > 0]
        for i in range(1, len(ratios)):
            assert ratios[i] > ratios[i - 1], (
                f"Spouse/you ratio must strictly increase year over year while only "
                f"you take RMDs; ratio[{i}]={ratios[i]:.6f} <= ratio[{i - 1}]={ratios[i - 1]:.6f}"
            )

    def test_symmetric_owners_regression(self):
        """Identical ages and IRAs → your_ira_end == spouse_ira_end every year."""
        hh = replace(
            Household(),
            your_age=75,
            spouse_age=75,
            your_ira=1_000_000.0,
            spouse_ira=1_000_000.0,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
        )
        result = project_asset_location(hh, {}, strategy="proportional")
        for yr in result.years:
            assert yr.your_ira_end == pytest.approx(yr.spouse_ira_end, rel=1e-9), (
                f"Age {yr.your_age}: your_ira_end={yr.your_ira_end:.2f} != "
                f"spouse_ira_end={yr.spouse_ira_end:.2f}"
            )

    def test_total_rmd_reflects_only_rmd_age_owner_early(self):
        """In the first year where only primary is RMD-age, yr.rmd equals primary-only RMD.

        Divisor at age 75 = 24.6 (IRS Uniform Lifetime Table).
        primary_rmd = 1_000_000 / 24.6 ≈ 40_650.41
        spouse contributes 0 (age 69 < 75).
        """
        hh = _age_gapped_hh()
        result = project_asset_location(hh, {}, strategy="proportional")
        yr0 = result.years[0]
        assert yr0.your_age == 75
        expected_rmd = calc_rmd(
            1_000_000.0,
            75,
            hh.your_rmd_start_age,
            first_year_deferred=hh.your_defer_first_rmd,
            prior_year_balance=0.0,
        )
        assert yr0.rmd == pytest.approx(expected_rmd, abs=1.0), (
            f"Expected rmd≈{expected_rmd:.2f} (primary only), got {yr0.rmd:.2f}"
        )
        assert expected_rmd > 0, "Sanity: primary should owe RMD at 75"
