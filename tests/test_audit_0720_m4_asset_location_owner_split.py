"""Regression test for audit-0720 finding M4.

project_asset_location's your_conv/spouse_conv split must be proportional to
POST-RMD balance, not beginning-of-year balance. Splitting by beginning
balance while flooring a per-owner end balance at 0 breaks the documented
invariant: your_ira_end + spouse_ira_end == ira_total_end.
"""

from __future__ import annotations

from engine.asset_location import project_asset_location
from models.household import Household


class TestM4PerOwnerSplitPreservesPoolInvariant:
    def test_lopsided_balances_large_conversion_preserves_sum(self) -> None:
        """Tiny your_ira + huge spouse_ira + a conversion larger than your_ira
        forces the proportional-by-beginning-balance split to allocate more
        conversion to 'your' sleeve than it can afford, clamping your_ira_end
        to 0 and losing the difference from the pool-sum invariant.
        """
        hh = Household(
            your_age=90,
            spouse_age=73,
            your_ira=500.0,
            spouse_ira=99_500.0,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            base_year=2026,
        )
        result = project_asset_location(
            hh, {2026: 95_000.0}, strategy="proportional", end_age=90
        )
        yr = result.years[0]
        gap = abs((yr.your_ira_end + yr.spouse_ira_end) - yr.ira_total_end)
        assert gap < 0.01, (
            f"your_ira_end={yr.your_ira_end:.2f} + spouse_ira_end={yr.spouse_ira_end:.2f} "
            f"= {yr.your_ira_end + yr.spouse_ira_end:.2f} != ira_total_end="
            f"{yr.ira_total_end:.2f} (gap={gap:.2f})"
        )
