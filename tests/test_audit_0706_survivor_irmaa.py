"""Regression test: survivor IRMAA zero-bug (audit 2026-07-06).

Bug: when survivor_active and who_dies=="you", the code sets ya_irmaa=0 and
sa_irmaa=sa.  irmaa_for_year uses *only* your_age_income_year for Single
filers (the spouse slot is ignored for non-MFJ).  Result: medicare_your_age =
0+2 = 2 < 65 → on_medicare = 0 → $0 IRMAA every post-death year even when
the surviving spouse is Medicare-eligible with high MAGI.

Fix: map the survivor's age into the primary slot that irmaa_for_year reads.
"""

from __future__ import annotations

from engine.scenario import ConversionPlan, run_scenario
from models.household import Household, SurvivorScenario


class TestSurvivorIrmaaZeroBug:
    """H1 bug: surviving spouse IRMAA is zero when who_dies=='you'."""

    def _make_hh(self, who_dies: str, death_year: int = 2026) -> Household:
        """Household where both spouses are Medicare-eligible at death.

        your_age=63, spouse_age=65 in base_year=2026.
        Spouse is 65+ immediately; you turn 65 in 2028.
        Large IRA balances ensure high MAGI (RMD) in post-death years so
        IRMAA tier 1+ is reached regardless of the year.
        """
        return Household(
            your_age=63,
            spouse_age=65,
            your_ira=2_000_000,
            spouse_ira=2_000_000,
            base_year=2026,
            survivor=SurvivorScenario(who_dies=who_dies, death_year=death_year),
        )

    # ------------------------------------------------------------------
    # Primary regression: who_dies=="you" → surviving spouse gets IRMAA
    # ------------------------------------------------------------------

    def test_survivor_spouse_irmaa_nonzero_post_death(self):
        """Surviving spouse (who_dies='you') must have IRMAA > 0 in post-death years.

        With the bug ya_irmaa=0 → irmaa_for_year returns 0 for Single filer
        because it only checks your_age_income_year for non-MFJ status.
        """
        hh = self._make_hh("you", death_year=2026)
        result = run_scenario(hh, ConversionPlan(), "surv_you_die", end_age=90)

        # Post-death years: 2027 onward (death_year + 1).
        # Spouse is 66+ from 2027, Medicare-eligible, high MAGI from large IRA.
        # IRMAA has a 2-year lookback, so from 2029 onward the IRMAA payment
        # reflects 2027+ income.  Check a year where payment is clearly due.
        # In year 2030 the spouse is 69, income year 2028 magi is large → IRMAA due.
        post_death_years = [yr for yr in result.years if yr.year >= 2030]
        assert post_death_years, "Expected projection years >= 2030"

        irmaa_values = [yr.irmaa_cost for yr in post_death_years]
        assert any(v > 0 for v in irmaa_values), (
            f"Surviving spouse (who_dies='you') should have IRMAA > 0 in post-death "
            f"years 2030+, but all are zero: {irmaa_values[:5]}"
        )

    def test_survivor_spouse_irmaa_in_rmd_years(self):
        """Surviving spouse must have IRMAA > 0 once RMDs produce high MAGI.

        Spouse born ~1961 → rmd_start_age=75 → RMDs begin in 2036 (age 75).
        From 2038 onward (payment year = 2036 income year + 2), IRMAA reflects
        the large RMD MAGI from the 4M rolled-over IRA.  The Single tier-1
        threshold is ~$109K, easily exceeded by RMDs on a 4M IRA.

        Prior to fix: ya_irmaa=0 → on_medicare=0 → $0 even with high MAGI.
        After fix: ya_irmaa=sa (survivor age) → on_medicare=1 → surcharge > 0.
        """
        hh = self._make_hh("you", death_year=2026)
        result = run_scenario(hh, ConversionPlan(), "surv_you_die", end_age=90)

        # From 2038+: income year 2036+ with large RMD MAGI, spouse age 77+.
        rmd_years = [yr for yr in result.years if yr.year >= 2038]
        assert rmd_years, "Expected projection years >= 2038"

        for yr in rmd_years:
            assert yr.irmaa_cost > 0, (
                f"Year {yr.year}: surviving spouse (age {yr.spouse_age}, Single, RMD phase) "
                f"should have IRMAA > 0 with 4M rolled-over IRA, got {yr.irmaa_cost}"
            )

    # ------------------------------------------------------------------
    # Unchanged paths: other survivor and non-survivor configurations
    # ------------------------------------------------------------------

    def test_survivor_you_irmaa_nonzero_post_death(self):
        """When who_dies=='spouse', YOU survive — ya_irmaa=ya path.

        This path was already correct in the old code and must remain correct.
        """
        hh = self._make_hh("spouse", death_year=2026)
        result = run_scenario(hh, ConversionPlan(), "surv_spouse_dies", end_age=90)

        post_death_years = [yr for yr in result.years if yr.year >= 2030]
        irmaa_values = [yr.irmaa_cost for yr in post_death_years]
        assert any(v > 0 for v in irmaa_values), (
            f"Surviving you (who_dies='spouse') should have IRMAA > 0 in post-death "
            f"years 2030+, but all are zero: {irmaa_values[:5]}"
        )

    def test_no_survivor_scenario_unaffected(self):
        """Without a survivor scenario the IRMAA logic is unchanged (else branch)."""
        hh = Household(
            your_age=63,
            spouse_age=65,
            your_ira=2_000_000,
            spouse_ira=2_000_000,
            base_year=2026,
        )
        result = run_scenario(hh, ConversionPlan(), "no_surv", end_age=90)

        post_years = [yr for yr in result.years if yr.year >= 2030]
        irmaa_values = [yr.irmaa_cost for yr in post_years]
        assert any(v > 0 for v in irmaa_values), (
            f"No-survivor household (MFJ, both Medicare-eligible, high MAGI) "
            f"should have IRMAA > 0, got {irmaa_values[:5]}"
        )
