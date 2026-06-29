"""Single-from-the-start (non-survivor) filing-status tests for the scenario engine.

Verify that hh.filing_status == "Single" routes the NON-survivor scenario path
through the single-filer brackets / standard deduction / LTCG & IRMAA thresholds,
while MFJ behavior is unchanged. Survivor-year behavior is covered elsewhere.
"""

from __future__ import annotations

import pytest

from engine.scenario import run_scenario
from engine.scenario_autofill import auto_fill_12
from engine.scenario_compute import compute_bracket_room, compute_federal_tax
from engine.scenario_types import ConversionPlan
from engine.tax import (
    BRACKETS_MFJ,
    BRACKETS_SINGLE,
    federal_tax,
    federal_tax_single,
    room_to_bracket,
)
from engine.tax_indexing import index_value
from models.household import Household


def _single_household(**overrides: object) -> Household:
    """Single-from-the-start household: filing Single, spouse inputs zeroed
    (mirrors views.setup spouse_single_overrides)."""
    base: dict[str, object] = {
        "filing_status": "Single",
        "spouse_ira": 0,
        "spouse_roth": 0,
        "spouse_age": 0,
        "spouse_ss_fra": 0,
    }
    base.update(overrides)
    return Household(**base)  # type: ignore[arg-type]


def _mfj_household() -> Household:
    """MFJ with the same spouse-zeroing, so the only difference is filing_status."""
    return Household(
        filing_status="MFJ", spouse_ira=0, spouse_roth=0, spouse_age=0, spouse_ss_fra=0
    )


def _large_ira_single() -> Household:
    """Single household with a large IRA so the IRA never constrains the fill.

    The 12% bracket ceiling (not IRA size) is the binding constraint every year,
    making it easy to verify Single fills strictly less than MFJ.
    """
    return _single_household(your_ira=3_000_000)


def _large_ira_mfj() -> Household:
    """MFJ counterpart — same large IRA, only filing_status differs."""
    return Household(
        filing_status="MFJ",
        spouse_ira=0,
        spouse_roth=0,
        spouse_age=0,
        spouse_ss_fra=0,
        your_ira=3_000_000,
    )


class TestComputeFederalTaxFilingStatus:
    def test_single_uses_single_brackets(self) -> None:
        ti, cg = 120_000.0, 120_000.0
        fed_single, _, _, _ = compute_federal_tax(ti, cg, 0.0, 0.0, 0.0, "Single", 2026, 1.0)
        fed_mfj, _, _, _ = compute_federal_tax(ti, cg, 0.0, 0.0, 0.0, "MFJ", 2026, 1.0)
        assert fed_single == pytest.approx(federal_tax_single(ti, year=2026, cpi=1.0))
        assert fed_mfj == pytest.approx(federal_tax(ti, year=2026, cpi=1.0))
        assert fed_single > fed_mfj

    def test_mfj_matches_plain_mfj_tax(self) -> None:
        ti, cg = 90_000.0, 90_000.0
        fed_mfj, _, _, _ = compute_federal_tax(ti, cg, 0.0, 0.0, 0.0, "MFJ", 2026, 1.0)
        assert fed_mfj == pytest.approx(federal_tax(ti, year=2026, cpi=1.0))


class TestComputeBracketRoomFilingStatus:
    def test_single_uses_single_ceilings(self) -> None:
        gross, ded = 50_000.0, 30_000.0
        r12_s, r22_s = compute_bracket_room(gross, ded, "Single", 2026, 1.0)
        r12_m, r22_m = compute_bracket_room(gross, ded, "MFJ", 2026, 1.0)
        assert r12_s == pytest.approx(
            room_to_bracket(gross, ded, index_value(BRACKETS_SINGLE[1][0], 2026, 1.0))
        )
        assert r12_m == pytest.approx(
            room_to_bracket(gross, ded, index_value(BRACKETS_MFJ[1][0], 2026, 1.0))
        )
        assert r12_m > r12_s
        assert r22_m > r22_s


class TestRunScenarioSingleNonSurvivor:
    def test_single_lower_deduction_higher_tax_than_mfj(self) -> None:
        plan = ConversionPlan(your_conversions={2026: 200_000.0})
        single = run_scenario(_single_household(), plan, "single", end_age=70)
        mfj = run_scenario(_mfj_household(), plan, "mfj", end_age=70)
        y_s = single.years[0]
        y_m = mfj.years[0]
        assert y_s.total_deductions < y_m.total_deductions
        assert y_s.federal_tax_amt > y_m.federal_tax_amt


class TestAutoFillSingleNonSurvivor:
    def test_auto_fill_12_single_fills_less_than_mfj(self) -> None:
        """With a large IRA (never a binding constraint), the 12% bracket ceiling
        is the sole limiting factor each year. Single ceiling < MFJ ceiling, so
        the total conversions over the projection window are strictly less for Single.
        """
        single_plan = auto_fill_12(_large_ira_single())
        mfj_plan = auto_fill_12(_large_ira_mfj())
        tot_single = sum(single_plan.your_conversions.values()) + sum(
            single_plan.spouse_conversions.values()
        )
        tot_mfj = sum(mfj_plan.your_conversions.values()) + sum(
            mfj_plan.spouse_conversions.values()
        )
        assert tot_single < tot_mfj
