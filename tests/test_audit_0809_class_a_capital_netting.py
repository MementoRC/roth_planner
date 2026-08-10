"""audit-0809 Class A: six sites bypass the §1222/§1211(b)-netted YTDSnapshot
capital-gain properties (ordinary_capital_gain_ytd / preferential_capital_gain_ytd)
and read the raw stcg_ytd/ltcg_ytd/crypto_stcg_ytd/crypto_ltcg_ytd legs instead.
Closes audit-0809 findings #9, #10, #14, #16, #22, #23, #27.

Each test constructs a fixture where the raw sum and the §1222-netted sum
genuinely diverge, so the assertion is not vacuously true under either the
buggy or the fixed code.
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_scenario
from engine.scenario_compute import compute_social_security
from engine.sweet_spot_compute import estimate_ltcg_eligible
from engine.tax import LTCG_RATES_MFJ, LTCG_THRESHOLDS_MFJ
from engine.tax_indexing import index_tuple
from models.household import Household
from models.ytd_income import YTDSnapshot


def _minimal_hh(**overrides: object) -> Household:
    """Household with SS/RMD/option-income/brokerage-forecast all zeroed so
    combined_gross/taxable_income reduce to just the YTD components under test."""
    defaults: dict[str, object] = {
        "your_age": 61,
        "spouse_age": 55,
        "base_year": 2026,
        "cpi_assumption": 0.0,
        "your_ira": 0.0,
        "spouse_ira": 0.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "grants": [],
    }
    defaults.update(overrides)
    return Household(**defaults)  # type: ignore[arg-type]


def _ltcg_stack_tax(
    start: float, total: float, thresholds: tuple[float, float], rates: tuple[float, float, float]
) -> float:
    """Replicates scenario.py's 0/15/20% LTCG stack-walk for a given total."""
    end = start + max(0.0, total)
    at_15 = max(0.0, min(end, thresholds[1]) - max(start, thresholds[0]))
    at_20 = max(0.0, end - max(start, thresholds[1]))
    return at_15 * rates[1] + at_20 * rates[2]


class TestSite1ScenarioCombinedGrossYTDInjection:
    """engine/scenario.py ~:544/:550 — combined_gross YTD injection reads raw
    stcg_ytd/crypto_stcg_ytd; must use ordinary_capital_gain_ytd (§1222/§1211(b)-netted).

    Fixture: net capital position is a LOSS steeper than -$3,000
    (stcg -10,000 / ltcg +3,000 -> net -7,000, capped at -3,000).
    """

    def test_combined_gross_uses_netted_ordinary_leg_not_raw_stcg(self) -> None:
        hh = _minimal_hh()
        ytd = YTDSnapshot(tax_year=2026, stcg_ytd=-10_000.0, ltcg_ytd=3_000.0)

        result = run_scenario(hh, ConversionPlan(), end_age=hh.your_age, ytd=ytd)
        yr = result.years[0]

        expected = ytd.ordinary_capital_gain_ytd  # -3,000: everything else in the fixture is 0
        assert yr.combined_gross == pytest.approx(expected, abs=1.0), (
            f"combined_gross={yr.combined_gross:.2f} should equal the netted/capped "
            f"ordinary_capital_gain_ytd={expected:.2f}, not the raw stcg_ytd=-10,000 sum."
        )


class TestSite2ScenarioLTCGStackWalk:
    """engine/scenario.py ~:775-779 — _ytd_ltcg_total reads raw ltcg_ytd instead of
    preferential_capital_gain_ytd. qualified_dividends_ytd must stay a separate addend.

    Fixture: short-term LOSS partially absorbing a long-term GAIN
    (stcg -50,000 / ltcg +250,000 -> net long-term 200,000, ordinary 0).
    """

    def test_ytd_ltcg_tax_uses_netted_preferential_leg(self) -> None:
        hh = _minimal_hh()
        ytd = YTDSnapshot(
            tax_year=2026,
            stcg_ytd=-50_000.0,
            ltcg_ytd=250_000.0,
            qualified_dividends_ytd=1_000.0,
        )

        result = run_scenario(hh, ConversionPlan(), end_age=hh.your_age, ytd=ytd)
        yr = result.years[0]

        fixed_total = ytd.preferential_capital_gain_ytd + ytd.qualified_dividends_ytd  # 201,000
        thresholds = index_tuple(LTCG_THRESHOLDS_MFJ, hh.base_year, hh.cpi_assumption, round50=True)
        expected_tax = _ltcg_stack_tax(0.0, fixed_total, thresholds, LTCG_RATES_MFJ)

        assert yr.ytd_ltcg_tax == pytest.approx(expected_tax, abs=1.0), (
            f"ytd_ltcg_tax={yr.ytd_ltcg_tax:.2f} should be computed from the netted "
            f"preferential_capital_gain_ytd + qualified_dividends_ytd total="
            f"{fixed_total:.2f} (expected tax {expected_tax:.2f}), not raw "
            f"ltcg_ytd + qualified_dividends_ytd=251,000."
        )


class TestSite3ScenarioComputeSocialSecurity:
    """engine/scenario_compute.py ~:396-411 — compute_social_security's
    provisional-income other_inc block reads all four raw capital legs.

    Fixture: net capital position is a LOSS steeper than -$3,000, structured as
    two snapshots that MUST net to the identical -$3,000 ordinary contribution
    after the §1222/§1211(b) fix (A: stcg -10,000/ltcg +3,000 -> net -7,000,
    capped -3,000; B: stcg -3,000 alone, already at the cap). Pre-fix, A and B
    feed different other_inc (-7,000 vs -3,000) into provisional income and can
    diverge in taxable_ss_amt; post-fix they must be identical.
    """

    def _hh(self) -> Household:
        # your_ss_start_age == your_fra_age == your_age: no early/delayed adjustment,
        # so combined_ss == your_ss_fra * 12 exactly (=10,000/yr).
        return _minimal_hh(
            your_age=67,
            spouse_age=67,
            your_ss_fra=833.333333,
            your_ss_start_age=67,
            your_fra_age=67,
            spouse_ss_fra=0.0,
        )

    def test_taxable_ss_amt_matches_across_equivalent_netted_positions(self) -> None:
        hh = self._hh()
        ytd_a = YTDSnapshot(tax_year=2026, wages_ytd=40_000.0, stcg_ytd=-10_000.0, ltcg_ytd=3_000.0)
        ytd_b = YTDSnapshot(tax_year=2026, wages_ytd=40_000.0, stcg_ytd=-3_000.0, ltcg_ytd=0.0)

        # Sanity: both fixtures net to the same -$3,000 ordinary/preferential split.
        assert ytd_a.ordinary_capital_gain_ytd == pytest.approx(ytd_b.ordinary_capital_gain_ytd)
        assert ytd_a.preferential_capital_gain_ytd == pytest.approx(ytd_b.preferential_capital_gain_ytd)

        *_, taxable_ss_a = compute_social_security(
            hh, 67, 67, False, None, "MFJ", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ytd_a
        )
        *_, taxable_ss_b = compute_social_security(
            hh, 67, 67, False, None, "MFJ", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ytd_b
        )

        assert taxable_ss_a == pytest.approx(taxable_ss_b, abs=1.0), (
            f"taxable_ss_amt diverges between two YTD fixtures that net to the identical "
            f"-$3,000 ordinary capital position (A={taxable_ss_a:.2f}, B={taxable_ss_b:.2f}) "
            f"-- other_inc must use the netted ordinary_capital_gain_ytd + "
            f"preferential_capital_gain_ytd, not the raw four-leg sum."
        )


class TestSite4SweetSpotEstimateLtcgEligible:
    """engine/sweet_spot_compute.py ~:316 — estimate_ltcg_eligible reads raw
    ltcg_ytd; must mirror scenario.py's fixed stack-walk input.

    Fixture: same short-term-loss-absorbing-long-term-gain shape as Site 2.
    """

    def test_estimate_ltcg_eligible_uses_netted_preferential_leg(self) -> None:
        hh = _minimal_hh()
        ytd = YTDSnapshot(
            tax_year=2026, stcg_ytd=-5_000.0, ltcg_ytd=20_000.0, qualified_dividends_ytd=1_000.0
        )

        result = estimate_ltcg_eligible(hh, 2026, ytd)

        expected = ytd.preferential_capital_gain_ytd + ytd.qualified_dividends_ytd  # 15,000 + 1,000
        assert result == pytest.approx(expected), (
            f"estimate_ltcg_eligible={result:.2f} should equal the netted "
            f"preferential_capital_gain_ytd + qualified_dividends_ytd={expected:.2f}, "
            f"not the raw ltcg_ytd + qualified_dividends_ytd=21,000."
        )


class TestSite5YTDIncomeTotalInvestmentIncome:
    """models/ytd_income.py ~:240 — total_investment_income (the §1411 NIIT base)
    sums the four raw capital legs instead of the netted ordinary/preferential pair.

    Worked case from Reg. §1.1411-4(d)(2)/(f)(4): $50,000 net capital LOSS + $40,000
    ordinary dividends. Net gain floors to $0 (never negative for NII purposes), the
    §1211(b) $3,000 allowed loss IS a properly allocable deduction against NII
    (§1.1411-4(f)(4) Example 1; Form 8960 line 5a starts from the Schedule D figure
    already capped at -$3,000, and line 5d has no zero floor) -> NII = $40,000 - $3,000
    = $37,000. The raw-sum code instead computes a net capital loss of -$10,000,
    which (after niit.py's TOTAL floor) yields NII $0 -- silently zeroing NIIT
    liability that should be $37,000 * 3.8%.
    """

    def test_total_investment_income_is_37000_not_raw_negative_sum(self) -> None:
        ytd = YTDSnapshot(tax_year=2026, stcg_ytd=-50_000.0, ordinary_dividends_ytd=40_000.0)

        assert ytd.total_investment_income == pytest.approx(37_000.0), (
            f"total_investment_income={ytd.total_investment_income:.2f} should be "
            f"$37,000 (= ordinary_capital_gain_ytd -3,000 + preferential_capital_gain_ytd "
            f"0 + dividends 40,000), per Reg. Sec 1.1411-4(f)(4)'s treatment of the "
            f"Sec 1211(b) allowed loss as a properly allocable NII deduction -- not the "
            f"raw ltcg+stcg+dividends+... sum of -$10,000."
        )
