"""Regression test: asset_location.py must honour hh.survivor (spousal rollover).

Audit 0706 — high-severity bug: the per-year loop in project_asset_location() never
consulted hh.survivor, so the deceased spouse's IRA kept growing and emitting phantom
RMDs for every year after death.

Expected behaviour (mirrors scenario.py lines 86-98):
  - In death_year the deceased still takes their final RMD (normal).
  - From death_year+1 the deceased's IRA balance transfers to the survivor
    (IRC §402(c)(9) spousal rollover).
  - After rollover: deceased's RMD == 0 for every subsequent year (balance is 0).
  - Survivor's IRA pool reflects the rolled-in balance (grows on the merged amount).
"""

from __future__ import annotations

import pytest

from engine.asset_location import project_asset_location
from models.household import Household, SurvivorScenario


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hh(
    *,
    who_dies: str = "spouse",
    death_year: int = 2031,
    your_age: int = 61,
    spouse_age: int = 55,
    your_ira: float = 500_000.0,
    spouse_ira: float = 500_000.0,
) -> Household:
    """Minimal Household with a survivor scenario set."""
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        base_year=2026,
        your_ira=your_ira,
        spouse_ira=spouse_ira,
        your_roth=0.0,
        spouse_roth=0.0,
        your_rmd_start_age=75,
        spouse_rmd_start_age=75,
        growth_rate=0.07,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        living_expenses=0.0,
        survivor=SurvivorScenario(who_dies=who_dies, death_year=death_year),
    )


# ---------------------------------------------------------------------------
# Core regression: spouse dies — no phantom RMDs on deceased's IRA after death
# ---------------------------------------------------------------------------

class TestSurvivorSpouseDies:
    """Spouse dies in 2031; projection runs to age 95 (your_age 61 -> 95)."""

    DEATH_YEAR = 2031

    @pytest.fixture
    def result(self):
        hh = _make_hh(who_dies="spouse", death_year=self.DEATH_YEAR)
        return project_asset_location(hh, annual_conversions={}, end_age=95)

    def test_spouse_ira_zero_after_death(self, result):
        """Deceased spouse's IRA end-balance must be 0 from death_year+1 onward."""
        post_death = [y for y in result.years if y.year > self.DEATH_YEAR]
        assert post_death, "No years projected after death_year"
        bad = [y for y in post_death if y.spouse_ira_end > 0]
        assert bad == [], (
            f"Phantom spouse IRA balance detected after death: "
            f"{[(y.year, y.spouse_ira_end) for y in bad]}"
        )

    def test_no_phantom_spouse_rmd_after_death(self, result):
        """Deceased spouse's IRA does not contribute a separate RMD after death.

        After the spousal rollover the spouse_ira_end must be 0 (tested separately).
        Here we verify the combined RMD is driven SOLELY by the survivor's (your)
        consolidated IRA, not inflated by a phantom separate deceased-spouse component.

        Proxy: in 2044 (your_age=79, 13 years after death) the spouse_ira_end==0,
        which means calc_rmd(0, ...) returns 0 for the deceased — no phantom addition.
        We assert spouse_ira_end==0 here as the direct proxy for "no phantom RMD".
        The rollover test already covers the survivor balance growing correctly.
        """
        yr_2044 = next((y for y in result.years if y.year == 2044), None)
        if yr_2044 is None:
            pytest.skip("year 2044 not in projection")
        assert yr_2044.spouse_ira_end == 0.0, (
            f"Deceased spouse has non-zero IRA in 2044: {yr_2044.spouse_ira_end:,.0f} — "
            "phantom balance generates phantom RMD"
        )

    def test_survivor_ira_reflects_rollover(self, result):
        """From death_year+1 your_ira_end must be > pre-death level (rolled-in balance)."""
        rollover_yr = next(
            (y for y in result.years if y.year == self.DEATH_YEAR + 1), None
        )
        pre_death_yr = next(
            (y for y in result.years if y.year == self.DEATH_YEAR), None
        )
        assert rollover_yr is not None and pre_death_yr is not None
        # After rollover your IRA should be materially larger than it was just before
        assert rollover_yr.your_ira_end > pre_death_yr.your_ira_end * 1.5, (
            f"Rollover year your_ira_end={rollover_yr.your_ira_end:,.0f} "
            f"not materially larger than pre-death {pre_death_yr.your_ira_end:,.0f}"
        )


# ---------------------------------------------------------------------------
# You die — same invariants for the reversed who_dies
# ---------------------------------------------------------------------------

class TestSurvivorYouDie:
    """You die in 2030; spouse is the survivor."""

    DEATH_YEAR = 2030

    @pytest.fixture
    def result(self):
        hh = _make_hh(who_dies="you", death_year=self.DEATH_YEAR)
        return project_asset_location(hh, annual_conversions={}, end_age=95)

    def test_your_ira_zero_after_death(self, result):
        """Deceased (you) IRA end-balance must be 0 from death_year+1 onward."""
        post_death = [y for y in result.years if y.year > self.DEATH_YEAR]
        assert post_death, "No years projected after death_year"
        bad = [y for y in post_death if y.your_ira_end > 0]
        assert bad == [], (
            f"Phantom 'your' IRA balance after death: "
            f"{[(y.year, y.your_ira_end) for y in bad]}"
        )

    def test_spouse_ira_reflects_rollover(self, result):
        """Spouse IRA must jump at rollover year when your balance transfers."""
        rollover_yr = next(
            (y for y in result.years if y.year == self.DEATH_YEAR + 1), None
        )
        pre_death_yr = next(
            (y for y in result.years if y.year == self.DEATH_YEAR), None
        )
        assert rollover_yr is not None and pre_death_yr is not None
        assert rollover_yr.spouse_ira_end > pre_death_yr.spouse_ira_end * 1.5, (
            f"Rollover year spouse_ira_end={rollover_yr.spouse_ira_end:,.0f} "
            f"not materially larger than pre-death {pre_death_yr.spouse_ira_end:,.0f}"
        )


# ---------------------------------------------------------------------------
# No survivor -> behaviour unchanged (non-regression)
# ---------------------------------------------------------------------------

class TestNoSurvivor:
    """Without a SurvivorScenario both IRA balances grow throughout."""

    def test_both_iras_present_at_end(self):
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=500_000.0,
            spouse_ira=500_000.0,
            your_roth=0.0,
            spouse_roth=0.0,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.07,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            living_expenses=0.0,
            survivor=None,
        )
        result = project_asset_location(hh, annual_conversions={}, end_age=85)
        last = result.years[-1]
        # Both owners still have IRA balances at end (no RMDs since both < 75 for much
        # of window; some RMDs fire after 75 but don't fully drain)
        assert last.your_ira_end > 0, "your IRA should not be fully drained by 85"
        assert last.spouse_ira_end > 0, "spouse IRA should not be fully drained by 85"
