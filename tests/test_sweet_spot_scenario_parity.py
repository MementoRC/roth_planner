"""Differential parity tests: engine.sweet_spot_compute vs engine.scenario (oracle).

Audit 2026-07-13 (R1+R2 confirmed) found 5 defects where sweet_spot_compute had
drifted from scenario.py's canonical formulas:
  1. all_in_at_conversion's magi omitted forecast qual/ord dividends + realized LTCG
  2. NIIT's net-investment-income omitted realized_gains/qual_div/ord_div
  3. base_income_for_year never added RMD / inherited-IRA income
  4. the ordinary-income base omitted the forecast ordinary-dividend term
  5. estimate_ltcg_eligible never suppressed the forecast in the base year with YTD

These tests build a Household + inputs where YTD ordinary income, forecast
qualified/ordinary dividends, and realized LTCG are all present, and a sweep
window that includes RMD years -- then assert sweet_spot_compute's magi,
niit_magi, and base income agree with engine.scenario's values for the same
year/conversion, using run_scenario as the oracle.
"""

from __future__ import annotations

import pytest

from engine.niit import niit
from engine.scenario import ConversionPlan, run_scenario
from engine.scenario_types import YearResult
from engine.sweet_spot_compute import (
    all_in_at_conversion,
    base_income_for_year,
    estimate_brokerage_income,
    estimate_ltcg_eligible,
    estimate_rmd_income,
)
from models.household import GrowthProfile, Household
from models.ytd_income import YTDSnapshot


def _oracle_year(
    hh: Household,
    year: int,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> YearResult:
    """Run engine.scenario for `hh` through `year` and return that year's YearResult.

    net_inv_income defaults to 0.0 (audit-0805 C10): most callers don't pass a
    manual net_inv_income, so the default keeps them unaffected. Callers that
    compare against a sweet_spot_compute result computed WITH a manual
    net_inv_income must pass the same value here, or the oracle's niit_magi
    will silently omit it while the sweet_spot side includes it.
    """
    end_age = hh.your_age + (year - hh.base_year)
    result = run_scenario(
        hh, ConversionPlan(), "oracle", end_age=end_age, ytd=ytd, net_inv_income=net_inv_income
    )
    for yr in result.years:
        if yr.year == year:
            return yr
    raise AssertionError(f"year {year} not found in scenario result")  # pragma: no cover


def _no_ss_no_option_household(**overrides: object) -> Household:
    """Household with SS and option income zeroed out so combined_gross/magi
    comparisons are not muddied by SS-taxability or option-income mechanics
    (those are exercised by other regression suites)."""
    defaults: dict[str, object] = {
        "your_age": 61,
        "spouse_age": 55,
        "base_year": 2026,
        "grants": [],
        "txn_price_now": 0.0,
        "txn_price_late": 0.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "your_ss_start_age": 70,
        "spouse_ss_start_age": 70,
        "cpi_assumption": 0.0,
        "ss_cola": 0.0,
        "growth_rate": 0.0,
        "filing_status": "MFJ",
    }
    defaults.update(overrides)
    return Household(**defaults)  # type: ignore[arg-type]


class TestBaseYearYtdMagiNiitParity:
    """Defects 1/2 (base-year YTD path, regression guard): with YTD ordinary
    income + qualified/ordinary dividends + LTCG present, sweet_spot's magi,
    niit_magi, and base gross must match engine.scenario's at conv=0."""

    def test_magi_niit_magi_gross_match_oracle(self) -> None:
        hh = _no_ss_no_option_household()
        year = hh.base_year
        ytd = YTDSnapshot(
            tax_year=year,
            wages_ytd=80_000.0,
            qualified_dividends_ytd=5_000.0,
            ordinary_dividends_ytd=3_000.0,
            ltcg_ytd=20_000.0,
        )
        oracle = _oracle_year(hh, year, ytd=ytd)

        base = base_income_for_year(hh, year, ytd=ytd)
        result = all_in_at_conversion(hh, base, 0.0, 0.0)

        assert result.magi == pytest.approx(oracle.magi, abs=1.0)
        assert result.niit_magi == pytest.approx(oracle.niit_magi, abs=1.0)
        assert base.base_gross == pytest.approx(oracle.combined_gross, abs=1.0)


class TestForecastDividendsAndGainsMagiNiitParity:
    """Defects 1/2/4: forecast qualified/ordinary dividends + realized LTCG (no
    YTD) must fold into sweet_spot's magi, niit_magi, and NIIT cost identically
    to engine.scenario. Tested at the base year (offset 0) so the brokerage
    balance used by both engines is identical (hh.brokerage_start) -- scenario.py
    projects/compounds the balance in later years, which sweet_spot's per-year
    snapshot deliberately does not model (see estimate_brokerage_income docstring)."""

    def _hh(self) -> Household:
        return _no_ss_no_option_household(
            brokerage_start=500_000.0,
            brok_turnover=0.30,
            brokerage_growth=GrowthProfile(
                default_rate=0.07, yield_rate=0.02, qualified_fraction=0.6
            ),
        )

    def test_magi_matches_oracle(self) -> None:
        hh = self._hh()
        year = hh.base_year
        oracle = _oracle_year(hh, year, ytd=None)

        base = base_income_for_year(hh, year, ytd=None)
        result = all_in_at_conversion(hh, base, 0.0, 0.0)

        assert base.forecast_qual_div > 0
        assert base.forecast_ord_div > 0
        assert base.forecast_realized_gains > 0
        assert result.magi == pytest.approx(oracle.magi, abs=1.0)
        assert result.niit_magi == pytest.approx(oracle.niit_magi, abs=1.0)

    def test_niit_cost_matches_oracle(self) -> None:
        """R2: net investment income = realized_gains + qual_div + ord_div (+ ytd
        investment income, 0 here). End-to-end NIIT cost must match the oracle."""
        hh = self._hh()
        year = hh.base_year
        oracle = _oracle_year(hh, year, ytd=None)

        # Independently replicate scenario.py's net_investment_income for this
        # year (not via the module under test) as the parity anchor.
        brokerage = hh.brokerage_start
        appr = hh.brokerage_growth.appreciation_for(year)  # type: ignore[union-attr]
        qual_div = hh.brokerage_growth.qualified_div_for(year, brokerage)  # type: ignore[union-attr]
        ord_div = hh.brokerage_growth.ordinary_div_for(year, brokerage)  # type: ignore[union-attr]
        realized_gains = brokerage * appr * hh.brok_turnover
        expected_nii = realized_gains + qual_div + ord_div

        # Sanity anchor: the oracle's own niit_cost must be reproducible from its
        # exposed niit_magi plus our independently-derived NII.
        assert oracle.niit_cost == pytest.approx(
            niit(oracle.niit_magi, expected_nii, filing_status=hh.filing_status), abs=1.0
        )

        base = base_income_for_year(hh, year, ytd=None)
        result = all_in_at_conversion(hh, base, 0.0, net_inv_income=0.0)

        # R2: sweet_spot's year-level NII addition must equal the same formula.
        assert base.net_investment_income_addl == pytest.approx(expected_nii, abs=1.0)

        sweet_spot_niit_cost = niit(
            result.niit_magi, base.net_investment_income_addl, filing_status=hh.filing_status
        )
        assert sweet_spot_niit_cost == pytest.approx(oracle.niit_cost, abs=1.0)


class TestRmdYearBaseIncomeParity:
    """Defect 3: base_income_for_year must fold taxable RMD income into base_gross
    and base_magi for years where the primary owes RMDs, matching engine.scenario.
    growth_rate=0.0 keeps the IRA balance static (no conversions/withdrawals occur
    before the RMD year in either engine), so both engines compute RMD off the
    identical undiminished balance."""

    def test_rmd_year_matches_oracle(self) -> None:
        hh = _no_ss_no_option_household(your_ira=1_700_000.0, spouse_ira=1_700_000.0)
        assert hh.your_rmd_start_age == 75  # sanity: post-1959 cohort default

        rmd_year = hh.base_year + (hh.your_rmd_start_age - hh.your_age)  # first RMD year
        oracle = _oracle_year(hh, rmd_year, ytd=None)

        assert oracle.taxable_rmd > 0, "precondition: oracle must show a taxable RMD this year"

        base = base_income_for_year(hh, rmd_year, ytd=None)
        result = all_in_at_conversion(hh, base, 0.0, 0.0)

        assert base.rmd_income > 0, "sweet_spot must recognize RMD income in this year"
        assert base.rmd_income == pytest.approx(oracle.taxable_rmd, abs=1.0)
        assert base.base_gross == pytest.approx(oracle.combined_gross, abs=1.0)
        assert result.magi == pytest.approx(oracle.magi, abs=1.0)


class TestRmdYtdNettingParity:
    """Audit findings 2+3 (HIGH, 2026-08): estimate_rmd_income does not net out
    RMD already taken/distributed year-to-date before adding the projected RMD,
    double-counting the YTD portion: once via ytd.magi_ytd (which includes
    ira_distributions_ytd) and again via the full un-netted forecast RMD.
    Mirrors engine.scenario's C2/scenario-1 reduction (ytd_year.ira_distributions_ytd
    netted against yr.taxable_rmd/yr.spouse_taxable_rmd) -- see scenario.py's
    "base-year RMD net-of-YTD reconciliation" block.

    Both households below are RMD-active in base_year (age == your_rmd_start_age)
    with no SS/option income, isolating the RMD double-count from any SS-taxability
    interaction (that compounding is exercised separately by finding 1's tests).
    """

    def _hh(self, *, your_ira: float, spouse_ira: float) -> Household:
        return _no_ss_no_option_household(
            your_age=75, spouse_age=75, your_ira=your_ira, spouse_ira=spouse_ira
        )

    def test_magi_matches_oracle_with_ytd_distributions(self) -> None:
        """Finding 2: base.rmd_income / result.magi must agree with scenario.py's
        oracle once YTD IRA distributions are netted out of the forecast RMD."""
        hh = self._hh(your_ira=1_700_000.0, spouse_ira=1_700_000.0)
        year = hh.base_year
        # your_age=75 in base_year 2026 -> birth year 1951 -> 1951-1959 cohort -> 73.
        assert hh.your_rmd_start_age == 73  # sanity: RMD active this year

        ytd_dist = 8_000.0
        ytd = YTDSnapshot(tax_year=year, ira_distributions_ytd=ytd_dist)
        oracle = _oracle_year(hh, year, ytd=ytd)

        base = base_income_for_year(hh, year, ytd=ytd)
        result = all_in_at_conversion(hh, base, 0.0, 0.0)

        # oracle.taxable_rmd is "your" side only -- base.rmd_income combines
        # both spouses (+ inherited IRAs, 0 here), so compare against the sum.
        oracle_combined_rmd = oracle.taxable_rmd + oracle.spouse_taxable_rmd
        assert base.rmd_income == pytest.approx(oracle_combined_rmd, abs=1.0), (
            f"base.rmd_income ({base.rmd_income:.2f}) must equal the oracle's "
            f"YTD-netted combined taxable RMD ({oracle_combined_rmd:.2f})"
        )
        assert result.magi == pytest.approx(oracle.magi, abs=1.0), (
            f"result.magi ({result.magi:.2f}) must equal the oracle's magi "
            f"({oracle.magi:.2f}) -- pre-fix it is inflated by the double-counted "
            f"YTD distribution"
        )

    def test_niit_no_phantom_charge_from_double_counted_rmd(self) -> None:
        """Finding 3 (INDEPENDENT of finding 2's magi assertion): a large YTD RMD
        distribution must not manufacture a phantom NIIT charge. Sized so the
        correct (oracle) MAGI sits just under the $250K MFJ NIIT threshold, but
        the un-netted double-count pushes the buggy MAGI over it."""
        # Combined RMD at age 75 (divisor 24.6, per-spouse) on $6M combined IRA
        # balance = 2 * (3,000,000 / 24.6) = $243,902.44 -- just under the $250K
        # NIIT threshold on its own.
        hh = self._hh(your_ira=3_000_000.0, spouse_ira=3_000_000.0)
        year = hh.base_year
        # your_age=75 in base_year 2026 -> birth year 1951 -> 1951-1959 cohort -> 73.
        assert hh.your_rmd_start_age == 73  # sanity: RMD active this year

        ytd_dist = 47_000.0
        ytd = YTDSnapshot(tax_year=year, ira_distributions_ytd=ytd_dist)
        # net_inv_income lowered 50_000 -> 5_000.0 (audit-0805 C10): manual
        # net_inv_income now counts toward niit_magi (threaded through
        # _oracle_year below too), so the original 50K pushed the oracle's
        # niit_magi to ~243,902+50,000=~294K, clearing the $250K MFJ threshold
        # and destroying this test's "correct answer is $0 NIIT" premise. 5K
        # keeps headroom under the threshold (243,902.44+5,000=248,902.44)
        # while staying non-zero so NIIT would still fire if the threshold
        # were crossed.
        net_inv_income = 5_000.0  # manual NII override -- makes NIIT sensitive to magi
        oracle = _oracle_year(hh, year, ytd=ytd, net_inv_income=net_inv_income)
        assert oracle.niit_magi < 250_000.0, (
            "precondition: oracle (correct) niit_magi must sit under the MFJ "
            f"NIIT threshold, got {oracle.niit_magi:.2f}"
        )

        oracle_niit = niit(oracle.niit_magi, net_inv_income, filing_status=hh.filing_status)
        assert oracle_niit == pytest.approx(0.0), (
            "precondition: oracle must show zero NIIT (magi below threshold)"
        )

        base = base_income_for_year(hh, year, ytd=ytd)
        result = all_in_at_conversion(hh, base, 0.0, net_inv_income=net_inv_income)

        assert result.niit_magi == pytest.approx(oracle.niit_magi, abs=1.0), (
            f"result.niit_magi ({result.niit_magi:.2f}) must equal the oracle's "
            f"niit_magi ({oracle.niit_magi:.2f}); pre-fix it is inflated by the "
            "double-counted YTD RMD, pushing it over the NIIT threshold"
        )
        sweet_spot_niit = niit(
            result.niit_magi, net_inv_income, filing_status=hh.filing_status
        )
        assert sweet_spot_niit == pytest.approx(0.0), (
            f"sweet_spot must NOT manufacture a phantom NIIT charge; got "
            f"{sweet_spot_niit:.2f} (oracle correctly shows $0.00)"
        )


class TestQcdNettingOutOfNiitMagi:
    """Audit finding 4 (MEDIUM, 2026-08): Sweet Spot Finder is unaware of a
    household's QCD election and doesn't net it out of niit_magi (or magi),
    overstating both by the full QCD amount. Mirrors
    engine.scenario_compute.compute_rmds' netting:
        taxable_rmd = max(your_rmd - min(qcd, qcd_limit, your_rmd), 0)
    gated on age >= QCD_MIN_AGE, applied per-spouse (not pooled, unlike the
    YTD-distribution netting in findings 2+3)."""

    def _hh(self) -> Household:
        return _no_ss_no_option_household(
            your_age=75, spouse_age=75, your_ira=1_700_000.0, spouse_ira=1_700_000.0
        )

    def test_qcd_nets_out_of_magi_and_niit_magi_matches_oracle(self) -> None:
        from engine.scenario_compute import QCD_MIN_AGE

        hh = self._hh()
        year = hh.base_year
        assert hh.your_age >= QCD_MIN_AGE  # sanity: QCD-eligible this year

        qcd_amount = 40_000.0
        plan = ConversionPlan(qcds={year: qcd_amount})
        oracle_result = run_scenario(hh, plan, "oracle", end_age=hh.your_age, ytd=None)
        oracle = next(yr for yr in oracle_result.years if yr.year == year)
        assert oracle.qcd == pytest.approx(qcd_amount), (
            "precondition: oracle must record the full QCD election"
        )

        base = base_income_for_year(hh, year, ytd=None, your_qcd=qcd_amount)
        result = all_in_at_conversion(hh, base, 0.0, 0.0)

        assert result.niit_magi == pytest.approx(oracle.niit_magi, abs=1.0), (
            f"result.niit_magi ({result.niit_magi:.2f}) must equal the oracle's "
            f"QCD-netted niit_magi ({oracle.niit_magi:.2f}); pre-fix it is "
            f"overstated by the full ${qcd_amount:,.0f} QCD"
        )
        assert result.magi == pytest.approx(oracle.magi, abs=1.0), (
            f"result.magi ({result.magi:.2f}) must equal the oracle's QCD-netted "
            f"magi ({oracle.magi:.2f})"
        )

    def test_qcd_ignored_below_qcd_min_age(self) -> None:
        """A QCD amount supplied for a spouse below QCD_MIN_AGE must have no
        effect (mirrors compute_rmds' age gate)."""
        from engine.scenario_compute import QCD_MIN_AGE

        hh = _no_ss_no_option_household(
            your_age=QCD_MIN_AGE - 1,
            spouse_age=QCD_MIN_AGE - 1,
            your_ira=1_700_000.0,
            spouse_ira=1_700_000.0,
        )
        year = hh.base_year

        no_qcd = estimate_rmd_income(hh, year)
        with_qcd = estimate_rmd_income(hh, year, your_qcd=40_000.0, spouse_qcd=40_000.0)

        assert with_qcd == pytest.approx(no_qcd), (
            "QCD below QCD_MIN_AGE must not reduce estimated RMD income"
        )


class TestEstimateLtcgEligibleBaseYearSuppression:
    """Defect 5: estimate_ltcg_eligible must suppress the forecast in the base
    year when ytd actuals are supplied, replacing it with realized YTD LTCG +
    qualified dividends -- mirroring scenario.py's base-year suppression."""

    def test_forecast_suppressed_and_replaced_with_ytd(self) -> None:
        hh = _no_ss_no_option_household(
            brokerage_start=500_000.0,
            brok_turnover=0.30,
            brokerage_growth=GrowthProfile(
                default_rate=0.07, yield_rate=0.02, qualified_fraction=0.6
            ),
        )
        year = hh.base_year

        forecast_only = estimate_ltcg_eligible(hh, year, ytd=None)
        assert forecast_only > 0, "precondition: forecast must be nonzero"

        ytd = YTDSnapshot(tax_year=year, ltcg_ytd=15_000.0, qualified_dividends_ytd=2_000.0)
        with_ytd = estimate_ltcg_eligible(hh, year, ytd=ytd)

        assert with_ytd == pytest.approx(17_000.0, abs=0.01)
        assert with_ytd != pytest.approx(forecast_only), (
            "ytd-suppressed value must differ from the (unsuppressed) forecast"
        )

        # Cross-check against the underlying suppression helper directly.
        qual_div, _ord_div, realized_gains = estimate_brokerage_income(hh, year, ytd)
        assert qual_div == 0.0
        assert realized_gains == 0.0


class TestMU8F1LtcgStackRegression:
    """Regression lock for the already-shipped MU8-F1 fix: base_income_for_year
    must fold ytd_ordinary (= ytd.total_ordinary_income, net of nqo_exercise_ytd)
    into the LTCG-stack start, not just into MAGI. A conversion that keeps
    taxable_inc below the 0%->15% LTCG threshold WITHOUT ytd wages, but pushes it
    above the threshold WITH ytd wages, must show ltcg_delta > 0 only in the
    with-ytd case."""

    def _hh(self) -> Household:
        # Deliberately keeps default option income + SS (unlike the other test
        # classes' zeroed-out household): the default option income is what lifts
        # taxable_inc close enough to the LTCG threshold for a modest conversion
        # to be the deciding factor, mirroring test_audit_0707_batch_a.py's
        # TestSweetSpotYtdOrdinaryBase.test_ytd_ordinary_shifts_the_ltcg_stack_base.
        #
        # The hold-to-expiration exercise-schedule default (PR #373 follow-up)
        # no longer lands the first TXN grant's spread in base_year on its own
        # (it now lands in the grant's own expiry_year), so it's pinned
        # explicitly here to preserve this test's calibration.
        from models.exercise_schedule import ExerciseSchedule

        hh = Household(
            your_age=66,
            spouse_age=64,
            base_year=2026,
            cpi_assumption=0.0,
            ss_cola=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            filing_status="MFJ",
        )
        hh.exercise_schedule = ExerciseSchedule()
        hh.exercise_schedule.set_shares(hh.grants[0].key(), hh.base_year, hh.grants[0].shares)
        hh.exercise_schedule.set_price(hh.base_year, hh.txn_price_now)
        return hh

    def test_ytd_ordinary_income_shifts_ltcg_stack_start(self) -> None:
        from engine.tax import LTCG_THRESHOLDS_MFJ, STD_DEDUCTION_MFJ

        hh = self._hh()
        year = hh.base_year
        threshold_0_15 = LTCG_THRESHOLDS_MFJ[0]

        conv = threshold_0_15 - STD_DEDUCTION_MFJ - 5_000.0  # below threshold w/o ytd
        wages = 15_000.0  # pushes taxable_inc above threshold w/ ytd
        ltcg_eligible = 20_000.0

        ytd = YTDSnapshot(tax_year=year, wages_ytd=wages)
        b_no_ytd = base_income_for_year(hh, year, ytd=None)
        b_with_ytd = base_income_for_year(hh, year, ytd=ytd)

        assert b_with_ytd.ytd_ordinary == pytest.approx(wages)

        res_no_ytd = all_in_at_conversion(
            hh, b_no_ytd, conv, 0.0, ltcg_eligible=ltcg_eligible
        )
        res_with_ytd = all_in_at_conversion(
            hh, b_with_ytd, conv, 0.0, ltcg_eligible=ltcg_eligible
        )

        assert res_no_ytd.ltcg_delta == pytest.approx(0.0, abs=0.01), (
            "without ytd_ordinary, taxable_inc stays below the LTCG 0%->15% threshold"
        )
        assert res_with_ytd.ltcg_delta > 0.0, (
            "ytd_ordinary must shift the LTCG-stack start above the threshold, "
            f"got ltcg_delta={res_with_ytd.ltcg_delta}"
        )


class TestBaseYearNqoExerciseMagiParity:
    """Audit 2026-07-22 (CROSS-magi-nqo / CROSS-niitmagi-nqo): a base-year NQO
    exercise spread is already captured in hh.option_income(base_year), so it
    must be counted ONCE in sweet_spot's MAGI / NIIT-MAGI base, matching
    scenario.py (which subtracts nqo_exercise_ytd from option_income before
    folding in magi_ytd -- scenario.py:343-345). Before the fix, sweet_spot
    added it via BOTH `opt` (hh.option_income) AND `magi_ytd`, overstating magi
    and niit_magi by exactly nqo_exercise_ytd, inflating irmaa/aca/niit deltas
    and misdirecting the IRMAA-safe conversion search.

    The rest of this suite misses the case because _no_ss_no_option_household
    zeroes grants/option income, so no test combined nqo_exercise_ytd > 0 with a
    live base-year exercise.
    """

    def _hh(self) -> Household:
        from models.exercise_schedule import ExerciseSchedule

        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            cpi_assumption=0.0,
            ss_cola=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            filing_status="MFJ",
        )
        # Exercise the first TXN grant's full block in the base year so its
        # spread lands in option_income(base_year).
        hh.exercise_schedule = ExerciseSchedule()
        hh.exercise_schedule.set_shares(hh.grants[0].key(), hh.base_year, hh.grants[0].shares)
        hh.exercise_schedule.set_price(hh.base_year, hh.txn_price_now)
        return hh

    def test_magi_and_niit_magi_count_nqo_exercise_once(self) -> None:
        hh = self._hh()
        year = hh.base_year
        nqo = hh.option_income(year)
        assert nqo > 0, "precondition: base-year exercise must produce option income"

        # The realized exercise IS the base-year YTD actual (magi_ytd/niit_magi_ytd
        # both include nqo_exercise_ytd), exactly as a real mid-year exercise would.
        ytd = YTDSnapshot(tax_year=year, nqo_exercise_ytd=nqo)
        oracle = _oracle_year(hh, year, ytd=ytd)

        base = base_income_for_year(hh, year, ytd=ytd)
        result = all_in_at_conversion(hh, base, 0.0, 0.0)

        # Counted once: sweet_spot must agree with the scenario oracle. Pre-fix,
        # result.magi/niit_magi were oracle + nqo (double-counted).
        assert result.magi == pytest.approx(oracle.magi, abs=1.0)
        assert result.niit_magi == pytest.approx(oracle.niit_magi, abs=1.0)
