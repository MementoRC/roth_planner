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
    ConversionPlan,
    add_bracket_fill_withdrawals,
    auto_fill_22,
    run_no_conversion,
    run_scenario,
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
            your_ss_start_age=hh.spouse_ss_start_age,
            spouse_ss_start_age=hh.your_ss_start_age,
            your_rmd_start_age=hh.spouse_rmd_start_age,
            spouse_rmd_start_age=hh.your_rmd_start_age,
            your_fra_age=hh.spouse_fra_age,
            spouse_fra_age=hh.your_fra_age,
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
                f"Year index {i} (year={yr.year}): magi {yr.magi:.2f} != swapped {yr_s.magi:.2f}"
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
            set(plan.your_conversions)
            | set(plan.spouse_conversions)
            | set(plan_swapped.your_conversions)
            | set(plan_swapped.spouse_conversions)
        )

        for year in all_years:
            total = plan.your_conversions.get(year, 0.0) + plan.spouse_conversions.get(year, 0.0)
            total_swapped = plan_swapped.your_conversions.get(
                year, 0.0
            ) + plan_swapped.spouse_conversions.get(year, 0.0)
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

    # ------------------------------------------------------------------
    # test 8: asymmetric SS claim ages — combined SS still symmetric under swap
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # test 9: symmetric QCDs both spouses — combined taxable RMD parity
    # ------------------------------------------------------------------

    def test_no_conversion_symmetric_qcds_combined_taxable_rmd(self) -> None:
        """With QCDs applied symmetrically to both spouses, combined taxable RMD stays symmetric."""
        hh = self._baseline_hh()
        hh_sw = self._swap(hh)
        # Each spouse does 50K of QCDs every RMD year
        qcds = dict.fromkeys(range(2040, 2061), 50_000.0)
        plan = ConversionPlan(qcds=qcds, spouse_qcds=qcds)
        r1 = run_scenario(hh, plan, end_age=95)
        r2 = run_scenario(hh_sw, plan, end_age=95)
        min_len = min(len(r1.years), len(r2.years))
        for i in range(min_len):
            y1 = r1.years[i]
            y2 = r2.years[i]
            c1 = y1.taxable_rmd + y1.spouse_taxable_rmd
            c2 = y2.taxable_rmd + y2.spouse_taxable_rmd
            assert c1 == pytest.approx(c2, rel=1e-6), (
                f"combined taxable_rmd differs at year-index {i} (year={y1.year}): "
                f"{c1:.2f} vs {c2:.2f}"
            )

    def test_no_conversion_asymmetric_ss_start_age_symmetric(self) -> None:
        """Asymmetric SS claim ages must still produce symmetric combined SS under swap.

        you=70, spouse=67 → after swap: you=67, spouse=70. The per-person SS
        amounts flip but the combined sum for each year-index must be equal.
        """
        hh = replace(self._baseline_hh(), your_ss_start_age=70, spouse_ss_start_age=67)
        hh_sw = self._swap(hh)

        r1 = run_no_conversion(hh, end_age=95)
        r2 = run_no_conversion(hh_sw, end_age=95)

        min_len = min(len(r1.years), len(r2.years))
        for i in range(min_len):
            y1 = r1.years[i]
            y2 = r2.years[i]
            c1 = y1.your_ss + y1.spouse_ss
            c2 = y2.your_ss + y2.spouse_ss
            assert c1 == pytest.approx(c2, rel=1e-6), (
                f"combined SS differs at year-index {i} (year={y1.year}): {c1:.2f} vs {c2:.2f}"
            )

    # ------------------------------------------------------------------
    # test 11 (Bug B): asymmetric rmd_start_age — combined IRA decay still symmetric
    # ------------------------------------------------------------------

    def test_no_conversion_asymmetric_rmd_start_age_symmetric(self) -> None:
        """Asymmetric RMD start ages must still produce symmetric combined IRA under swap.

        your_rmd_start_age=73 (pre-1960 cohort), spouse_rmd_start_age=75 (post-1960).
        After swap: your=75, spouse=73. Combined IRA begin each year-index must match.
        """
        hh = replace(
            self._baseline_hh(),
            your_rmd_start_age=73,
            spouse_rmd_start_age=75,
        )
        hh_sw = self._swap(hh)

        r1 = run_no_conversion(hh, end_age=95)
        r2 = run_no_conversion(hh_sw, end_age=95)

        min_len = min(len(r1.years), len(r2.years))
        for i in range(min_len):
            y1 = r1.years[i]
            y2 = r2.years[i]
            c1 = y1.your_ira_begin + y1.spouse_ira_begin
            c2 = y2.your_ira_begin + y2.spouse_ira_begin
            assert c1 == pytest.approx(c2, rel=1e-6), (
                f"combined IRA begin differs at year-index {i} (year={y1.year}): "
                f"{c1:.2f} vs {c2:.2f}"
            )

    # ------------------------------------------------------------------
    # test 12 (Bug J): bracket-fill withdrawals debit spouse IRA when yours is exhausted
    # ------------------------------------------------------------------

    def test_bracket_fill_uses_spouse_ira_when_yours_exhausted(self) -> None:
        """add_bracket_fill_withdrawals should draw from spouse IRA when your IRA is empty.

        Use a household where your IRA is small so it's quickly depleted by RMDs,
        while spouse IRA is large. In RMD years after your IRA is near zero,
        the bracket fill should populate spouse_extra_withdrawals.
        """
        hh = Household(
            your_age=74,
            spouse_age=72,
            your_ira=200_000,  # small — depletes quickly under RMD
            spouse_ira=1_500_000,
            your_ss_fra=3_800.0,
            spouse_ss_fra=3_200.0,
            your_ira_growth=GrowthProfile(default_rate=0.07),
            spouse_ira_growth=GrowthProfile(default_rate=0.07),
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
        )
        base_plan = ConversionPlan()
        plan = add_bracket_fill_withdrawals(hh, base_plan, target_bracket=0.22)

        # At least some spouse extra withdrawals should be populated in RMD years
        # (once your small IRA is exhausted, bracket fill must use spouse IRA)
        assert len(plan.spouse_extra_withdrawals) > 0, (
            "Expected spouse_extra_withdrawals to be non-empty when your IRA is small"
        )
        # All spouse extra withdrawals should be positive
        for year, amt in plan.spouse_extra_withdrawals.items():
            assert amt > 0, f"spouse_extra_withdrawals[{year}] = {amt} should be positive"

    # ------------------------------------------------------------------
    # test 13 (E1): per-spouse FRA age — combined SS still symmetric under swap
    # ------------------------------------------------------------------

    def test_asymmetric_fra_ages_combined_ss_symmetric(self) -> None:
        """Asymmetric FRA ages must still produce symmetric combined SS under swap.

        your_fra_age=66 (pre-1960 cohort), spouse_fra_age=67 (1960+ cohort).
        After swap: your=67, spouse=66. Per-person SS amounts differ but
        combined SS for each year-index must be equal.
        """
        hh = replace(
            self._baseline_hh(),
            your_fra_age=66,
            spouse_fra_age=67,
        )
        hh_sw = self._swap(hh)

        r1 = run_no_conversion(hh, end_age=95)
        r2 = run_no_conversion(hh_sw, end_age=95)

        min_len = min(len(r1.years), len(r2.years))
        for i in range(min_len):
            y1 = r1.years[i]
            y2 = r2.years[i]
            c1 = y1.your_ss + y1.spouse_ss
            c2 = y2.your_ss + y2.spouse_ss
            assert c1 == pytest.approx(c2, rel=1e-6), (
                f"combined SS differs at year-index {i} (year={y1.year}): {c1:.2f} vs {c2:.2f}"
            )
