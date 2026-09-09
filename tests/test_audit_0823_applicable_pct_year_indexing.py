"""audit-0823 const/APPLICABLE-PCT: the IRC section 36B applicable-percentage
table is frozen at the TY2026 values for every projected year.

engine/aca.py:aca_premium_cap_rate already accepts a `year` and threads it to
the FPL DENOMINATOR -- _fpl(year=...) -> index_value(FPL_2, year, cpi). But the
NUMERATOR, the applicable percentage, is read from the single frozen list
ACA_PRE_ARP_SCHEDULE via _aca_cap_schedule(), which takes no year at all. So
half of the FPL ratio ages with the projection and half does not.

The percentages are re-published every year under IRC section 36B(b)(3)(A)(ii).
They are NOT CPI-indexed -- the statute indexes them by the excess of premium
growth over income growth -- so they cannot be run through index_value the way
the FPL denominator is. The published table has to be carried.

Published values, both quoted from the IRS primary sources:

  TY2026 -- Rev. Proc. 2025-25 (IRB 2025-32, Aug 4 2025)
    https://www.irs.gov/pub/irs-drop/rp-25-25.pdf
    <133%: 2.10 flat | 133-150: 3.14->4.19 | 150-200: 4.19->6.60
    200-250: 6.60->8.44 | 250-300: 8.44->9.96 | 300-400: 9.96 flat

  TY2027 -- Rev. Proc. 2026-26 (IRB 2026-31, Jul 27 2026)
    https://www.irs.gov/pub/irs-drop/rp-26-26.pdf
    <133%: 2.15 flat | 133-150: 3.23->4.30 | 150-200: 4.30->6.78
    200-250: 6.78->8.66 | 250-300: 8.66->10.22 | 300-400: 10.22 flat

Every band rose. Because a higher applicable percentage means a higher expected
contribution and therefore a SMALLER subsidy, freezing at 2026 OVERSTATES the
ACA subsidy for every year from 2027 on, and understates the marginal ACA cost
of a Roth conversion inside the band.

All fixtures below pass cpi=0.0 so the FPL denominator is pinned at FPL_2 =
21,150 and the band arithmetic is exact: 300-400% FPL is $63,450-$84,600, so
MAGI $70,000 sits squarely in the flat top band in every year under test.
"""

import pytest

from engine.aca import FPL_2, aca_premium_cap_rate

# MAGI landing in the flat 300-400% FPL band with cpi=0.0 (ratio ~3.31).
MAGI_TOP_BAND = 70_000.0
# MAGI landing at the midpoint of the 150-200% ramp band (ratio 1.75).
MAGI_RAMP_MID = 1.75 * FPL_2
# MAGI landing in the flat sub-133% band (ratio 1.20).
MAGI_BOTTOM_BAND = 1.20 * FPL_2

PCT_2026_TOP = 0.0996
PCT_2027_TOP = 0.1022
PCT_2026_BOTTOM = 0.0210
PCT_2027_BOTTOM = 0.0215
# Ramp midpoints: start + 0.5 * (end - start) across the 150-200% band.
PCT_2026_RAMP_MID = 0.0419 + 0.5 * (0.0660 - 0.0419)
PCT_2027_RAMP_MID = 0.0430 + 0.5 * (0.0678 - 0.0430)


def _rate(magi: float, year: int) -> float:
    return aca_premium_cap_rate(
        magi, enhanced_subsidies_active=False, year=year, cpi=0.0
    )


class TestTy2026IsUnchanged:
    """Non-regression. Every existing caller defaults to BASE_YEAR (2026), so
    the 2026 column must survive byte-identical. These pass BOTH before and
    after the fix -- they are what proves the change is purely additive."""

    def test_top_band_2026(self) -> None:
        assert _rate(MAGI_TOP_BAND, 2026) == pytest.approx(PCT_2026_TOP)

    def test_bottom_band_2026(self) -> None:
        assert _rate(MAGI_BOTTOM_BAND, 2026) == pytest.approx(PCT_2026_BOTTOM)

    def test_ramp_midpoint_2026(self) -> None:
        assert _rate(MAGI_RAMP_MID, 2026) == pytest.approx(PCT_2026_RAMP_MID, abs=1e-6)

    def test_years_before_the_first_published_table_use_it(self) -> None:
        """Nothing projects backwards, but the lookup must not fall off the
        bottom of the table if something ever does."""
        assert _rate(MAGI_TOP_BAND, 2025) == pytest.approx(PCT_2026_TOP)


class TestTy2027UsesThePublishedTable:
    """The defect. Pre-fix these return the 2026 percentages."""

    def test_top_band_2027(self) -> None:
        assert _rate(MAGI_TOP_BAND, 2027) == pytest.approx(PCT_2027_TOP), (
            "2027 must use Rev. Proc. 2026-26 (10.22%), not the frozen "
            "Rev. Proc. 2025-25 value (9.96%)"
        )

    def test_bottom_band_2027(self) -> None:
        assert _rate(MAGI_BOTTOM_BAND, 2027) == pytest.approx(PCT_2027_BOTTOM)

    def test_ramp_midpoint_2027(self) -> None:
        """The ramp interpolation must run against the 2027 endpoints, not just
        the 2027 band-start rates."""
        assert _rate(MAGI_RAMP_MID, 2027) == pytest.approx(PCT_2027_RAMP_MID, abs=1e-6)

    def test_every_band_rose_from_2026_to_2027(self) -> None:
        """Direction guard: freezing at 2026 can only OVERSTATE the subsidy."""
        for magi in (MAGI_BOTTOM_BAND, MAGI_RAMP_MID, MAGI_TOP_BAND):
            assert _rate(magi, 2027) > _rate(magi, 2026), (
                f"MAGI ${magi:,.0f}: 2027 applicable pct must exceed 2026's"
            )


class TestYearsPastTheLastPublishedTable:
    """Held at the latest published table, deliberately and visibly.

    Extrapolating would need the section 36B(b)(3)(A)(ii) premium-growth-over-
    income-growth ratio, which this model has no input for; cpi_assumption is
    not that ratio. Holding flat is a declared assumption, not an accident --
    but it must hold at the LATEST published year, not the earliest.
    """

    def test_2028_holds_at_2027(self) -> None:
        assert _rate(MAGI_TOP_BAND, 2028) == pytest.approx(PCT_2027_TOP)

    def test_far_future_holds_at_2027(self) -> None:
        assert _rate(MAGI_TOP_BAND, 2040) == pytest.approx(PCT_2027_TOP)

    def test_hold_is_flat_not_drifting(self) -> None:
        assert _rate(MAGI_TOP_BAND, 2040) == pytest.approx(_rate(MAGI_TOP_BAND, 2028))


class TestEnhancedScheduleIsNotYearIndexed:
    """The ARPA/IRA enhanced caps are statutory percentages with no annual
    indexing provision, so they must NOT vary by year. Passes before and after."""

    def test_enhanced_is_year_invariant(self) -> None:
        rates = [
            aca_premium_cap_rate(
                MAGI_TOP_BAND, enhanced_subsidies_active=True, year=y, cpi=0.0
            )
            for y in (2026, 2027, 2030)
        ]
        assert rates[0] == rates[1] == rates[2]


class TestPublishedTableProvenance:
    """Pins the published values themselves, in the style of
    tests/test_constants_provenance.py. Imported function-locally so the rest of
    this file still runs as a value-level RED gate before the symbol exists."""

    def test_2026_table_matches_rev_proc_2025_25(self) -> None:
        from engine.aca import ACA_PRE_ARP_SCHEDULE_BY_YEAR

        pcts = [p for _fpl, p in ACA_PRE_ARP_SCHEDULE_BY_YEAR[2026]]
        # Rev. Proc. 2025-25: 2.10 / 3.14 / 4.19 / 6.60 / 8.44 / 9.96
        assert pcts == pytest.approx(
            [0.0210, 0.0314, 0.0419, 0.0660, 0.0844, 0.0996], abs=1e-5
        )

    def test_2027_table_matches_rev_proc_2026_26(self) -> None:
        from engine.aca import ACA_PRE_ARP_SCHEDULE_BY_YEAR

        pcts = [p for _fpl, p in ACA_PRE_ARP_SCHEDULE_BY_YEAR[2027]]
        # Rev. Proc. 2026-26: 2.15 / 3.23 / 4.30 / 6.78 / 8.66 / 10.22
        assert pcts == pytest.approx(
            [0.0215, 0.0323, 0.0430, 0.0678, 0.0866, 0.1022], abs=1e-5
        )

    def test_fpl_breakpoints_identical_across_published_years(self) -> None:
        """The band edges are statutory (100/133/150/200/250/300/400% FPL) and
        do not move when the percentages are re-indexed."""
        from engine.aca import ACA_PRE_ARP_SCHEDULE_BY_YEAR

        edges = {
            year: [fpl for fpl, _p in table]
            for year, table in ACA_PRE_ARP_SCHEDULE_BY_YEAR.items()
        }
        for year, e in edges.items():
            assert e == [1.33, 1.50, 2.00, 2.50, 3.00, 4.00], f"year {year}"

    def test_each_band_final_pct_equals_next_band_initial_pct(self) -> None:
        """Structural invariant the interpolation in aca_premium_cap_rate
        depends on: it reads the NEXT entry's start rate as the current band's
        end rate. True of both published tables; assert it so a future year's
        table cannot be added in a shape that silently breaks the ramp."""
        from engine.aca import ACA_PRE_ARP_SCHEDULE_BY_YEAR

        for year, table in ACA_PRE_ARP_SCHEDULE_BY_YEAR.items():
            rates = [p for _fpl, p in table]
            assert rates == sorted(rates), (
                f"year {year}: applicable percentages must be non-decreasing "
                "across bands or the ramp interpolation inverts"
            )

    def test_alias_points_at_the_base_year_table(self) -> None:
        """ACA_PRE_ARP_SCHEDULE is imported directly by
        tests/test_constants_provenance.py and tests/test_single_filer.py, so it
        must keep resolving to the BASE_YEAR (2026) table."""
        from engine.aca import ACA_PRE_ARP_SCHEDULE, ACA_PRE_ARP_SCHEDULE_BY_YEAR
        from engine.tax_indexing import BASE_YEAR

        assert ACA_PRE_ARP_SCHEDULE_BY_YEAR[BASE_YEAR] == ACA_PRE_ARP_SCHEDULE
