"""audit-0823 scenario/AF-1 + scenario/AF-2 — closed-form conversion sizing
ignores the non-linear feedback the conversion itself creates.

Both findings share one root cause (the audit's root-cause cluster 2): an
amount is sized by a CLOSED-FORM subtraction against a bracket ceiling, using
income and deduction figures measured BEFORE that amount exists. Two things
move once it lands:

  IRC §86 "tax torpedo" — the conversion/withdrawal raises provisional income,
  pushing MORE Social Security into taxability, so ordinary income rises by
  MORE than the amount converted.

  OBBBA senior bonus (IRC §151(d)(5), Pub. L. 119-21 §70103) — the $6,000
  per-person deduction phases out at $0.06 per $1 of MAGI above $150,000 MFJ,
  so deductions SHRINK as the amount grows.

Either way the resulting taxable income overshoots the ceiling being targeted.
The correct primitive already exists and is already used in the same module:
engine.tax.bisect_conversion_for_ceiling (scenario_autofill.py:511).

Every assertion is on OBSERVABLE BEHAVIOUR — the taxable income that
engine.scenario.run_scenario actually reports for the produced plan, versus
the indexed bracket ceiling that plan claimed to fill. Nothing asserts on
internal fields, so these stay valid whatever shape the fix takes.

FIXTURE NOTE — why the households hold a large, inert brokerage balance.
The scenario funds each year's Medicare premiums and taxes before anything
else. With brokerage_start=0 that funding comes from a forced IRA draw
(YearResult.forced_your_ira_draw), which injects ordinary income the autofill
never sized for — measured at $15,333/yr on an otherwise-identical fixture,
which both contaminates the overshoot figure and breaks the autofill's own
OBBBA fixed point. Funding the household from a brokerage balance that is
deliberately inert (GrowthProfile(default_rate=0, yield_rate=0) and
brok_turnover=0, so it throws off no dividends and realizes no gains) drives
every forced draw to exactly $0 and isolates the defect under test. The
zero-Social-Security non-regression case below fills its bracket to the cent,
which is what proves the isolation is clean.

RED-GATE EXPECTATION: the AF-1 and AF-2 classes FAIL against current source.
The non-regression class passes BOTH before and after.
"""

from __future__ import annotations

import pytest

from engine.scenario import run_scenario
from engine.scenario_autofill import (
    add_bracket_fill_withdrawals,
    auto_fill_12,
    auto_fill_22,
    auto_fill_irmaa_safe,
)
from engine.scenario_types import ConversionPlan
from engine.tax import BRACKETS_MFJ
from engine.tax_indexing import index_value
from models.household import GrowthProfile, Household, InheritedIRA

_IDX_12, _IDX_22 = 1, 2

# The bisect primitive converges to well under a dollar, so $1 cleanly separates
# "fills the bracket" from "overshoots the bracket".
_TOL = 1.0

# An inert brokerage: no growth, no yield, no turnover. Exists purely to fund
# premiums/taxes so no forced IRA draw perturbs the measurement. See module
# docstring.
_INERT = GrowthProfile(default_rate=0.0, yield_rate=0.0)


def _ceiling(idx: int, year: int, cpi: float) -> float:
    return index_value(BRACKETS_MFJ[idx][0], year, cpi, round50=True)


def _torpedo_household(**overrides) -> Household:
    """Ages 70, SS claimed, RMDs not yet started.

    Five clean conversion years (2026-2030) in which Social Security is being
    paid but no RMD is forced, so the only thing driving provisional income is
    the conversion the autofill is sizing.
    """
    kwargs = {
        "your_age": 70,
        "spouse_age": 70,
        "your_ss_start_age": 70,
        "spouse_ss_start_age": 70,
        "your_rmd_start_age": 75,
        "spouse_rmd_start_age": 75,
        "your_ss_fra": 2_000.0,
        "spouse_ss_fra": 1_500.0,
        "your_ira": 1_500_000.0,
        "spouse_ira": 1_500_000.0,
        "your_roth": 0.0,
        "spouse_roth": 0.0,
        "brokerage_start": 800_000.0,
        "brokerage_growth": _INERT,
        "brok_turnover": 0.0,
        "living_expenses": 0.0,
        "cpi_assumption": 0.0,
        "grants": [],
        "filing_status": "MFJ",
    }
    kwargs.update(overrides)
    return Household(**kwargs)


def _post_rmd_household(**overrides) -> Household:
    """Ages 76 — past the age-75 RMD start, the only regime in which
    add_bracket_fill_withdrawals does anything.

    IRA balances are deliberately MODEST: at $3M+ the forced RMD alone drives
    provisional income past the point where Social Security is already pinned
    at its 85% cap, which would mask the §86 leg entirely.
    """
    kwargs = {
        "your_age": 76,
        "spouse_age": 76,
        "your_ss_start_age": 70,
        "spouse_ss_start_age": 70,
        "your_rmd_start_age": 75,
        "spouse_rmd_start_age": 75,
        "your_ss_fra": 1_800.0,
        "spouse_ss_fra": 1_400.0,
        "your_ira": 400_000.0,
        "spouse_ira": 400_000.0,
        "your_roth": 0.0,
        "spouse_roth": 0.0,
        "brokerage_start": 800_000.0,
        "brokerage_growth": _INERT,
        "brok_turnover": 0.0,
        "living_expenses": 0.0,
        "cpi_assumption": 0.0,
        "grants": [],
        "filing_status": "MFJ",
    }
    kwargs.update(overrides)
    return Household(**kwargs)


def _acted_years(hh: Household, plan: ConversionPlan, end_age: int = 82):
    """Years in which the strategy actually chose to act.

    A year with no conversion and no extra withdrawal was not sized by the code
    under test, and a forced RMD alone may legitimately exceed any ceiling.
    """
    result = run_scenario(hh, plan, "af-gate", end_age=end_age)
    for yr in result.years:
        acted = (
            yr.your_conversion
            + yr.spouse_conversion
            + yr.extra_withdrawal
            + yr.spouse_extra_withdrawal
        )
        if acted > 0:
            yield yr, acted


def _assert_no_overshoot(hh: Household, plan: ConversionPlan, idx: int, label: str) -> None:
    worst = 0.0
    lines = []
    for yr, acted in _acted_years(hh, plan):
        ceil = _ceiling(idx, yr.year, hh.cpi_assumption)
        over = yr.taxable_income - ceil
        worst = max(worst, over)
        lines.append(
            f"  {yr.year} acted=${acted:,.2f} taxable=${yr.taxable_income:,.2f} "
            f"ceiling=${ceil:,.2f} overshoot=${over:,.2f} "
            f"(combined_ss=${yr.combined_ss:,.2f} taxable_ss=${yr.taxable_ss_amt:,.2f} "
            f"deductions=${yr.total_deductions:,.2f})"
        )
    assert lines, f"{label}: no acted-on year — fixture is not exercising the code"
    assert worst <= _TOL, (
        f"{label}: sized amount overshoots its own target ceiling by "
        f"${worst:,.2f}\n" + "\n".join(lines)
    )


def _assert_fills_exactly(hh: Household, plan: ConversionPlan, idx: int, label: str) -> None:
    judged = 0
    for yr, _acted in _acted_years(hh, plan):
        ceil = _ceiling(idx, yr.year, hh.cpi_assumption)
        judged += 1
        assert abs(yr.taxable_income - ceil) <= _TOL, (
            f"{label}: {yr.year} taxable=${yr.taxable_income:,.2f} should fill "
            f"ceiling=${ceil:,.2f} exactly (delta ${yr.taxable_income - ceil:,.2f}); "
            f"the closed form was already correct here and must not change"
        )
    assert judged, f"{label}: no acted-on year — fixture not exercising the code"


# ---------------------------------------------------------------------------
# AF-1
# ---------------------------------------------------------------------------


class TestAF1ClosedFormBracketRoomIgnoresSSTorpedo:
    """engine/scenario_autofill.py:434 (auto_fill_12), :451 (auto_fill_22),
    :535 (the 22% cap leg inside auto_fill_irmaa_safe).

    room_to_12/22 both reduce to engine/tax.py:284-291

        room_to_bracket = max(total_deductions + bracket_ceiling - current_gross, 0)

    where `current_gross` is scenario_autofill.py's `fixed_gross`, carrying
    taxable SS computed at the PRE-conversion base (:272, :283). Converting that
    room raises provisional income, more SS becomes taxable, and actual taxable
    income lands above the ceiling.

    auto_fill_24 (:551) is deliberately NOT tested here. run_scenario applies a
    default room_22 conversion cap, so a 24% plan is clipped to the 22% bracket
    before it can overshoot its own 24% ceiling — the defect is real at that
    site but unobservable through this interface. Reported separately.
    """

    def test_auto_fill_12_does_not_overshoot_12pct_ceiling(self):
        hh = _torpedo_household()
        _assert_no_overshoot(hh, auto_fill_12(hh), _IDX_12, "auto_fill_12")

    def test_auto_fill_22_does_not_overshoot_22pct_ceiling(self):
        hh = _torpedo_household()
        _assert_no_overshoot(hh, auto_fill_22(hh), _IDX_22, "auto_fill_22")

    def test_auto_fill_irmaa_safe_does_not_overshoot_its_22pct_cap(self):
        """The :535 cap leg — min(bisected_irmaa_room, room_to_22(...)).

        REACHABILITY: the un-bisected 22% cap binds only when

            deductions + bracket_22_ceiling(yr) < irmaa_tier1_threshold(yr+2)

        At statutory 2026 values that is 32,200 + 211,400 = 243,600 against
        218,000, so the IRMAA leg — which IS already bisected — binds first and
        the cap is inert. Because the IRMAA tier is indexed at yr+2 while the
        bracket is indexed at yr, a high enough CPI flips it. Measured directly:
        at cpi=0.0 this household does NOT overshoot (it runs $32,740 UNDER the
        22% ceiling); at cpi=0.08 it does. So this is a latent trap reachable
        only through a user-set high CPI, not a defect at default settings —
        which is worth fixing but must not be reported as live breakage.
        """
        hh = _torpedo_household(cpi_assumption=0.08)
        _assert_no_overshoot(hh, auto_fill_irmaa_safe(hh), _IDX_22, "auto_fill_irmaa_safe")


# ---------------------------------------------------------------------------
# AF-2
# ---------------------------------------------------------------------------


class TestAF2BracketFillFreezesDeductionsAndTaxableSS:
    """engine/scenario_autofill.py:693

        room = max(yr.total_deductions + bracket_ceiling - yr.combined_gross, 0)

    `yr` is a YearResult from a run_scenario over the BASE plan, so BOTH inputs
    are stale with respect to the withdrawal being sized:

      leg 1  yr.total_deductions carries senior_bonus_deduction priced at the
             pre-withdrawal yr.magi (engine/scenario.py:661-665)
      leg 2  yr.combined_gross carries yr.taxable_ss_amt, computed from an
             other_inc that includes only the BASE plan's extra_withdrawal —
             zero in these years, since this function is what adds it
             (engine/scenario_compute.py:371-388, :427)

    Reachable from the UI: engine/scenario_compare.py:323 inside build_scenario(),
    driven by views/comparator.py:81 (Scenario Comparator).
    """

    def test_leg1_obbba_phaseout_only(self):
        """SS zeroed, so ONLY the senior-bonus phase-out can move.

        Filling to 22% drives MAGI into the $150K-$250K per-person phase-out
        corridor, so the bonus deduction the room was priced with evaporates.
        """
        hh = _post_rmd_household(your_ss_fra=0.0, spouse_ss_fra=0.0)
        plan = add_bracket_fill_withdrawals(hh, ConversionPlan(), target_bracket=0.22)
        _assert_no_overshoot(hh, plan, _IDX_22, "AF-2 leg1 (OBBBA phase-out)")

    def test_leg2_ss_torpedo_only(self):
        """SS in the §86 band, filling only to 12% so MAGI stays below the
        $150K OBBBA phase-out start and the deduction leg cannot move."""
        hh = _post_rmd_household()
        plan = add_bracket_fill_withdrawals(hh, ConversionPlan(), target_bracket=0.12)
        _assert_no_overshoot(hh, plan, _IDX_12, "AF-2 leg2 (§86 torpedo)")

    def test_both_legs_together(self):
        hh = _post_rmd_household()
        plan = add_bracket_fill_withdrawals(hh, ConversionPlan(), target_bracket=0.22)
        _assert_no_overshoot(hh, plan, _IDX_22, "AF-2 both legs")


# ---------------------------------------------------------------------------
# NON-REGRESSION — must pass BEFORE and AFTER the fix
# ---------------------------------------------------------------------------


class TestNonRegressionClosedFormStillCorrectWhereItAlreadyWas:
    """Where no feedback term exists, the closed form is exactly right.

    These pin the invariant as "fills the bracket EXACTLY" rather than as a
    hard-coded dollar figure, so they bite in both directions: a fix that
    under-converts (leaving room on the table) fails as loudly as one that
    overshoots. If any of these turn red, the fix is over-broad — it is
    changing plans that were already correct.
    """

    @pytest.mark.parametrize(
        ("strategy", "idx", "label"),
        [
            (auto_fill_12, _IDX_12, "auto_fill_12"),
            (auto_fill_22, _IDX_22, "auto_fill_22"),
        ],
    )
    def test_zero_social_security_plans_unchanged(self, strategy, idx, label):
        """No Social Security at all: the torpedo term is identically zero, so
        bisecting must agree with the closed form to the cent."""
        hh = _torpedo_household(your_ss_fra=0.0, spouse_ss_fra=0.0)
        _assert_fills_exactly(hh, strategy(hh), idx, f"{label} (zero SS)")

    def test_social_security_already_at_85pct_cap_plans_unchanged(self):
        """An inherited-IRA drain pins taxable SS at its 85% ceiling BEFORE any
        conversion (IRC §86 caps inclusion at 0.85 x benefit), so the conversion
        adds no further taxable SS and the closed form is again exactly right.

        Small benefits keep the 0.85 cap low enough to be reached by the drain
        alone, and the 12% target keeps MAGI under $150K so the OBBBA leg also
        stays still — isolating "no feedback of either kind".
        """
        hh = _torpedo_household(
            your_ss_fra=500.0,
            spouse_ss_fra=400.0,
            inherited_iras=[
                InheritedIRA(balance=500_000.0, inherited_year=2026, owner="you")
            ],
        )
        _assert_fills_exactly(hh, auto_fill_12(hh), _IDX_12, "auto_fill_12 (SS at 85% cap)")

    def test_income_already_above_ceiling_yields_empty_fill(self):
        """RMDs alone past the 22% ceiling: room is 0, so
        add_bracket_fill_withdrawals must add nothing at all."""
        hh = _post_rmd_household(your_ira=6_000_000.0, spouse_ira=6_000_000.0)
        plan = add_bracket_fill_withdrawals(hh, ConversionPlan(), target_bracket=0.22)
        assert not plan.extra_withdrawals, (
            f"expected no extra withdrawals when income already exceeds the ceiling, "
            f"got {plan.extra_withdrawals}"
        )
        assert not plan.spouse_extra_withdrawals, (
            f"expected no spouse extra withdrawals, got {plan.spouse_extra_withdrawals}"
        )
