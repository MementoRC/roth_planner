"""audit-1008 W3 -- survivor modelling (F-14, F-24, F-25, F-39, F-58, F-84).

All households here are synthetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.aca import aca_net_cost, effective_benchmark_premium, resolve_couple_benchmark_annual
from engine.irmaa import irmaa_for_year
from engine.scenario import ConversionPlan, run_no_conversion, run_scenario
from engine.scenario_autofill import auto_fill_22
from engine.scenario_compare import build_scenario, compute_survivor_snapshot
from engine.scenario_compute import compute_social_security
from models.household import Household, SurvivorScenario
from models.ytd_income import YTDSnapshot


def _ss(hh: Household, *, ya: int, sa: int, who_dies: str, death_year: int) -> tuple[float, float]:
    your_ss, spouse_ss, _combined, _, _ = compute_social_security(
        hh=hh,
        ya=ya,
        sa=sa,
        survivor_active=True,
        who_dies=who_dies,
        current_filing_status="Single",
        your_conversion=0.0,
        spouse_conversion=0.0,
        taxable_rmd=0.0,
        spouse_taxable_rmd=0.0,
        extra_withdrawal=0.0,
        spouse_extra_withdrawal=0.0,
        option_income=0.0,
        your_inherited_distribution=0.0,
        spouse_inherited_distribution=0.0,
        ord_div_this_year=0.0,
        ytd_year=None,
        qual_div_this_year=0.0,
        realized_gains=0.0,
        death_year=death_year,
    )
    return your_ss, spouse_ss


# ---------------------------------------------------------------------------
# F-14: survivor IRMAA uses the lookback return's filing status
# ---------------------------------------------------------------------------


class TestF14SurvivorIrmaaLookbackStatus:
    def _run(self):
        surv = SurvivorScenario(who_dies="spouse", death_year=2026)
        hh = Household(
            your_age=73,
            spouse_age=73,
            base_year=2026,
            your_ira=2_000_000,
            spouse_ira=0,
            your_ss_fra=2_400,
            spouse_ss_fra=0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            growth_rate=0.0,
            living_expenses=60_000,
            prior_year_magi={2025: 230_000, 2024: 230_000},
            survivor=surv,
        )
        return hh, run_scenario(hh, ConversionPlan(), end_age=82)

    def test_death_plus_one_uses_mfj_thresholds_for_one_person(self):
        hh, result = self._run()
        yr = next(y for y in result.years if y.year == 2027)
        mfj_one, _ = irmaa_for_year(230_000, 73, 0, filing_status="MFJ", year=2027)
        single_one, _ = irmaa_for_year(230_000, 73, 0, filing_status="Single", year=2027)
        assert single_one > mfj_one, "sanity: Single thresholds must cost more at $230K"
        assert yr.irmaa_cost == pytest.approx(mfj_one, abs=1.0)

    def test_death_plus_two_uses_death_year_joint_return(self):
        surv = SurvivorScenario(who_dies="spouse", death_year=2026)
        hh = Household(
            your_age=73,
            spouse_age=73,
            base_year=2026,
            your_ira=5_500_000,
            spouse_ira=0,
            your_ss_fra=2_400,
            spouse_ss_fra=0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            growth_rate=0.0,
            living_expenses=60_000,
            prior_year_magi={2025: 150_000, 2024: 150_000},
            survivor=surv,
        )
        result = run_scenario(hh, ConversionPlan(), end_age=82)
        y2026 = next(y for y in result.years if y.year == 2026)
        y2028 = next(y for y in result.years if y.year == 2028)
        assert y2026.filing_status == "MFJ"
        expected, _ = irmaa_for_year(y2026.magi, 74, 0, filing_status="MFJ", year=2028)
        single, _ = irmaa_for_year(y2026.magi, 74, 0, filing_status="Single", year=2028)
        assert single > expected, "sanity: the two statuses must differ at this MAGI"
        assert y2028.irmaa_cost == pytest.approx(expected, abs=1.0)

    def test_death_plus_three_switches_to_single(self):
        hh, result = self._run()
        y2027 = next(y for y in result.years if y.year == 2027)
        y2029 = next(y for y in result.years if y.year == 2029)
        expected, _ = irmaa_for_year(y2027.magi, 75, 0, filing_status="Single", year=2029)
        assert y2029.irmaa_cost == pytest.approx(expected, abs=1.0)


# ---------------------------------------------------------------------------
# F-24: widow(er) benefit is reduced from PIA and limited by RIB-LIM
# ---------------------------------------------------------------------------


def _early_claimer_hh(spouse_age: int) -> Household:
    # Decedent ("you"): PIA $3,000/mo, FRA 67, claimed at 62. Dies 2030 (age 65).
    return Household(
        your_age=61,
        spouse_age=spouse_age,
        base_year=2026,
        your_ira=0,
        spouse_ira=0,
        your_ss_fra=3_000,
        spouse_ss_fra=500,
        your_ss_start_age=62,
        spouse_ss_start_age=70,
        your_fra_age=67,
        spouse_fra_age=67,
        your_rmd_start_age=75,
        spouse_rmd_start_age=75,
        growth_rate=0.0,
        ss_cola=0.0,
        survivor=SurvivorScenario(who_dies="you", death_year=2030),
    )


class TestF24WidowBenefitFromPia:
    @pytest.mark.parametrize(
        ("spouse_age", "expected"),
        [
            # onset 60: 36,000 * 0.715 = 25,740 (below the 82.5% limit of 29,700)
            (55, 25_740.0),
            # onset 63: 36,000 * (0.715 + 3/7 * 0.285) = 30,137 -> limited to 29,700
            (58, 29_700.0),
            # onset 67 (FRA): 36,000 -> limited to max(25,200, 29,700) = 29,700
            (62, 29_700.0),
        ],
    )
    def test_reduction_applies_to_pia_then_rib_lim(self, spouse_age, expected):
        hh = _early_claimer_hh(spouse_age)
        onset = spouse_age + 5  # first survivor year 2031
        your_ss, spouse_ss = _ss(hh, ya=66, sa=onset, who_dies="you", death_year=2030)
        assert your_ss == 0.0
        assert spouse_ss == pytest.approx(expected, abs=1.0)


# ---------------------------------------------------------------------------
# F-25: delayed retirement credits earned before death count
# ---------------------------------------------------------------------------


class TestF25DelayedCreditsBeforeDeath:
    def _hh(self, death_year: int) -> Household:
        # Decedent: PIA $3,000/mo, FRA 67, planned claim 70. Survivor at FRA.
        return Household(
            your_age=66,
            spouse_age=66,
            base_year=2026,
            your_ira=0,
            spouse_ira=0,
            your_ss_fra=3_000,
            spouse_ss_fra=500,
            your_ss_start_age=70,
            spouse_ss_start_age=67,
            your_fra_age=67,
            spouse_fra_age=67,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.0,
            ss_cola=0.0,
            survivor=SurvivorScenario(who_dies="you", death_year=death_year),
        )

    def test_unclaimed_death_at_69_keeps_drcs(self):
        # Dies at 69 (2029): 2 years of DRCs -> 36,000 * 1.16 = 41,760
        hh = self._hh(2029)
        _, spouse_ss = _ss(hh, ya=70, sa=70, who_dies="you", death_year=2029)
        assert spouse_ss == pytest.approx(41_760.0, abs=1.0)

    def test_unclaimed_death_before_fra_is_pia(self):
        # Dies at 66 (2026), before FRA: no DRCs, survivor at FRA -> 36,000
        hh = self._hh(2026)
        _, spouse_ss = _ss(hh, ya=67, sa=67, who_dies="you", death_year=2026)
        assert spouse_ss == pytest.approx(36_000.0, abs=1.0)


# ---------------------------------------------------------------------------
# F-39: couple benchmark override is age-rated for the survivor
# ---------------------------------------------------------------------------


class TestF39SurvivorBenchmarkShare:
    def test_survivor_pays_age_rated_share_of_couple_override(self):
        hh = Household(
            your_age=56,
            spouse_age=62,
            base_year=2026,
            your_ira=500_000,
            spouse_ira=500_000,
            your_aca_enrolled=True,
            spouse_aca_enrolled=True,
            aca_benchmark_premium_annual=24_000.0,
            survivor=SurvivorScenario(who_dies="spouse", death_year=2029),
        )
        result = run_no_conversion(hh, end_age=70)
        yr = next(y for y in result.years if y.year == 2030)
        assert yr.filing_status == "Single"
        couple = resolve_couple_benchmark_annual(
            24_000.0,
            your_age=60,
            spouse_age=66,
            filing_status="MFJ",
            year=2030,
            cpi=hh.cpi_assumption,
        )
        share = effective_benchmark_premium(
            couple,
            your_age=60,
            your_on_aca=True,
            spouse_age=66,
            spouse_on_aca=False,
            filing_status="MFJ",
        )
        assert share < 24_000.0
        expected = aca_net_cost(
            yr.aca_magi,
            share,
            enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
            filing_status="Single",
            year=2030,
            cpi=hh.cpi_assumption,
        )
        assert yr.aca_premium_cost == pytest.approx(max(expected, 0.0), abs=1.0)


# ---------------------------------------------------------------------------
# F-58: survivor snapshot starts the brokerage at the death-year CLOSE
# ---------------------------------------------------------------------------


class TestF58SnapshotBrokerageStart:
    def test_snapshot_ignores_death_year_opening_balance(self):
        hh = Household()
        hh.brokerage_start = 1_000_000.0
        hh.brokerage_start_basis = 1_000_000.0
        scen = build_scenario(hh, "no_conv")
        rows_a = compute_survivor_snapshot(hh, [scen], "you", [70])
        yr_death = next(y for y in scen.years if y.your_age == 70)
        # Corrupt the OPENING balance only; the snapshot must start from the close.
        yr_death.brokerage_balance = 0.0
        rows_b = compute_survivor_snapshot(hh, [scen], "you", [70])
        assert rows_a == rows_b


# ---------------------------------------------------------------------------
# scenario_compare follow-up: auto-fill presets see the same YTD as the run
# ---------------------------------------------------------------------------


class TestBuildScenarioAutofillYtd:
    def test_fill_22_preset_uses_ytd_for_sizing(self):
        hh = Household()
        ytd = YTDSnapshot(tax_year=hh.base_year, wages_ytd=40_000.0)
        scen = build_scenario(hh, "fill_22", ytd=ytd)
        direct = run_scenario(hh, auto_fill_22(hh, ytd), "x", end_age=95, ytd=ytd)
        got = [(y.year, y.your_conversion, y.spouse_conversion) for y in scen.years]
        want = [(y.year, y.your_conversion, y.spouse_conversion) for y in direct.years]
        assert got == pytest.approx(want)


# ---------------------------------------------------------------------------
# F-84: Setup caption no longer says the SS step-up is not modelled
# ---------------------------------------------------------------------------


def test_f84_survivor_caption_describes_modelled_ss():
    src = Path("views/setup/_partials/_assumptions.py").read_text(encoding="utf-8")
    assert "NOT YET MODELED: SS survivor benefit step-up" not in src
    assert "SSA-44" in src
