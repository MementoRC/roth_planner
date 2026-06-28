"""Tests for QCD age-70½ engine gate (IRC §408(d)(8)(B)).

Verifies:
1. QCD is NOT capped at rmd when planned QCD > rmd (pre-fix bug).
2. QCD is active at age 71 even when rmd_start_age is 73 (no RMD yet).
3. QCD is zero below QCD_MIN_AGE (age 69).
4. run_scenario: early QCD (ages 71-72, before rmd_start_age=73) shrinks IRA balance.
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_scenario
from engine.scenario_compute import QCD_MIN_AGE, compute_rmds
from models.household import Household

QCD_LIMIT = 111_000.0  # 2026 per-person annual limit


def _call_compute_rmds(
    your_ira: float,
    ya: int,
    your_qcd_planned: float,
    rmd_start_age: int = 73,
    qcd_limit: float = QCD_LIMIT,
) -> tuple[float, float, float, float, float, float]:
    """Thin wrapper: single-filer variant (spouse_ira=0, sa=60 keeps spouse_rmd=0)."""
    return compute_rmds(
        your_ira=your_ira,
        spouse_ira=0.0,
        ya=ya,
        sa=60,
        your_rmd_start_age=rmd_start_age,
        spouse_rmd_start_age=75,
        your_qcd_planned=your_qcd_planned,
        spouse_qcd_planned=0.0,
        qcd_limit=qcd_limit,
    )


class TestQcdAge70HalfEngine:
    """Behavioral tests for the QCD age-70½ engine fix."""

    # ------------------------------------------------------------------
    # 1. QCD exceeds rmd: qcd == min(planned, limit), taxable_rmd == 0
    # ------------------------------------------------------------------

    def test_qcd_not_capped_at_rmd_when_planned_exceeds_rmd(self):
        """At RMD age, planned QCD > RMD should NOT be clipped to the RMD amount.

        Pre-fix: min(planned, rmd, limit) silently capped QCD at the RMD,
        hiding the excess charitable intent and incorrectly computing IRA drain.
        Post-fix: qcd == min(planned, limit), taxable_rmd capped only for income.
        """
        your_ira = 2_000_000.0
        ya = 75  # at rmd_start_age=75
        # planned QCD is 80K but RMD for 2M at 75 ≈ 2M/22.9 ≈ 87K; 80K < rmd
        # Use a large IRA so rmd > planned — plan something > rmd to expose the cap.
        # With 4M IRA and rmd_start_age=75: rmd ≈ 4M/22.9 ≈ 174K
        # Plan 200K QCD (> rmd, < limit=111K)... limit is 111K.
        # Plan 90K QCD (< limit, < rmd@4M).  Actually we want planned > rmd.
        # With 2M IRA at 75: rmd ≈ 87K. Plan 100K QCD (> rmd, < limit).
        planned_qcd = 100_000.0
        your_rmd, qcd, taxable_rmd, *_ = _call_compute_rmds(
            your_ira=your_ira,
            ya=ya,
            your_qcd_planned=planned_qcd,
            rmd_start_age=75,
        )
        # RMD should be positive (in RMD phase)
        assert your_rmd > 0.0, f"Expected positive RMD at 75; got {your_rmd}"
        # planned_qcd (100K) > your_rmd (~87K) > 0; limit=111K
        # Post-fix: qcd = min(100K, 111K) = 100K (NOT clipped at rmd)
        assert qcd == pytest.approx(min(planned_qcd, QCD_LIMIT), abs=0.01), (
            f"QCD should be min(planned, limit)={min(planned_qcd, QCD_LIMIT):,.0f}; got {qcd:,.0f}"
        )
        # taxable_rmd = max(rmd - min(qcd, rmd), 0) = max(rmd - rmd, 0) = 0
        assert taxable_rmd == pytest.approx(0.0, abs=0.01), (
            f"taxable_rmd should be 0 when qcd >= rmd; got {taxable_rmd:,.0f}"
        )

    # ------------------------------------------------------------------
    # 2. QCD active at age 71 (before rmd_start_age=73, rmd==0)
    # ------------------------------------------------------------------

    def test_qcd_active_before_rmd_start_age(self):
        """Age 71 with rmd_start_age=73 → no RMD yet, but QCD gate is open.

        The IRA still sees a QCD distribution; qcd > 0 and taxable_rmd == 0.
        """
        ya = 71
        rmd_start_age = 73
        planned_qcd = 50_000.0

        your_rmd, qcd, taxable_rmd, *_ = _call_compute_rmds(
            your_ira=1_500_000.0,
            ya=ya,
            your_qcd_planned=planned_qcd,
            rmd_start_age=rmd_start_age,
        )
        # No RMD at 71 with rmd_start_age=73
        assert your_rmd == pytest.approx(0.0, abs=0.01), (
            f"Expected zero RMD at age 71 (rmd_start=73); got {your_rmd}"
        )
        # But QCD gate is open (71 >= QCD_MIN_AGE=71)
        assert qcd == pytest.approx(min(planned_qcd, QCD_LIMIT), abs=0.01), (
            f"QCD should be active at age 71; got {qcd:,.0f}"
        )
        # taxable_rmd = max(0 - min(qcd, 0), 0) = 0
        assert taxable_rmd == pytest.approx(0.0, abs=0.01)

    # ------------------------------------------------------------------
    # 3. QCD zero below QCD_MIN_AGE (age 69)
    # ------------------------------------------------------------------

    def test_qcd_zero_below_min_age(self):
        """Age 69 (< QCD_MIN_AGE=71) → qcd must be 0 regardless of planned amount."""
        ya = 69
        assert ya < QCD_MIN_AGE  # sanity

        your_rmd, qcd, taxable_rmd, *_ = _call_compute_rmds(
            your_ira=1_000_000.0,
            ya=ya,
            your_qcd_planned=50_000.0,
            rmd_start_age=73,
        )
        assert qcd == pytest.approx(0.0, abs=0.01), (
            f"QCD must be 0 below QCD_MIN_AGE={QCD_MIN_AGE}; got {qcd:,.0f}"
        )

    # ------------------------------------------------------------------
    # 3b. QCD zero AT age 70 — not yet 70½ for the whole year (D1)
    # ------------------------------------------------------------------

    def test_qcd_zero_at_age_70_not_yet_70half(self):
        """Age 70 (< QCD_MIN_AGE=71) → qcd must be 0 (D1 regression).

        The engine carries whole-year ages only and cannot represent the
        mid-year 70½ attainment date, so eligibility opens the first whole
        year the taxpayer is past 70½ for its entirety — age 71. A taxpayer in
        the calendar year they turn 70 has not necessarily attained 70½ (Jul–Dec
        births never do that year), so no exclusion is granted. Pre-fix (gate at
        70) wrongly granted a full-year QCD here.
        """
        your_rmd, qcd, taxable_rmd, *_ = _call_compute_rmds(
            your_ira=1_000_000.0,
            ya=70,
            your_qcd_planned=50_000.0,
            rmd_start_age=73,
        )
        assert qcd == pytest.approx(0.0, abs=0.01), (
            f"QCD must be 0 at age 70 (< QCD_MIN_AGE={QCD_MIN_AGE}); got {qcd:,.0f}"
        )

    # ------------------------------------------------------------------
    # 4. run_scenario: early QCD (ages 71-72) shrinks IRA vs no-QCD baseline
    # ------------------------------------------------------------------

    def test_early_qcd_shrinks_ira_before_rmd_start_age(self):
        """QCD active at 71-72 (pre-RMD) pulls money from IRA → lower end balance.

        Household: your_age=70, rmd_start_age=73 so RMDs begin in year 3.
        Age 70 (2026) is gated out (not yet 70½ for the whole year, D1), so only
        ages 71-72 fire. IRA should still be smaller than the no-QCD run.
        """
        hh = Household(
            your_age=70,
            spouse_age=60,
            base_year=2026,
            your_ira=2_000_000.0,
            spouse_ira=0.0,
            growth_rate=0.07,
            your_rmd_start_age=73,
            spouse_rmd_start_age=75,
            grants=[],
        )

        # QCD active years 2027-2028 (ages 71-72, before rmd_start_age=73).
        # Age 70 (2026) is gated out — not yet 70½ for the whole year (D1).
        early_qcd_years = {2027: 50_000.0, 2028: 50_000.0}

        plan_no_qcd = ConversionPlan()
        plan_with_qcd = ConversionPlan(qcds=early_qcd_years)

        result_no = run_scenario(hh, plan_no_qcd, "no_qcd", end_age=78)
        result_qcd = run_scenario(hh, plan_with_qcd, "early_qcd", end_age=78)

        # Find age-73 year (first RMD year) — IRA should be smaller with prior QCDs
        yr73_no = next(yr for yr in result_no.years if yr.your_age == 73)
        yr73_qcd = next(yr for yr in result_qcd.years if yr.your_age == 73)

        assert yr73_qcd.your_ira_end < yr73_no.your_ira_end, (
            f"IRA at age 73 should be smaller with early QCDs; "
            f"no_qcd={yr73_no.your_ira_end:,.0f} vs with_qcd={yr73_qcd.your_ira_end:,.0f}"
        )
        # Difference should be roughly 2 × 50K grown for 0-1 years at 7%
        expected_min_reduction = 2 * 50_000 * 0.9  # conservative (ignoring growth)
        actual_reduction = yr73_no.your_ira_end - yr73_qcd.your_ira_end
        assert actual_reduction >= expected_min_reduction, (
            f"IRA reduction ({actual_reduction:,.0f}) smaller than expected "
            f"minimum ({expected_min_reduction:,.0f})"
        )


class TestQcdCapInflationIndexed:
    """D2: the QCD annual cap is inflation-indexed across the projection
    (SECURE 2.0 §307), not frozen at the 2026 $111K limit."""

    def test_qcd_cap_indexed_in_out_year(self):
        """A planned QCD above the 2026 $111K cap but below the indexed out-year
        cap is fully excluded — proving scenario.py indexes hh.qcd_limit to the
        projection year instead of passing the flat 2026 value.

        Pre-fix (flat 111K cap) would clip the 130K QCD to 111K.
        """
        hh = Household(
            your_age=65,
            spouse_age=60,
            base_year=2026,
            your_ira=3_000_000.0,
            spouse_ira=0.0,
            growth_rate=0.05,
            your_rmd_start_age=73,
            spouse_rmd_start_age=75,
            cpi_assumption=0.025,
            grants=[],
        )
        # Age 79 in 2040 (QCD gate open). 2040 indexed cap = 111K × 1.025^14
        # ≈ 156.8K, so a 130K planned QCD is NOT clipped.
        plan = ConversionPlan(qcds={2040: 130_000.0})
        result = run_scenario(hh, plan, "indexed_qcd_cap", end_age=80)
        yr40 = next(yr for yr in result.years if yr.your_age == 79)
        assert yr40.qcd == pytest.approx(130_000.0, abs=1.0), (
            f"QCD cap should be inflation-indexed: expected 130K not clipped, "
            f"got {yr40.qcd:,.0f}"
        )
