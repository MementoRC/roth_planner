"""audit-0823 scenario/SC-1 — realized YTD NQO exercises must never make the
household's spendable cash go DOWN relative to a lighter realization.

engine/scenario.py's ``_project_year`` floors ``option_income_bounded =
max(yr.option_income, nqo_exercise_ytd)`` so realized-exceeds-scheduled NQO
income is not lost from gross income / SS provisional income / MAGI (line
~436). ``yr.federal_tax_amt`` (subtracted from ``available_income`` at line
~931) is computed on that BOUNDED figure. But the credit side of
``available_income`` (line ~924) adds the RAW ``yr.option_income`` (the
scheduled forecast), and the YTD cash add-back block (lines ~952-958) restores
wages/NEC/interest/STCG/IRA-distribution-excess but NOT ``nqo_exercise_ytd``.
Net effect: the household is taxed on the realized amount but credited only
the scheduled amount whenever realized YTD NQO EXCEEDS the schedule -- a
phantom shortfall that inflates ``income_needed`` / deflates ``excess_rmd``.

FIXTURE SHAPE (read before touching this file):
  - ``YTDSnapshot.magi_ytd`` (models/ytd_income.py) is a computed property
    that ALREADY includes ``nqo_exercise_ytd`` in its sum. ``compute_magi``
    (engine/scenario_compute.py) adds ``option_income_for_magi`` (=
    ``option_income_bounded - nqo_exercise_ytd``, i.e. 0 whenever realized
    >= scheduled) PLUS ``ytd_year.magi_ytd`` -- so setting only
    ``nqo_exercise_ytd`` on the snapshot (leaving every other YTD field at
    its 0.0 default) does NOT double-count: MAGI picks it up exactly once,
    via the ``magi_ytd`` property. No other snapshot field needs touching to
    isolate this one dimension.
  - The two runs below share one household and one ``ConversionPlan`` and
    differ ONLY in ``ytd.nqo_exercise_ytd`` (0.0 vs 200,000.0).
  - ``grants=[]`` on the household makes ``hh.option_income(year) == 0.0``
    (the SCHEDULED figure) for every year -- asserted explicitly below so the
    realized-exceeds-scheduled precondition can never silently stop holding.
  - ``your_ss_fra=0.0`` / ``spouse_ss_fra=0.0`` zero out Social Security
    entirely, so the only "other" income source is a plan
    ``extra_withdrawals`` voluntary IRA draw. That draw is sized (300,000)
    comfortably above ``living_expenses`` (50,000) plus any plausible federal
    tax delta from the extra 200,000 of ordinary income, so BOTH runs clear
    the ``max(..., 0)`` floors on ``excess_rmd``/``income_needed`` and no
    within-year IRA-withdrawal-waterfall solver is triggered
    (``income_needed`` stays 0 pre-fix and post-fix alike) -- keeping the
    algebra in the tight magnitude test exact rather than floor-clipped.
  - Both spouses are under 65 (IRMAA) and neither is ACA-enrolled (ACA), so
    ``irmaa_cost == aca_premium_cost == 0`` in both runs: the ONLY cost term
    in ``available_income`` that differs between run A and run B is
    ``federal_tax_amt``. That is what makes the magnitude test's algebraic
    identity hold exactly (up to the tolerance floor).
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_scenario
from models.household import Household
from models.ytd_income import YTDSnapshot

_BASE_YEAR = 2026
_REALIZED_NQO = 200_000.0


def _household() -> Household:
    return Household(
        your_age=61,
        spouse_age=59,
        base_year=_BASE_YEAR,
        cpi_assumption=0.0,
        your_ira=500_000.0,
        spouse_ira=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        living_expenses=50_000.0,
        grants=[],  # scheduled option income == 0.0 for every year
    )


def _plan() -> ConversionPlan:
    # A voluntary IRA draw unrelated to option income, sized well above
    # living_expenses + any plausible tax delta from the extra 200K of
    # ordinary income, so excess_rmd/income_needed never hit their floors
    # in either run and the waterfall solver never activates.
    return ConversionPlan(extra_withdrawals={_BASE_YEAR: 300_000.0})


def _run(nqo_exercise_ytd: float):
    hh = _household()
    plan = _plan()
    ytd = YTDSnapshot(tax_year=_BASE_YEAR, nqo_exercise_ytd=nqo_exercise_ytd)
    result = run_scenario(hh, plan, ytd=ytd, end_age=hh.your_age)
    assert len(result.years) == 1, "fixture must project exactly the base year"
    return hh, result.years[0]


class TestSC1RealizedNqoCashCredit:
    """Realizing more NQO exercise income cannot make spendable cash go down."""

    def test_precondition_realized_exceeds_scheduled(self) -> None:
        hh = _household()
        scheduled = hh.option_income(_BASE_YEAR)
        assert scheduled == 0.0, (
            f"fixture precondition broken: scheduled option_income={scheduled}, "
            "expected 0.0 (grants=[]) so realized YTD NQO strictly exceeds it"
        )
        assert scheduled < _REALIZED_NQO, (
            "fixture precondition broken: realized YTD NQO must exceed the "
            "household's scheduled option income to exercise the SC-1 defect"
        )

    def test_excess_rmd_does_not_decrease_when_realized_nqo_exceeds_schedule(self) -> None:
        """Differential invariant: B (realized 200K NQO) must not leave the
        household with LESS spendable cash than A (realized 0 NQO), since B
        strictly dominates A in real income received.

        RED today: option_income_bounded (used for tax) floors to the
        realized 200K in run B, but available_income's credit side still
        adds only the raw (scheduled, 0) option_income and the YTD add-back
        list omits nqo_exercise_ytd -- so run B pays MORE federal tax than
        run A while being credited NO additional spendable cash, making
        excess_rmd_B < excess_rmd_A instead of >.
        """
        hh_a, yr_a = _run(0.0)
        hh_b, yr_b = _run(_REALIZED_NQO)

        # Sanity: neither run should be shortfall-floored (see module docstring).
        assert yr_a.income_needed == 0.0
        assert yr_b.income_needed == 0.0

        assert yr_b.excess_rmd > yr_a.excess_rmd, (
            f"realizing an extra ${_REALIZED_NQO:,.0f} of NQO income made spendable "
            f"cash WORSE, not better: excess_rmd_A={yr_a.excess_rmd:,.2f}, "
            f"excess_rmd_B={yr_b.excess_rmd:,.2f} (federal_tax_amt_A="
            f"{yr_a.federal_tax_amt:,.2f}, federal_tax_amt_B={yr_b.federal_tax_amt:,.2f})"
        )

    def test_excess_rmd_delta_equals_realized_nqo_minus_incremental_tax(self) -> None:
        """Tight magnitude pin: the ONLY thing that should change between run A
        and run B is (a) the household is credited the full realized NQO cash,
        and (b) it pays the incremental federal tax on that cash. So:

            excess_rmd_B - excess_rmd_A == REALIZED_NQO - (tax_B - tax_A)

        RED today: the realized cash is never credited anywhere (see module
        docstring), so the actual LHS is -(tax_B - tax_A) -- short of the
        expected RHS by exactly REALIZED_NQO ($200,000).
        """
        _, yr_a = _run(0.0)
        _, yr_b = _run(_REALIZED_NQO)

        delta_excess_rmd = yr_b.excess_rmd - yr_a.excess_rmd
        delta_federal_tax = yr_b.federal_tax_amt - yr_a.federal_tax_amt
        expected_delta = _REALIZED_NQO - delta_federal_tax

        assert delta_excess_rmd == pytest.approx(expected_delta, abs=1.0), (
            f"delta_excess_rmd={delta_excess_rmd:,.2f} != expected="
            f"{expected_delta:,.2f} (REALIZED_NQO={_REALIZED_NQO:,.2f} - "
            f"delta_federal_tax={delta_federal_tax:,.2f}); gap="
            f"{expected_delta - delta_excess_rmd:,.2f}"
        )
