"""audit-0823 cluster 1: the LTCG preferential-rate stack-walk END must be
capped at TOTAL taxable income (ordinary + LTCG, minus all deductions,
floored at 0) -- not "ordinary taxable income + full LTCG" unadjusted.

engine/tax.py::estimate_ytd_federal_tax already applies this cap (audit-0805
C1, tax.py:539-548: ``taxable_total = max(gross + ltcg - std_ded, 0.0)``).
Two other sites still use the pre-C1 shape:

  SITE A -- engine/scenario.py ~805-806 (_project_year, base-year YTD LTCG
  stack-walk): ``_ytd_ltcg_end = max(0, yr.taxable_income) + max(0, _ytd_ltcg_total)``.

  SITE B -- engine/scenario_compare.py ~87-88 (survivor_year_tax):
  ``ltcg_end = taxable_ordinary + brok_ltcg_income``.

Rationale (Form 1040 Qualified Dividends and Capital Gain Tax Worksheet,
lines 1/6/7/9): a standard deduction unused by ordinary income must offset
LTCG too, so the preferential amount actually taxed is capped at TOTAL
taxable income, not stacked on top of ordinary taxable income unadjusted.

Expected dollars below are derived purely from the statutory building blocks
(engine.tax constants + deductions()/senior_bonus_deduction() public
helpers) -- never from running the patched scenario/comparator code and
reading its output.
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_scenario
from engine.scenario_compare import survivor_year_tax
from engine.tax import (
    LTCG_RATES_MFJ,
    LTCG_RATES_SINGLE,
    LTCG_THRESHOLDS_MFJ,
    LTCG_THRESHOLDS_SINGLE,
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_MFJ,
    STD_DEDUCTION_SINGLE,
    deductions,
    federal_tax_single,
    senior_bonus_deduction,
)
from engine.tax_indexing import index_tuple
from models.household import Household
from models.ytd_income import YTDSnapshot


def _stack_tax(
    start: float, end: float, thresholds: tuple[float, float], rates: tuple[float, float, float]
) -> float:
    """0/15/20% LTCG stack-walk tax for an explicit [start, end) band."""
    at_15 = max(0.0, min(end, thresholds[1]) - max(start, thresholds[0]))
    at_20 = max(0.0, end - max(start, thresholds[1]))
    return at_15 * rates[1] + at_20 * rates[2]


def _minimal_hh(**overrides: object) -> Household:
    """MFJ household, ages below 65 (no senior extra / OBBBA bonus noise),
    with SS/RMD/option-income/brokerage-forecast all zeroed so combined_gross
    reduces to just the YTD components under test."""
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


class TestSiteAScenarioLTCGEndCappedAtTotalTaxableIncome:
    """engine/scenario.py ~805-806: base-year YTD LTCG stack-walk end must be
    capped at total taxable income (ordinary + LTCG - deductions, floored 0),
    not ordinary-taxable-income + full LTCG unadjusted.

    Fixture: wages 5,000 (well below the 32,200 MFJ standard deduction) +
    LTCG 200,000. Ordinary taxable income floors to 0, so the deduction has
    31,200-2,050... i.e. 27,200 of UNUSED capacity that must offset the LTCG
    stack under the C1 shape.
    """

    def test_ltcg_end_capped_when_deductions_exceed_ordinary_income(self) -> None:
        hh = _minimal_hh()
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=5_000.0, ltcg_ytd=200_000.0)

        result = run_scenario(hh, ConversionPlan(), end_age=hh.your_age, ytd=ytd)
        yr = result.years[0]

        combined_gross = 5_000.0  # only nonzero component in this fixture
        total_deductions = deductions(
            hh.your_age, hh.spouse_age, STD_DEDUCTION_MFJ, None, filing_status="MFJ", year=2026, cpi=0.0
        )
        assert total_deductions == pytest.approx(32_200.0)  # sanity: ages < 65, no senior extra
        ltcg_total = ytd.preferential_capital_gain_ytd + ytd.qualified_dividends_ytd
        assert ltcg_total == pytest.approx(200_000.0)

        start = max(0.0, combined_gross - total_deductions)  # ordinary taxable income, floors to 0
        assert start == pytest.approx(0.0)
        end = max(0.0, combined_gross + ltcg_total - total_deductions)  # C1-shape cap

        thresholds = index_tuple(LTCG_THRESHOLDS_MFJ, 2026, 0.0, round50=True)
        expected_tax = _stack_tax(start, end, thresholds, LTCG_RATES_MFJ)
        assert expected_tax == pytest.approx(11_085.0)  # 172,800 taxed at 0/15%: (172,800-98,900)*0.15

        assert yr.ytd_ltcg_tax == pytest.approx(expected_tax, abs=1.0), (
            f"ytd_ltcg_tax={yr.ytd_ltcg_tax:.2f} should be {expected_tax:.2f} (end capped at "
            f"total taxable income {end:.2f}), not the pre-C1 shape (end = ordinary taxable "
            f"income {start:.2f} + full LTCG {ltcg_total:.2f} = {start + ltcg_total:.2f})."
        )

    def test_no_behaviour_change_when_deductions_below_ordinary_income(self) -> None:
        """When ordinary income already exceeds deductions, the C1 cap and the
        pre-C1 shape produce the IDENTICAL end -- no regression for the common case."""
        hh = _minimal_hh()
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=50_000.0, ltcg_ytd=100_000.0)

        result = run_scenario(hh, ConversionPlan(), end_age=hh.your_age, ytd=ytd)
        yr = result.years[0]

        combined_gross = 50_000.0
        total_deductions = 32_200.0
        ltcg_total = 100_000.0
        start = max(0.0, combined_gross - total_deductions)
        old_end = start + ltcg_total
        new_end = max(0.0, combined_gross + ltcg_total - total_deductions)
        assert old_end == pytest.approx(new_end)  # the two shapes agree here

        thresholds = index_tuple(LTCG_THRESHOLDS_MFJ, 2026, 0.0, round50=True)
        expected_tax = _stack_tax(start, new_end, thresholds, LTCG_RATES_MFJ)
        assert expected_tax == pytest.approx(2_835.0)  # (117,800-98,900)*0.15

        assert yr.ytd_ltcg_tax == pytest.approx(expected_tax, abs=1.0)


class TestSiteADifferentialConversionLTCGMarginalCostNeverNegative:
    """engine/scenario.py ~825-841 (_ytd_ltcg_end_base, the WITHOUT-conversion
    counterfactual): the with-conversion leg (`_ytd_ltcg_end`, fixed above at
    ~812) is already capped at total taxable income, but the base leg still
    used the pre-C1 shape (``start_base + full ltcg_total``, uncapped). The two
    legs are DIFFERENCED into `conversion_ltcg_cost` (the reported marginal
    LTCG cost of the conversion), so leaving one leg uncapped makes the
    subtraction statutorily meaningless: the uncapped base leg overstates base
    LTCG tax whenever ordinary income (with or without the conversion) sits
    below the standard deduction, understating -- or as shown below, fully
    zeroing out via the existing ``max(0.0, ...)`` floor at scenario.py:841 --
    a real marginal cost.

    Fixture: wages 5,000, a small $1,000 Roth conversion (your_ira funds it),
    LTCG 200,000, MFJ ages 61/55 (std deduction only, no senior-bonus noise).
    Both WITH and WITHOUT the $1,000 conversion, ordinary income (6,000 / 5,000)
    stays below the 32,200 standard deduction, so BOTH legs' stack-walk START
    is 0 -- the entire marginal cost comes from the $1,000 of extra "stacking
    room" the conversion consumes out of the unused deduction, taxed at the
    15% LTCG rate: 1,000 * 0.15 = $150.00.
    """

    def test_marginal_ltcg_cost_matches_both_legs_capped_at_total_taxable_income(
        self,
    ) -> None:
        # living_expenses=0.0 disables the IRA-withdrawal-waterfall path (only
        # engaged when yr.income_needed > 0) so the plan's conversion is
        # applied directly, uncapped, matching the simple _project_year path
        # the other tests in this file exercise.
        hh = _minimal_hh(your_ira=10_000.0, living_expenses=0.0)
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=5_000.0, ltcg_ytd=200_000.0)
        plan = ConversionPlan(your_conversions={2026: 1_000.0})

        result = run_scenario(hh, plan, end_age=hh.your_age, ytd=ytd)
        yr = result.years[0]
        assert yr.your_conversion == pytest.approx(1_000.0)  # not clamped by IRA balance

        total_deductions = deductions(
            hh.your_age, hh.spouse_age, STD_DEDUCTION_MFJ, None, filing_status="MFJ", year=2026, cpi=0.0
        )
        assert total_deductions == pytest.approx(32_200.0)
        thresholds = index_tuple(LTCG_THRESHOLDS_MFJ, 2026, 0.0, round50=True)
        ltcg_total = 200_000.0

        # WITH-conversion leg (already fixed at scenario.py:812): capped at
        # TOTAL taxable income (combined_gross + ltcg - deductions).
        with_gross = 5_000.0 + 1_000.0  # wages + conversion
        with_start = max(0.0, with_gross - total_deductions)
        assert with_start == pytest.approx(0.0)  # ordinary still below the deduction
        with_end = max(0.0, with_gross + ltcg_total - total_deductions)
        with_tax = _stack_tax(with_start, with_end, thresholds, LTCG_RATES_MFJ)

        # WITHOUT-conversion (base) leg -- the fix under test: SAME capped shape,
        # built from the SAME without-conversion ordinary gross (wages only).
        base_gross = 5_000.0
        base_start = max(0.0, base_gross - total_deductions)
        assert base_start == pytest.approx(0.0)
        base_end_correct = max(0.0, base_gross + ltcg_total - total_deductions)
        base_tax_correct = _stack_tax(base_start, base_end_correct, thresholds, LTCG_RATES_MFJ)

        expected_marginal_cost = with_tax - base_tax_correct
        assert expected_marginal_cost == pytest.approx(150.0, abs=1.0)  # 1,000 * 15%
        assert expected_marginal_cost >= 0.0

        # Documented for the audit record (NOT asserted against production code,
        # which already floors conversion_ltcg_cost at 0.0 via max(0.0, ...) at
        # scenario.py:841): the PRE-FIX base leg used
        # ``base_start + ltcg_total`` uncapped == 0 + 200,000 = 200,000, taxing
        # 27,200 MORE LTCG in the base leg than the deduction leaves room for.
        # That inflated base tax makes the raw (unclamped) marginal delta
        # NEGATIVE in this fixture -- a spurious "conversion reduces LTCG tax"
        # credit -- which the existing floor then silently zeroes out instead
        # of reporting the real $150.00 cost.
        old_base_end = base_start + ltcg_total
        old_base_tax = _stack_tax(base_start, old_base_end, thresholds, LTCG_RATES_MFJ)
        raw_old_marginal = with_tax - old_base_tax
        assert raw_old_marginal < 0.0  # proves the negative-credit failure mode exists

        assert yr.conversion_ltcg_cost >= 0.0
        assert yr.conversion_ltcg_cost == pytest.approx(expected_marginal_cost, abs=1.0), (
            f"conversion_ltcg_cost={yr.conversion_ltcg_cost:.2f} should be "
            f"{expected_marginal_cost:.2f} (both legs capped at total taxable "
            f"income), not silently floored to $0.00 by an inflated, uncapped "
            f"base leg (raw pre-fix delta would be {raw_old_marginal:.2f})."
        )


class TestSiteBScenarioCompareLTCGEndCappedAtTotalTaxableIncome:
    """engine/scenario_compare.py ~87-88 (survivor_year_tax): same C1-shape
    cap must apply to the survivor's LTCG stack-walk end.

    Fixture: RMD 5,000 (well below the 18,150 Single std ded + senior extra
    for a 70-year-old survivor) + brokerage LTCG 200,000. survivor_ss=0.0
    zeroes taxable SS so gross reduces to just the RMD.
    """

    def test_ltcg_end_capped_when_deductions_exceed_ordinary_income(self) -> None:
        gross = 5_000.0  # rmd + tss(0, since combined_ss=0) + brok_ord_income(0)
        ded = deductions(
            70, 0, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, filing_status="Single", year=2026, cpi=0.0
        )
        assert ded == pytest.approx(18_150.0)  # age 70 >= 65: std ded + one senior extra
        survivor_magi = gross + 200_000.0  # gross + brok_ltcg_income
        bonus = senior_bonus_deduction(70, 0, survivor_magi, year=2026, cpi=0.0, filing_status="Single")
        assert bonus == pytest.approx(0.0)  # MAGI 205,000 fully phases out the $6,000 bonus (ends 175,000)
        ded_total = ded + bonus

        start = max(0.0, gross - ded_total)  # taxable_ordinary, floors to 0
        assert start == pytest.approx(0.0)
        end = max(0.0, gross + 200_000.0 - ded_total)  # C1-shape cap

        thresholds = index_tuple(LTCG_THRESHOLDS_SINGLE, 2026, 0.0, round50=True)
        expected_ltcg_tax = _stack_tax(start, end, thresholds, LTCG_RATES_SINGLE)
        assert expected_ltcg_tax == pytest.approx(20_610.0)  # 137,400 * 0.15
        expected_total_tax = federal_tax_single(start, year=2026, cpi=0.0) + expected_ltcg_tax

        total_tax, _marginal, taxable_ordinary = survivor_year_tax(
            70, 5_000.0, 0.0, year=2026, cpi=0.0, brok_ord_income=0.0, brok_ltcg_income=200_000.0
        )

        assert taxable_ordinary == pytest.approx(start, abs=1.0)
        assert total_tax == pytest.approx(expected_total_tax, abs=1.0), (
            f"total_tax={total_tax:.2f} should be {expected_total_tax:.2f} (LTCG end capped at "
            f"total taxable income {end:.2f}), not the pre-C1 shape (end = taxable_ordinary "
            f"{start:.2f} + full brok_ltcg_income 200,000.00 = {start + 200_000.0:.2f})."
        )

    def test_no_behaviour_change_when_deductions_below_ordinary_income(self) -> None:
        """RMD 50,000 exceeds deductions, so the C1 cap and the pre-C1 shape
        produce the IDENTICAL end -- no regression for the common case."""
        gross = 50_000.0
        survivor_magi = gross + 50_000.0
        bonus = senior_bonus_deduction(70, 0, survivor_magi, year=2026, cpi=0.0, filing_status="Single")
        ded = deductions(
            70, 0, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, filing_status="Single", year=2026, cpi=0.0
        )
        ded_total = ded + bonus
        start = max(0.0, gross - ded_total)
        old_end = start + 50_000.0
        new_end = max(0.0, gross + 50_000.0 - ded_total)
        assert old_end == pytest.approx(new_end)

        thresholds = index_tuple(LTCG_THRESHOLDS_SINGLE, 2026, 0.0, round50=True)
        expected_ltcg_tax = _stack_tax(start, new_end, thresholds, LTCG_RATES_SINGLE)
        expected_total_tax = federal_tax_single(start, year=2026, cpi=0.0) + expected_ltcg_tax

        total_tax, _marginal, taxable_ordinary = survivor_year_tax(
            70, 50_000.0, 0.0, year=2026, cpi=0.0, brok_ord_income=0.0, brok_ltcg_income=50_000.0
        )
        assert taxable_ordinary == pytest.approx(start, abs=1.0)
        assert total_tax == pytest.approx(expected_total_tax, abs=1.0)
