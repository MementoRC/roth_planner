"""Me↔spouse parity tests — regression guard for the parity audit (June 2026).

When me and spouse inputs are swapped, combined quantities must be identical
modulo legitimate asymmetries:
- NQO grants are me-only by design (TXN grants belong to "you")
- Different SS claim ages (bug A — currently impossible since ss_start_age is single)

Each assertion compares (your + spouse) totals between the original and swapped
household runs — under symmetry, totals must match year-by-year.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.scenario import (
    auto_fill_22,
    run_no_conversion,
)
from models.household import GrowthProfile, Household


class TestMeSpouseParity:
    """Regression guard: swapping me↔spouse must produce identical combined outputs."""

    def _baseline_hh(self) -> Household:
        return Household(
            your_age=61,
            spouse_age=55,
            your_ira=1_700_000,
            spouse_ira=1_300_000,
            your_ss_fra=3800.0,
            spouse_ss_fra=3200.0,
            your_ira_growth=GrowthProfile(default_rate=0.07),
            spouse_ira_growth=GrowthProfile(default_rate=0.07),
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
        )

    def _swap(self, hh: Household) -> Household:
        return replace(
            hh,
            your_age=hh.spouse_age,
            spouse_age=hh.your_age,
            your_ira=hh.spouse_ira,
            spouse_ira=hh.your_ira,
            your_ss_fra=hh.spouse_ss_fra,
            spouse_ss_fra=hh.your_ss_fra,
            your_ira_growth=hh.spouse_ira_growth,
            spouse_ira_growth=hh.your_ira_growth,
            your_aca_enrolled=hh.spouse_aca_enrolled,
            spouse_aca_enrolled=hh.your_aca_enrolled,
        )

    # ------------------------------------------------------------------
    # test 1: combined IRA trajectory under no-conversion
    # ------------------------------------------------------------------

    def test_no_conversion_combined_ira_trajectory(self) -> None:
        """Combined your_ira_begin + spouse_ira_begin must be symmetric year-by-year."""
        hh = self._baseline_hh()
        hh_swapped = self._swap(hh)

        result = run_no_conversion(hh)
        result_swapped = run_no_conversion(hh_swapped)

        # Both runs have the same number of years (end_age=95 relative to your_age).
        # After swapping, your_age changes (55 vs 61), so end years differ.
        # Compare over the shorter run to stay on common ground.
        min_len = min(len(result.years), len(result_swapped.years))
        for i in range(min_len):
            yr = result.years[i]
            yr_s = result_swapped.years[i]
            combined = yr.your_ira_begin + yr.spouse_ira_begin
            combined_swapped = yr_s.your_ira_begin + yr_s.spouse_ira_begin
            assert combined == pytest.approx(combined_swapped, rel=1e-6), (
                f"Year index {i} (year={yr.year}): combined IRA begin "
                f"{combined:.2f} != swapped {combined_swapped:.2f}"
            )

    # ------------------------------------------------------------------
    # test 2: federal_tax symmetric under no-conversion (MFJ combined)
    # ------------------------------------------------------------------

    def test_no_conversion_federal_tax_symmetric(self) -> None:
        """Federal tax is a single MFJ number — must be symmetric after swap."""
        hh = self._baseline_hh()
        hh_swapped = self._swap(hh)

        result = run_no_conversion(hh)
        result_swapped = run_no_conversion(hh_swapped)

        min_len = min(len(result.years), len(result_swapped.years))
        for i in range(min_len):
            yr = result.years[i]
            yr_s = result_swapped.years[i]
            assert yr.federal_tax_amt == pytest.approx(yr_s.federal_tax_amt, rel=1e-6), (
                f"Year index {i} (year={yr.year}): federal_tax "
                f"{yr.federal_tax_amt:.2f} != swapped {yr_s.federal_tax_amt:.2f}"
            )

    # ------------------------------------------------------------------
    # test 3: MAGI symmetric under no-conversion
    # ------------------------------------------------------------------

    def test_no_conversion_magi_symmetric(self) -> None:
        """MAGI must be identical year-by-year after swap (no grants in baseline)."""
        hh = self._baseline_hh()
        hh_swapped = self._swap(hh)

        result = run_no_conversion(hh)
        result_swapped = run_no_conversion(hh_swapped)

        min_len = min(len(result.years), len(result_swapped.years))
        for i in range(min_len):
            yr = result.years[i]
            yr_s = result_swapped.years[i]
            assert yr.magi == pytest.approx(yr_s.magi, rel=1e-6), (
                f"Year index {i} (year={yr.year}): magi "
                f"{yr.magi:.2f} != swapped {yr_s.magi:.2f}"
            )

    # ------------------------------------------------------------------
    # test 4: combined SS symmetric under no-conversion
    # ------------------------------------------------------------------

    def test_no_conversion_combined_ss_symmetric(self) -> None:
        """With single ss_start_age and swapped FRA amounts, combined SS must match."""
        hh = self._baseline_hh()
        hh_swapped = self._swap(hh)

        result = run_no_conversion(hh)
        result_swapped = run_no_conversion(hh_swapped)

        min_len = min(len(result.years), len(result_swapped.years))
        for i in range(min_len):
            yr = result.years[i]
            yr_s = result_swapped.years[i]
            combined = yr.your_ss + yr.spouse_ss
            combined_swapped = yr_s.your_ss + yr_s.spouse_ss
            assert combined == pytest.approx(combined_swapped, rel=1e-6), (
                f"Year index {i} (year={yr.year}): combined SS "
                f"{combined:.2f} != swapped {combined_swapped:.2f}"
            )

    # ------------------------------------------------------------------
    # test 5: combined IRA trajectory (implicitly tests spouse RMD engine path)
    # ------------------------------------------------------------------

    def test_no_conversion_combined_rmd_via_ira_decay(self) -> None:
        """Combined IRA end-of-year must be symmetric; divergence signals RMD engine bug."""
        hh = self._baseline_hh()
        hh_swapped = self._swap(hh)

        result = run_no_conversion(hh)
        result_swapped = run_no_conversion(hh_swapped)

        min_len = min(len(result.years), len(result_swapped.years))
        for i in range(min_len):
            yr = result.years[i]
            yr_s = result_swapped.years[i]
            combined_end = yr.your_ira_end + yr.spouse_ira_end
            combined_end_swapped = yr_s.your_ira_end + yr_s.spouse_ira_end
            assert combined_end == pytest.approx(combined_end_swapped, rel=1e-6), (
                f"Year index {i} (year={yr.year}): combined IRA end "
                f"{combined_end:.2f} != swapped {combined_end_swapped:.2f}"
            )

    # ------------------------------------------------------------------
    # test 6: auto_fill_22 total conversions symmetric
    # ------------------------------------------------------------------

    def test_auto_fill_22_combined_conversions_symmetric(self) -> None:
        """Sum of all conversions over the plan horizon must be symmetric.

        Bug I: auto_fill omits spouse RMD from base_magi. When sa>=75 (i.e.,
        your_age>=81 in hh but your_age>=75 in hh_swapped), room calculations
        diverge. If the bug exists, late-year totals will differ.
        """
        hh = self._baseline_hh()
        hh_swapped = self._swap(hh)

        plan = auto_fill_22(hh)
        plan_swapped = auto_fill_22(hh_swapped)

        all_years = sorted(
            set(plan.your_conversions) | set(plan.spouse_conversions)
            | set(plan_swapped.your_conversions) | set(plan_swapped.spouse_conversions)
        )

        for year in all_years:
            total = plan.your_conversions.get(year, 0.0) + plan.spouse_conversions.get(year, 0.0)
            total_swapped = (
                plan_swapped.your_conversions.get(year, 0.0)
                + plan_swapped.spouse_conversions.get(year, 0.0)
            )
            assert total == pytest.approx(total_swapped, rel=1e-6), (
                f"Year {year}: combined conversions {total:.2f} != swapped {total_swapped:.2f}"
            )

    # ------------------------------------------------------------------
    # test 7: auto_fill_22 per-year conversion symmetric (same as test 6
    #         but iterating from the plan dicts directly — belt-and-suspenders)
    # ------------------------------------------------------------------

    def test_auto_fill_22_per_year_conversion_symmetric(self) -> None:
        """Per-year combined conversions must match under swap."""
        hh = self._baseline_hh()
        hh_swapped = self._swap(hh)

        plan = auto_fill_22(hh)
        plan_swapped = auto_fill_22(hh_swapped)

        # Run through resulting scenario years to confirm plan execution is symmetric
        from engine.scenario import run_scenario

        result = run_scenario(hh, plan)
        result_swapped = run_scenario(hh_swapped, plan_swapped)

        min_len = min(len(result.years), len(result_swapped.years))
        for i in range(min_len):
            yr = result.years[i]
            yr_s = result_swapped.years[i]
            combined = yr.your_conversion + yr.spouse_conversion
            combined_swapped = yr_s.your_conversion + yr_s.spouse_conversion
            assert combined == pytest.approx(combined_swapped, rel=1e-6), (
                f"Year index {i} (year={yr.year}): per-year combined conversion "
                f"{combined:.2f} != swapped {combined_swapped:.2f}"
            )
