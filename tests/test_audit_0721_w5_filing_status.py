"""Regression tests — audit-0721 wave 5 (filing-status correctness).

Findings covered: C1 (MFS senior-extra), C2 (unmodeled filing_status fail-loud
guard), C4 (headroom spouse_ss gating), C5 (survivor ACA benchmark share),
C34 (roth_eligibility MFJ spouse-only phase-out), C35 (setup reset missing keys).
"""

from __future__ import annotations

import pytest

from engine.aca import aca_net_cost, aca_subsidy, effective_benchmark_premium
from engine.aca_irmaa_compute import compute_year_by_year_timeline
from engine.headroom import compute_headroom
from engine.tax import deductions, estimate_ytd_federal_tax, room_to_12
from models.household import Household, SurvivorScenario
from models.ytd_income import YTDSnapshot


class TestC1MfsSeniorExtra:
    """C1: MFS senior extra must use the MFJ ($1,650) amount, not Single ($2,050)."""

    def test_deductions_mfs_uses_mfj_senior_extra(self):
        # STD_DEDUCTION_SINGLE (16_100, correct — MFS std ded == half MFJ)
        # + SENIOR_EXTRA_MFJ (1_650, fixed — was incorrectly SENIOR_EXTRA_SINGLE 2_050)
        assert deductions(70, 0, filing_status="MFS", year=2026) == pytest.approx(17_750)

    def test_deductions_mfj_unaffected(self):
        """Control: MFJ senior extra behavior must be unchanged."""
        assert deductions(70, 70, filing_status="MFJ", year=2026) == pytest.approx(
            32_200 + 2 * 1_650
        )


class TestC2UnmodeledFilingStatusGuard:
    """C2: an unmodeled filing_status (e.g. HoH) must fail loud, not silently
    fall through to MFJ brackets/deductions."""

    def test_room_to_12_raises_for_hoh(self):
        with pytest.raises(NotImplementedError):
            room_to_12(50_000.0, 30_000.0, filing_status="HoH")

    def test_room_to_22_raises_for_hoh(self):
        from engine.tax import room_to_22

        with pytest.raises(NotImplementedError):
            room_to_22(50_000.0, 30_000.0, filing_status="HoH")

    def test_room_to_24_raises_for_hoh(self):
        from engine.tax import room_to_24

        with pytest.raises(NotImplementedError):
            room_to_24(50_000.0, 30_000.0, filing_status="HoH")

    def test_estimate_ytd_federal_tax_raises_for_hoh(self):
        hh = Household(your_age=50, spouse_age=0, filing_status="HoH")
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=50_000.0)
        with pytest.raises(NotImplementedError):
            estimate_ytd_federal_tax(ytd, hh)

    def test_mfj_and_single_still_work(self):
        """Control: the two UI-reachable filing statuses must not raise."""
        assert room_to_12(50_000.0, 30_000.0, filing_status="MFJ") >= 0
        assert room_to_12(50_000.0, 30_000.0, filing_status="Single") >= 0


class TestC4HeadroomSpouseSsGating:
    """C4: compute_headroom must not fold spouse SS into combined_ss for a
    non-MFJ filing_status (mirrors engine.aca_irmaa_compute._nontaxable_ss)."""

    def test_single_does_not_fold_spouse_ss_into_locked_magi(self):
        hh = Household(
            your_age=63,
            spouse_age=68,
            your_ss_fra=0.0,
            spouse_ss_fra=28_000.0,
            your_ss_start_age=70,
            spouse_ss_start_age=65,
            filing_status="MFJ",
        )
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=50_000.0)

        hr_single = compute_headroom(hh, ytd, filing_status="Single")
        # No "your" SS claimed (age 63 < start 70) and spouse SS must be gated
        # to 0 for Single, so locked_magi should equal magi_ytd with no taxable-SS add.
        assert hr_single.locked_magi == pytest.approx(ytd.magi_ytd)

        hr_mfj = compute_headroom(hh, ytd, filing_status="MFJ")
        assert hr_mfj.locked_magi > hr_single.locked_magi, (
            "MFJ must fold spouse SS in (higher locked_magi); Single must not"
        )


class TestC5SurvivorAcaBenchmarkIndividualShare:
    """C5: a survivor (single, spouse deceased) must get an age-rated
    individual share of the couple benchmark, not the full couple rate."""

    def test_survivor_gets_individual_share_not_couple_rate(self):
        hh = Household()
        hh.base_year = 2026
        hh.your_age = 61
        hh.spouse_age = 59
        hh.filing_status = "MFJ"
        hh.your_aca_enrolled = True
        hh.spouse_aca_enrolled = True
        hh.aca_enhanced_subsidies_active = True
        hh.survivor = SurvivorScenario(who_dies="spouse", death_year=2026)

        base_magi = 50_000.0
        rows = compute_year_by_year_timeline(hh, base_magi=base_magi, years=3, cpi=0.0)
        survivor_row = next(r for r in rows if r.year == 2027)
        assert survivor_row.aca_subsidy is not None

        deceased_age = hh.spouse_age_in(2027)
        expected_individual_share = effective_benchmark_premium(
            hh.aca_benchmark_premium_annual,
            your_age=hh.your_age_in(2027),
            your_on_aca=True,
            spouse_age=deceased_age,
            spouse_on_aca=False,
            filing_status="MFJ",
        )
        assert expected_individual_share < hh.aca_benchmark_premium_annual, (
            "Sanity: the survivor's age-rated share must be less than the full couple rate"
        )

        expected_pay = aca_net_cost(
            base_magi,
            benchmark=expected_individual_share,
            enhanced_subsidies_active=True,
            filing_status="Single",
            year=2027,
            cpi=0.0,
        )
        assert survivor_row.aca_you_pay == pytest.approx(expected_pay)

        # Regression guard: the old bug used the FULL couple rate for the
        # survivor's benchmark, inflating the subsidy (aca_you_pay saturates at
        # the income-based premium cap at this MAGI regardless of benchmark, so
        # the subsidy — not aca_you_pay — is where the bug is visible).
        buggy_subsidy = aca_subsidy(
            base_magi,
            hh.aca_benchmark_premium_annual,
            enhanced_subsidies_active=True,
            filing_status="Single",
            year=2027,
            cpi=0.0,
        )
        assert survivor_row.aca_subsidy != pytest.approx(buggy_subsidy)
        assert survivor_row.aca_subsidy < buggy_subsidy, (
            "Survivor's subsidy must be LOWER than the old full-couple-rate bug "
            "(smaller benchmark → smaller subsidy for the same MAGI)"
        )


class TestC34RothEligibilitySpouseOnlyPhaseout:
    """C34: when "You" has no workplace plan but Spouse does, You's Trad IRA
    deduction must apply the MFJ spouse-only phase-out, not fall through to
    fully-deductible. views/roth_eligibility.py persons tuple now carries
    each person's OTHER spouse's workplace flag (`other_workplace`)."""

    def test_persons_loop_uses_other_spouses_workplace_flag(self):
        """Static check: the elif branch must reference `other_workplace`
        (the OTHER person's plan), not the stale global `spouse_workplace`
        which is wrong when evaluating the Spouse's own row."""
        import inspect

        import views.roth_eligibility as mod

        src = inspect.getsource(mod)
        assert 'elif filing == "MFJ" and other_workplace:' in src
        assert "other_workplace" in src


class TestC35ClearPersonalSessionStateIncludesWorkplaceBeneficiary:
    """C35: household reset must clear workplace-plan/beneficiary keys."""

    def test_clear_removes_workplace_and_beneficiary_keys(self, monkeypatch):
        import views.setup._state as state_mod

        fake_state: dict = {
            "your_has_workplace_plan": True,
            "spouse_has_workplace_plan": True,
            "spouse_is_sole_beneficiary": True,
        }
        monkeypatch.setattr(state_mod.st, "session_state", fake_state)
        state_mod._clear_personal_session_state()

        assert "your_has_workplace_plan" not in fake_state
        assert "spouse_has_workplace_plan" not in fake_state
        assert "spouse_is_sole_beneficiary" not in fake_state
