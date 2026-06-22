"""Tests for engine.asset_location — tax-efficient placement."""

import pytest

from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestAssetLocation:
    """Test asset location engine — equity-first vs proportional vs bond-first."""

    def test_equity_first_reduces_ira_growth(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = {2026: 100_000, 2027: 100_000, 2028: 100_000}
        eq = project_asset_location(hh, conv, strategy="equity_first")
        prop = project_asset_location(hh, conv, strategy="proportional")
        # After converting equities, IRA growth rate should be lower
        assert eq.ira_growth_at_75 < prop.ira_growth_at_75

    def test_equity_first_smaller_ira_at_85(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = dict.fromkeys(range(2026, 2040), 100000)
        eq = project_asset_location(hh, conv, strategy="equity_first")
        prop = project_asset_location(hh, conv, strategy="proportional")
        bd = project_asset_location(hh, conv, strategy="bond_first")
        # Equity-first should have smallest IRA (slowest remaining growth)
        assert eq.ira_at_85 < prop.ira_at_85
        assert prop.ira_at_85 < bd.ira_at_85

    def test_equity_first_larger_roth(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = dict.fromkeys(range(2026, 2035), 100000)
        eq = project_asset_location(hh, conv, strategy="equity_first")
        bd = project_asset_location(hh, conv, strategy="bond_first")
        # Equity-first Roth should be larger (equities grow faster tax-free)
        eq_roth_85 = next(y for y in eq.years if y.your_age == 85).roth_total
        bd_roth_85 = next(y for y in bd.years if y.your_age == 85).roth_total
        assert eq_roth_85 > bd_roth_85

    def test_same_total_converted(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = dict.fromkeys(range(2026, 2040), 80000)
        eq = project_asset_location(hh, conv, strategy="equity_first")
        bd = project_asset_location(hh, conv, strategy="bond_first")
        assert eq.total_converted == approx(bd.total_converted)

    def test_no_conversion_same_for_all(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        eq = project_asset_location(hh, {}, strategy="equity_first")
        bd = project_asset_location(hh, {}, strategy="bond_first")
        # With no conversions, IRA trajectory should be identical
        assert eq.ira_at_85 == approx(bd.ira_at_85)

    def test_rmd_smaller_with_equity_first(self):
        from engine.asset_location import project_asset_location

        hh = Household()
        conv = dict.fromkeys(range(2026, 2040), 100000)
        eq = project_asset_location(hh, conv, strategy="equity_first")
        bd = project_asset_location(hh, conv, strategy="bond_first")
        assert eq.rmd_at_85 < bd.rmd_at_85

    def test_conversion_does_not_consume_rmd_dollars(self):
        """When conv >= ira_total - rmd, the RMD must still be fully honoured.

        Build a scenario where the RMD start age is 73 and the requested
        conversion in the first RMD year is intentionally larger than what
        remains after the RMD.  The conversion stored on the year result must
        be capped to ira_total - rmd, not ira_total.
        """
        from dataclasses import replace

        from engine.asset_location import project_asset_location

        # Use a small IRA so RMD is material relative to a large conversion
        hh = replace(Household(), your_ira=500_000.0, your_rmd_start_age=73)
        # age 73 is 2026 + (73 - 61) = 2038 for default your_age=61
        first_rmd_year = 2026 + (73 - hh.your_age)
        # Request a very large conversion in the first RMD year
        conv = {first_rmd_year: 1_000_000.0}
        result = project_asset_location(hh, conv, strategy="proportional")

        yr = next((y for y in result.years if y.year == first_rmd_year), None)
        assert yr is not None, "RMD year not found in projection"
        assert yr.rmd > 0, "Sanity: RMD should be positive in first RMD year"
        # Conversion must leave the full RMD intact
        assert yr.conversion + yr.rmd <= yr.ira_total + 1.0, (
            "Conversion + RMD may not exceed opening IRA balance"
        )
        assert yr.conversion <= yr.ira_total - yr.rmd + 1.0, (
            "Conversion must be capped to post-RMD balance"
        )
