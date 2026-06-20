"""Filing-status correctness for the sweet-spot and ACA/IRMAA view-compute
modules, plus the survivor ACA-enrollee fix (R2-D)."""

from __future__ import annotations

import pytest

from engine.aca_irmaa_compute import compute_cost_curves
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year
from models.household import Household, SurvivorScenario


def _single_hh(**kw: object) -> Household:
    base: dict[str, object] = {
        "filing_status": "Single",
        "spouse_ira": 0,
        "spouse_roth": 0,
        "spouse_age": 0,
        "spouse_ss_fra": 0,
    }
    base.update(kw)
    return Household(**base)  # type: ignore[arg-type]


def _mfj_hh(**kw: object) -> Household:
    base: dict[str, object] = {
        "filing_status": "MFJ",
        "spouse_ira": 0,
        "spouse_roth": 0,
        "spouse_age": 0,
        "spouse_ss_fra": 0,
    }
    base.update(kw)
    return Household(**base)  # type: ignore[arg-type]


class TestSweetSpotSingleFiler:
    def test_single_higher_conv_tax_and_lower_deduction(self) -> None:
        year = 2026
        conv = 150_000.0
        bs = base_income_for_year(_single_hh(), year)
        bm = base_income_for_year(_mfj_hh(), year)
        # Single deduction is smaller than MFJ
        assert bs.total_ded < bm.total_ded
        rs = all_in_at_conversion(_single_hh(), bs, conv, 0.0)
        rm = all_in_at_conversion(_mfj_hh(), bm, conv, 0.0)
        # Single tax is higher on same conversion
        assert rs.conv_tax > rm.conv_tax
        # At zero conversion, Single has less room to the 12% ceiling
        rs0 = all_in_at_conversion(_single_hh(), bs, 0.0, 0.0)
        rm0 = all_in_at_conversion(_mfj_hh(), bm, 0.0, 0.0)
        assert rs0.room_12 < rm0.room_12


class TestAcaIrmaaSingleFiler:
    def test_single_fed_tax_higher_than_mfj(self) -> None:
        magi_points = [120_000.0, 200_000.0]
        cs = compute_cost_curves(magi_points, 120_000.0, 0.0, _single_hh(), year=2026, cpi=0.0)
        cm = compute_cost_curves(magi_points, 120_000.0, 0.0, _mfj_hh(), year=2026, cpi=0.0)
        for fs, fm in zip(cs.fed_tax_vals, cm.fed_tax_vals, strict=True):
            assert fs > fm


class TestSurvivorAcaEnrolleeExclusion:
    def test_deceased_spouse_not_counted_on_aca(self) -> None:
        def mk(spouse_enrolled: bool) -> Household:
            return Household(
                your_age=61,
                spouse_age=60,
                your_aca_enrolled=True,
                spouse_aca_enrolled=spouse_enrolled,
                aca_enhanced_subsidies_active=True,
                advance_aptc_annual=10_000.0,
                survivor=SurvivorScenario(who_dies="spouse", death_year=2026),
            )

        enrolled = run_scenario(mk(True), ConversionPlan(), "enr", end_age=64)
        not_enrolled = run_scenario(mk(False), ConversionPlan(), "not", end_age=64)
        post = [
            (a, b)
            for a, b in zip(enrolled.years, not_enrolled.years, strict=True)
            if a.year >= 2027
        ]
        assert post
        assert any(a.aca_clawback != 0.0 for a, _ in post)
        for a, b in post:
            assert a.aca_clawback == pytest.approx(b.aca_clawback)
            assert a.aca_loss == pytest.approx(b.aca_loss)
