"""TDD tests: ACA premiums and IRMAA surcharges must reach CASH FLOW.

Before this change ``yr.aca_loss`` and ``yr.irmaa_cost`` fed only the DISPLAY
metric ``yr.all_in_cost`` (engine/scenario.py:876). The single path that
touched a balance was ``yr.aca_clawback`` -> ``federal_tax_amt``
(engine/scenario.py:768), and that is gated on
``Household.advance_aptc_annual > 0``, which defaults to 0.0. So the entire
healthcare side of the model could not move terminal net worth: a household
that loses every dollar of ACA subsidy ended the projection exactly as rich
as one that kept it.

Same defect class as the unfunded living expenses fixed in PR #429.

The funded quantity is the household's REAL out-of-pocket premium, not the
subsidy delta::

    aca_premium_cost = max(aca_net_cost(aca_magi) - aca_clawback, 0)

which composes exactly with the existing clawback path:

* ``advance_aptc == 0`` -> clawback 0 -> fund ``benchmark - PTC``, so the
  refundable PTC is credited (it previously was not: the clawback gate
  suppresses the negative/refund direction entirely).
* ``advance_aptc > 0``  -> clawback ``(advance_aptc - PTC)`` is already in
  ``federal_tax_amt``, so this leaves ``benchmark - advance_aptc``, the true
  in-year premium outflow.

Total healthcare outflow is ``benchmark - PTC`` either way, counted once.

``aca_loss`` deliberately REMAINS a display-only DELTA metric inside
``all_in_cost`` -- the cash effect emerges from two scenarios differing,
which is exactly what makes the comparison honest.

IRMAA needs no within-year feedback term: ``irmaa_for_year`` reads MAGI from
two years earlier (the statutory lookback), so this year's waterfall draw
cannot move this year's surcharge. It must still be FUNDED in the year it is
charged.
"""

from __future__ import annotations

import pytest

from engine.aca import (
    aca_applies,
    aca_net_cost,
    effective_benchmark_premium,
    resolve_couple_benchmark_annual,
)
from engine.scenario import ConversionPlan, run_scenario
from engine.scenario_types import ScenarioResult
from models.household import Household


def approx(expected: float, tol: float = 0.01) -> object:
    return pytest.approx(expected, abs=tol)


def _aca_household(**overrides: object) -> Household:
    """MFJ household aged into the ACA window (61) with every income source
    zeroed except living expenses, so the ONLY way to fund the year is a
    waterfall draw from the IRAs. growth_rate/brok_turnover/expense_inflation
    are 0.0 so brokerage growth, dividends and realized LTCG are exactly zero
    and multi-year arithmetic stays hand-checkable.

    ACA enrollment is ON by default here -- ``your_aca_enrolled`` and
    ``spouse_aca_enrolled`` both default to False on Household, so the ACA
    page is inert out of the box and every ACA test must opt in explicitly.
    """
    base: dict[str, object] = {
        "grants": [],
        "your_age": 61,
        "spouse_age": 61,
        "your_ira": 1_000_000.0,
        "spouse_ira": 1_000_000.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "filing_status": "MFJ",
        "base_year": 2026,
        "growth_rate": 0.0,
        "brok_turnover": 0.0,
        "expense_inflation": 0.0,
        "brokerage_start": 0.0,
        "living_expenses": 60_000.0,
        "your_aca_enrolled": True,
        "spouse_aca_enrolled": True,
    }
    base.update(overrides)
    return Household(**base)  # type: ignore[arg-type]


def _terminal_wealth(res: ScenarioResult) -> float:
    """Total household wealth at the end of the final projected year."""
    last = res.years[-1]
    return (
        last.your_ira_end
        + last.spouse_ira_end
        + last.your_roth_end
        + last.spouse_roth_end
        + last.brokerage_balance_end
        + last.your_inherited_balance_end
        + last.spouse_inherited_balance_end
    )


def _expected_premium(hh: Household, yr: object) -> float:
    """Independently recompute the year's out-of-pocket ACA premium from
    engine/aca.py primitives -- deliberately NOT by calling the engine helper
    under a different name."""
    ya = yr.your_age  # type: ignore[attr-defined]
    sa = yr.spouse_age  # type: ignore[attr-defined]
    status = yr.filing_status or hh.filing_status  # type: ignore[attr-defined]
    your_on = aca_applies(ya, hh.your_aca_enrolled)
    spouse_on = aca_applies(sa, hh.spouse_aca_enrolled)
    if not (your_on or spouse_on):
        return 0.0
    couple = resolve_couple_benchmark_annual(
        hh.aca_benchmark_premium_annual,
        your_age=ya,
        spouse_age=sa,
        filing_status=status,
        year=yr.year,  # type: ignore[attr-defined]
        cpi=hh.cpi_assumption,
    )
    effective = effective_benchmark_premium(
        couple,
        your_age=ya,
        your_on_aca=your_on,
        spouse_age=sa,
        spouse_on_aca=spouse_on,
        filing_status=status,
    )
    net = aca_net_cost(
        yr.aca_magi,  # type: ignore[attr-defined]
        effective,
        hh.aca_enhanced_subsidies_active,
        status,
        year=yr.year,  # type: ignore[attr-defined]
        cpi=hh.cpi_assumption,
    )
    return max(net - yr.aca_clawback, 0.0)  # type: ignore[attr-defined]


class TestHealthcareReachesCashFlow:
    """The defect in one assertion: enrolling in ACA coverage must cost the
    household money by the end of the projection."""

    def test_aca_enrollment_lowers_terminal_wealth(self) -> None:
        """Two identical households, one enrolled on the marketplace and one
        not. The enrolled one pays real premiums out of the IRA every year,
        so it MUST end poorer.

        Pre-fix the two terminal figures are bit-for-bit identical, because
        aca_loss never touches a balance.
        """
        plan = ConversionPlan()
        enrolled = run_scenario(_aca_household(), plan, "aca-on", end_age=64)
        unenrolled = run_scenario(
            _aca_household(your_aca_enrolled=False, spouse_aca_enrolled=False),
            plan,
            "aca-off",
            end_age=64,
        )

        premiums = sum(yr.aca_premium_cost for yr in enrolled.years)
        assert premiums > 0.0, "fixture did not actually put anyone on the marketplace"

        gap = _terminal_wealth(unenrolled) - _terminal_wealth(enrolled)
        assert gap > 0.0, (
            f"Enrolled household ended with the SAME wealth as the unenrolled "
            f"one (gap={gap:.2f}) despite paying {premiums:.2f} of ACA premiums "
            f"-- the premium never reaches cash flow."
        )
        # The gap is the premiums themselves plus the tax on the extra IRA
        # dollars drawn to pay them, so it is bounded BELOW by the premiums.
        assert gap >= premiums - 0.01

    def test_zero_benchmark_override_is_exactly_inert(self) -> None:
        """``aca_benchmark_premium_annual=0.0`` is an explicit household
        override meaning "no ACA premium exposure modeled" (distinct from
        None, which derives). It must be EXACTLY as inert as not enrolling --
        the zero-decision-variable invariant."""
        plan = ConversionPlan()
        zero_benchmark = run_scenario(
            _aca_household(aca_benchmark_premium_annual=0.0),
            plan,
            "aca-zero",
            end_age=64,
        )
        unenrolled = run_scenario(
            _aca_household(your_aca_enrolled=False, spouse_aca_enrolled=False),
            plan,
            "aca-off",
            end_age=64,
        )

        assert all(yr.aca_premium_cost == 0.0 for yr in zero_benchmark.years)
        assert _terminal_wealth(zero_benchmark) == approx(_terminal_wealth(unenrolled))

    def test_medicare_age_household_has_no_aca_premium(self) -> None:
        """Nobody under 65 -> nobody on the marketplace -> zero premium in
        every year, even with enrollment flags left on."""
        plan = ConversionPlan()
        res = run_scenario(
            _aca_household(your_age=66, spouse_age=66),
            plan,
            "medicare",
            end_age=70,
        )
        assert all(yr.aca_premium_cost == 0.0 for yr in res.years)


class TestPremiumFormula:
    """The funded amount is the real out-of-pocket premium, and it is counted
    exactly once regardless of how much of it was pre-paid as advance APTC."""

    def test_premium_equals_net_cost_minus_clawback(self) -> None:
        hh = _aca_household()
        res = run_scenario(hh, ConversionPlan(), "formula", end_age=64)
        for yr in res.years:
            assert yr.aca_premium_cost == approx(_expected_premium(hh, yr)), (
                f"year {yr.year}: premium {yr.aca_premium_cost:.2f} != "
                f"aca_net_cost - clawback {_expected_premium(hh, yr):.2f}"
            )

    @pytest.mark.parametrize("advance_aptc", [0.0, 5_000.0, 15_000.0])
    def test_advance_aptc_does_not_double_count(self, advance_aptc: float) -> None:
        """Total healthcare outflow (in-year premium + Form 8962 reconciliation)
        must equal ``benchmark - PTC`` no matter how the household split it
        between monthly premiums and advance credit. Pre-paying more APTC
        shifts cost from ``aca_premium_cost`` into ``aca_clawback``; it must
        not create or destroy any."""
        hh = _aca_household(advance_aptc_annual=advance_aptc)
        res = run_scenario(hh, ConversionPlan(), f"aptc-{advance_aptc:.0f}", end_age=64)
        for yr in res.years:
            if yr.aca_premium_cost == 0.0 and yr.aca_clawback == 0.0:
                continue
            total_outflow = yr.aca_premium_cost + yr.aca_clawback
            # _expected_premium already nets the clawback back out, so adding
            # it returns the gross net-of-subsidy premium.
            expected = _expected_premium(hh, yr) + yr.aca_clawback
            assert total_outflow == approx(expected)


class TestIrmaaFunded:
    """IRMAA is a surcharge ABOVE the base Part B/D premium (engine/irmaa.py:130
    subtracts base_part_b), so funding it is purely additive -- it cannot
    double-count a standard premium already inside living_expenses."""

    def test_irmaa_surcharge_is_inside_the_funded_need(self) -> None:
        """A household with MAGI high enough to trigger IRMAA must have the
        surcharge inside the cash it needs to raise that year."""
        plan = ConversionPlan()
        # Ages 67 -> Medicare, past the 2-year lookback, so irmaa_cost is live.
        rich = run_scenario(
            _aca_household(your_age=67, spouse_age=67, living_expenses=400_000.0),
            plan,
            "irmaa-high",
            end_age=72,
        )
        surcharges = sum(yr.irmaa_cost for yr in rich.years)
        assert surcharges > 0.0, "fixture did not trigger any IRMAA surcharge"

        poor = run_scenario(
            _aca_household(your_age=67, spouse_age=67, living_expenses=30_000.0),
            plan,
            "irmaa-low",
            end_age=72,
        )
        assert sum(yr.irmaa_cost for yr in poor.years) == approx(0.0)

        for yr in rich.years:
            if yr.irmaa_cost > 0.0:
                assert yr.income_needed >= yr.irmaa_cost, (
                    f"year {yr.year}: income_needed {yr.income_needed:.2f} does "
                    f"not even cover the IRMAA surcharge {yr.irmaa_cost:.2f}"
                )

    def test_no_irmaa_before_medicare_is_inert(self) -> None:
        res = run_scenario(
            _aca_household(
                your_age=55,
                spouse_age=55,
                your_aca_enrolled=False,
                spouse_aca_enrolled=False,
            ),
            ConversionPlan(),
            "pre-medicare",
            end_age=60,
        )
        assert all(yr.irmaa_cost == 0.0 for yr in res.years)


class TestCliffGrossUp:
    """A waterfall draw that pushes ACA MAGI across the 400%-FPL cliff makes
    the premium jump by the entire subsidy. The solver must gross the draw up
    to cover that jump, exactly as it already grosses up for marginal tax."""

    def test_draw_crossing_aca_cliff_leaves_no_unfunded_need(self) -> None:
        # 400% FPL for MFJ is ~4 x 21,150 = ~84,600 (indexed). With no other
        # income the zero-draw ACA MAGI is 0, and a draw funding ~$95k of
        # expenses lands MAGI well above the cliff.
        hh = _aca_household(living_expenses=95_000.0)
        res = run_scenario(hh, ConversionPlan(), "cliff", end_age=64)

        engaged = [
            yr for yr in res.years if yr.aca_loss > 0.0 or yr.aca_premium_cost > 0.0
        ]
        assert engaged, "fixture never engaged ACA at all"

        for yr in res.years:
            assert yr.unfunded_need == approx(0.0), (
                f"year {yr.year}: {yr.unfunded_need:.2f} left unfunded with "
                f"$2M of IRA available -- the solver did not gross the draw up "
                f"for the ACA premium it triggered."
            )


class TestHouseholdShapes:
    """Structural, so it must hold across the input space -- not just one
    household (see the waterfall-activation lesson: parametrize over shapes,
    never verify an engine on a single plan)."""

    @pytest.mark.parametrize(
        "shape",
        [
            {},
            {"brokerage_start": 750_000.0},
            {"filing_status": "Single", "spouse_ira": 0.0, "spouse_aca_enrolled": False},
            {"your_ss_fra": 30_000.0, "spouse_ss_fra": 24_000.0},
            {"your_ira": 120_000.0, "spouse_ira": 0.0},
        ],
        ids=["default", "large-brokerage", "single", "with-ss", "small-ira"],
    )
    def test_enrollment_never_increases_terminal_wealth(
        self, shape: dict[str, object]
    ) -> None:
        """Paying premiums can never make a household RICHER. Weak inequality
        so shapes where ACA is inert (Single past 65, tiny IRA fully drained)
        still pass, but the direction can never invert."""
        plan = ConversionPlan()
        on = run_scenario(_aca_household(**shape), plan, "on", end_age=64)
        off_shape = dict(shape)
        off_shape.update({"your_aca_enrolled": False, "spouse_aca_enrolled": False})
        off = run_scenario(_aca_household(**off_shape), plan, "off", end_age=64)

        assert _terminal_wealth(on) <= _terminal_wealth(off) + 0.01
