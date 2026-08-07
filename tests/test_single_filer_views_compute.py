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


class TestSingleFilerIrmaaNoPhantoSpouse:
    """Regression for 42 U.S.C. §1395r(i): Single filer has one Medicare beneficiary.

    compute_cost_curves previously summed both your_age_in(year) and
    spouse_age_in(year) for on_medicare with no filing-status gate.  For a
    Single household with a default-age phantom spouse that eventually crosses
    65, on_medicare became 2 and doubled the IRMAA surcharge.
    """

    def test_single_filer_irmaa_not_doubled_when_phantom_spouse_over_65(self) -> None:
        """Single filer at age 70 with phantom spouse age 70 must get 1-person IRMAA."""
        from engine.irmaa import irmaa_surcharge

        # Build a Single household where your_age_in(year) >= 65 AND
        # spouse_age (phantom, default 0 but we force it high) >= 65,
        # so the pre-fix bug would produce on_medicare=2.
        hh_single = Household(
            filing_status="Single",
            your_age=70,
            spouse_age=70,  # phantom spouse also over 65 — triggers the bug
            spouse_ira=0,
            spouse_roth=0,
            spouse_ss_fra=0,
        )
        year = 2026
        magi_above_tier1 = 250_000.0  # above IRMAA Tier 1 threshold
        magi_points = [magi_above_tier1]

        cc = compute_cost_curves(magi_points, magi_above_tier1, 0.0, hh_single, year=year, cpi=0.0)

        # Ground truth: irmaa_surcharge with exactly 1 beneficiary.
        # compute_cost_curves applies the 2-year IRMAA lookback (_irmaa_year = year+2),
        # so reference calculations must use the same payment year (audit A1).
        irmaa_year = year + 2
        expected_single = irmaa_surcharge(
            magi_above_tier1,
            num_people=1,
            base_part_b=hh_single.medicare_part_b_base_monthly * 12,
            filing_status="Single",
            year=irmaa_year,
            cpi=0.0,
        )
        double_amount = irmaa_surcharge(
            magi_above_tier1,
            num_people=2,
            base_part_b=hh_single.medicare_part_b_base_monthly * 12,
            filing_status="Single",
            year=irmaa_year,
            cpi=0.0,
        )
        # Verify the bug would have produced a different (doubled) value
        assert double_amount != pytest.approx(expected_single), (
            "Test setup failure: 1-person and 2-person IRMAA happen to be equal"
        )
        # Assert fix: curve must match 1-person amount
        assert cc.irmaa_vals[0] == pytest.approx(expected_single, rel=1e-9), (
            f"Single filer IRMAA is {cc.irmaa_vals[0]:.0f}; expected single-beneficiary "
            f"{expected_single:.0f}. Got 2-person amount {double_amount:.0f} — "
            "phantom spouse is being counted (on_medicare=2 bug)."
        )

    def test_single_filer_base_irmaa_is_not_doubled(self) -> None:
        """base_irmaa (hoisted outside loop) must also use 1-person count for Single."""
        from engine.irmaa import irmaa_surcharge

        hh_single = Household(
            filing_status="Single",
            your_age=70,
            spouse_age=70,
            spouse_ira=0,
            spouse_roth=0,
            spouse_ss_fra=0,
        )
        year = 2026
        base_magi = 250_000.0
        cc = compute_cost_curves([base_magi], base_magi, 0.0, hh_single, year=year, cpi=0.0)

        # compute_cost_curves uses _irmaa_year = year+2; match that payment year (audit A1).
        expected_base = irmaa_surcharge(
            base_magi,
            num_people=1,
            base_part_b=hh_single.medicare_part_b_base_monthly * 12,
            filing_status="Single",
            year=year + 2,
            cpi=0.0,
        )
        assert cc.base_irmaa == pytest.approx(expected_base, rel=1e-9), (
            f"base_irmaa={cc.base_irmaa:.0f} != single-beneficiary {expected_base:.0f}"
        )

    def test_mfj_both_over_65_still_gets_two_person_irmaa(self) -> None:
        """MFJ filer with both spouses over 65 must still count both (no regression)."""
        from engine.irmaa import irmaa_surcharge

        hh_mfj = Household(
            filing_status="MFJ",
            your_age=70,
            spouse_age=70,
        )
        year = 2026
        magi = 250_000.0
        cc = compute_cost_curves([magi], magi, 0.0, hh_mfj, year=year, cpi=0.0)

        # compute_cost_curves uses _irmaa_year = year+2; match that payment year (audit A1).
        expected_two = irmaa_surcharge(
            magi,
            num_people=2,
            base_part_b=hh_mfj.medicare_part_b_base_monthly * 12,
            filing_status="MFJ",
            year=year + 2,
            cpi=0.0,
        )
        assert cc.irmaa_vals[0] == pytest.approx(expected_two, rel=1e-9), (
            "MFJ with both spouses over 65 should still get 2-person IRMAA"
        )
