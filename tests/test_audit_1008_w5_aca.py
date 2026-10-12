"""Audit 2026-10-08 wave W5 -- ACA rules (F-02, F-03, F-40, F-41).

F-02  run_scenario zeroed the surviving spouse's ACA premium when "you" died:
      the survivor sat in the spouse slot of a Single-status year, and the
      Single branch of effective_benchmark_premium only looks at the "you" slot.
F-03  Coverage year Y uses the HHS poverty guidelines published in year Y-1
      (26 CFR 1.36B-1(h)); 2027 coverage must use the published 2026
      guidelines, not a CPI projection of the 2025 ones.
F-40  ARPA/IRA (IRC §36B(c)(1)(E)) removed only the 400% FPL upper limit; the
      100% FPL floor of §36B(c)(1)(A) still applies under the enhanced schedule.
F-41  The enhanced applicable percentage ramps linearly within each band
      (§36B(b)(3)(A)(iii)); it is not a step function.

All figures are synthetic.
"""

import pytest

from engine.aca import (
    _fpl,
    aca_ceiling_magi,
    aca_premium_cap_rate,
    aca_subsidy,
    aca_subsidy_loss,
)
from engine.scenario import ConversionPlan, run_scenario
from models.household import Household, SurvivorScenario

# HHS 2026 poverty guidelines, 48 contiguous states + DC (Federal Register,
# Jan 2026; https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines).
HHS_2026_FPL_1 = 15_960
HHS_2026_FPL_2 = 21_640
# HHS 2025 guidelines -- govern 2026 coverage.
HHS_2025_FPL_1 = 15_650
HHS_2025_FPL_2 = 21_150


class TestF03PublishedFplByCoverageYear:
    @pytest.mark.parametrize("cpi", [0.0, 0.025, 0.04])
    def test_2027_coverage_uses_published_2026_guidelines(self, cpi: float) -> None:
        assert _fpl("Single", year=2027, cpi=cpi) == HHS_2026_FPL_1
        assert _fpl("MFJ", year=2027, cpi=cpi) == HHS_2026_FPL_2

    def test_2026_coverage_still_uses_2025_guidelines(self) -> None:
        assert _fpl("Single", year=2026, cpi=0.03) == HHS_2025_FPL_1
        assert _fpl("MFJ", year=2026, cpi=0.03) == HHS_2025_FPL_2

    def test_years_past_latest_table_project_from_latest_published(self) -> None:
        assert _fpl("MFJ", year=2028, cpi=0.03) == pytest.approx(HHS_2026_FPL_2 * 1.03)
        assert _fpl("Single", year=2029, cpi=0.0) == HHS_2026_FPL_1

    def test_2027_cliff(self) -> None:
        assert aca_ceiling_magi("Single", 2027, 0.025) == 4 * HHS_2026_FPL_1
        assert aca_ceiling_magi("MFJ", 2027, 0.0) == 4 * HHS_2026_FPL_2

    def test_single_just_over_true_2027_cliff_gets_no_subsidy(self) -> None:
        # 64,000 / 15,960 = 4.010 -> over the cliff. The CPI projection put the
        # cliff at 64,165 and paid a ~$10.7K subsidy.
        assert aca_subsidy(64_000, 17_000, False, "Single", year=2027, cpi=0.025) == 0.0

    def test_mfj_under_true_2027_cliff_keeps_subsidy_at_zero_cpi(self) -> None:
        # 85,500 / 21,640 = 3.951 -> eligible at the 10.22% flat band.
        ptc = aca_subsidy(85_500, 33_720.66, False, "MFJ", year=2027, cpi=0.0)
        assert ptc == pytest.approx(33_720.66 - 85_500 * 0.1022)

    def test_headroom_aca_cliff_uses_published_fpl(self) -> None:
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=61,
            spouse_age=59,
            your_aca_enrolled=True,
            spouse_aca_enrolled=True,
            grants=[],
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
        )
        result = compute_headroom(hh, YTDSnapshot(), year=2027, cpi=0.025)
        assert result.room_to_aca_cliff == pytest.approx(4 * HHS_2026_FPL_2)


class TestF40EnhancedFloor:
    @pytest.mark.parametrize(
        ("status", "fpl"), [("MFJ", HHS_2025_FPL_2), ("Single", HHS_2025_FPL_1)]
    )
    @pytest.mark.parametrize("ratio", [0.0, 0.5, 0.99])
    def test_no_credit_below_100_pct_fpl(self, status: str, fpl: int, ratio: float) -> None:
        assert aca_subsidy(ratio * fpl, 20_000, True, status, year=2026, cpi=0.0) == 0.0

    def test_crossing_100_pct_creates_eligibility_not_a_loss(self) -> None:
        # Base MAGI under 100% FPL has no PTC, so a conversion into the band
        # cannot "lose" subsidy.
        loss = aca_subsidy_loss(10_000, 40_000, 20_000, True, "MFJ", year=2026, cpi=0.0)
        assert loss == 0.0


class TestF41EnhancedLinearRamp:
    @pytest.mark.parametrize(
        ("ratio", "expected"),
        [
            (1.10, 0.0),
            (1.40, 0.0),
            (1.60, 0.004),
            (1.75, 0.01),
            (2.25, 0.03),
            (2.75, 0.05),
            (3.25, 0.06625),
            (3.50, 0.0725),
            (4.00, 0.085),
            (6.00, 0.085),
        ],
    )
    def test_applicable_percentage_interpolates(self, ratio: float, expected: float) -> None:
        rate = aca_premium_cap_rate(ratio * HHS_2025_FPL_2, True, "MFJ", year=2026, cpi=0.0)
        assert rate == pytest.approx(expected)

    @pytest.mark.parametrize("edge", [1.5, 2.0, 2.5, 3.0, 4.0])
    def test_no_false_cliffs_at_band_edges(self, edge: float) -> None:
        magi = edge * HHS_2025_FPL_2
        loss = aca_subsidy_loss(magi - 1, magi + 1, 30_000, True, "MFJ", year=2026, cpi=0.0)
        assert loss < 1.0


class TestF02SurvivorAcaPremium:
    def _hh(self, who_dies: str) -> Household:
        return Household(
            your_age=62,
            spouse_age=58,
            your_ira=400_000.0,
            spouse_ira=300_000.0,
            your_roth=0.0,
            spouse_roth=0.0,
            grants=[],
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            your_aca_enrolled=True,
            spouse_aca_enrolled=True,
            advance_aptc_annual=0.0,
            survivor=SurvivorScenario(who_dies=who_dies, death_year=2026),
        )

    def test_spouse_survivor_still_pays_aca_until_65(self) -> None:
        result = run_scenario(self._hh("you"), ConversionPlan(), end_age=70)
        by_year = {yr.year: yr for yr in result.years}
        # Survivor (spouse) is 59 in 2027 and 64 in 2032 -- six ACA years.
        for year in range(2027, 2033):
            assert by_year[year].aca_premium_cost > 0.0, year
        # Medicare from 65 (2033): no marketplace premium.
        assert by_year[2033].aca_premium_cost == 0.0

    def test_spouse_survivor_matches_mirror_household(self) -> None:
        """Survivor aged 59 in 2027 pays the same premium whichever slot she sits in."""
        spouse_survives = {
            yr.year: yr for yr in run_scenario(self._hh("you"), ConversionPlan(), end_age=70).years
        }
        mirror = Household(
            your_age=58,
            spouse_age=62,
            your_ira=300_000.0,
            spouse_ira=400_000.0,
            your_roth=0.0,
            spouse_roth=0.0,
            grants=[],
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            your_aca_enrolled=True,
            spouse_aca_enrolled=True,
            advance_aptc_annual=0.0,
            survivor=SurvivorScenario(who_dies="spouse", death_year=2026),
        )
        you_survive = {
            yr.year: yr for yr in run_scenario(mirror, ConversionPlan(), end_age=70).years
        }
        assert spouse_survives[2027].aca_premium_cost == pytest.approx(
            you_survive[2027].aca_premium_cost, rel=1e-6
        )
