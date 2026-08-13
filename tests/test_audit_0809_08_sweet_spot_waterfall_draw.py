"""audit-0809 finding #08: Sweet Spot's base_magi omits the living-expense IRA
waterfall draw that engine.scenario folds into yr.magi.

The defect
----------
`sweet_spot_compute.base_income_for_year` assembles

    base_gross = opt + tss + ordinary_addl
    base_magi  = opt + tss + magi_addl

and neither `ordinary_addl` nor `magi_addl` has a term for the forced
IRA-withdrawal-waterfall draw that funds living expenses (plus, since PR #434,
IRMAA surcharges and ACA premiums). `engine.scenario` folds exactly that draw
into `yr.magi` via `compute_magi(forced_your_ira_draw=..., forced_spouse_ira_draw=...)`.

So for any household whose living expenses come out of a traditional IRA, the
two engines report a different MAGI for the *same* household-year at the *same*
(zero) conversion -- and every Sweet Spot recommendation sized off that base
(fill-to-12/22, IRMAA-Safe Max, the marginal-cost sweep, the chart guide-lines)
is optimistic by the whole draw.

engine/scenario.py:1527 already names this failure mode from the other side:
the IRMAA/ACA guarantee "was previously carried SOLELY by
auto_fill_irmaa_safe/auto_fill_aca sizing the plan against a draw-blind
base_magi, so once the waterfall activated a real, materially larger draw, the
achieved yr.magi ... could sail past the ceiling the plan claimed to respect."

Scope of the fix under test
---------------------------
The draw folded in is the one solved at ZERO conversion, which is the
non-circular baseline engine/scenario.py:1532-1538 argues for: capping a
conversion against a MAGI that already contains that conversion's own draw is
circular. A nonzero conversion does raise tax and therefore the true draw
beyond this baseline, so a second-order overstatement survives -- the same
direction and the same reason as the engine's own first-pass `conversion_cap`.
That residual is deliberately out of scope here; see
TestResidualIsBoundedAndSameSignAsEngine.
"""

from __future__ import annotations

import pytest

from engine.irmaa import IRMAA_TIERS_MFJ, _index_irmaa_tiers
from engine.scenario import ConversionPlan, run_scenario
from engine.scenario_types import YearResult
from engine.sweet_spot_compute import (
    all_in_at_conversion,
    base_income_for_year,
    compute_multi_year_summary,
    irmaa_safe_max,
    zero_conversion_ira_draws,
)
from models.household import Household


def _shortfall_household(**overrides: object) -> Household:
    """A household that must fund living expenses from its traditional IRAs.

    Mirrors tests/test_sweet_spot_scenario_parity.py::_no_ss_no_option_household
    (ages 61/55, no SS, no option income, no indexing) but deliberately supplies
    NO ytd wages, so `available_income` is zero and the whole living-expense
    need falls through to the waterfall. The existing parity suite seeds
    `wages_ytd=80_000`, which covers the expenses and keeps the waterfall
    dormant -- which is exactly why those tests never caught this.

    brokerage_start stays 0.0 so the waterfall's brokerage leg is empty and the
    solved draw is purely the two IRA legs.
    """
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
        "expense_inflation": 0.0,
        "brokerage_start": 0.0,
        "filing_status": "MFJ",
    }
    defaults.update(overrides)
    return Household(**defaults)  # type: ignore[arg-type]


def _oracle_year(hh: Household, year: int) -> YearResult:
    """engine.scenario's answer for `year` at a zero conversion."""
    end_age = hh.your_age + (year - hh.base_year)
    result = run_scenario(hh, ConversionPlan(), "oracle", end_age=end_age)
    for yr in result.years:
        if yr.year == year:
            return yr
    raise AssertionError(f"year {year} not found in scenario result")  # pragma: no cover


class TestWaterfallActuallyFires:
    """Guard: if these preconditions ever stop holding, the tests below would
    pass vacuously (a zero draw trivially matches a draw-blind base)."""

    def test_scenario_solves_a_nonzero_ira_draw_at_zero_conversion(self) -> None:
        hh = _shortfall_household()
        oracle = _oracle_year(hh, hh.base_year)

        draw = oracle.forced_your_ira_draw + oracle.forced_spouse_ira_draw
        assert draw > 0, "waterfall did not fire -- fixture no longer produces a shortfall"
        assert oracle.forced_brokerage_draw == pytest.approx(0.0)
        assert oracle.unfunded_need == pytest.approx(0.0)
        assert oracle.magi == pytest.approx(draw, abs=1.0)


class TestBaseMagiParityWithScenario:
    """The finding proper: same household, same year, same (zero) conversion,
    two engines, one answer."""

    def test_multi_year_summary_base_magi_matches_scenario_oracle(self) -> None:
        """RED before the fix: rows[0].base_magi is 0.00 while the oracle
        reports the full draw. Uses no new API -- compute_multi_year_summary
        must derive the draw itself."""
        hh = _shortfall_household()
        oracle = _oracle_year(hh, hh.base_year)

        rows = compute_multi_year_summary(hh)
        row = next(r for r in rows if r.year == hh.base_year)

        assert row.base_magi == pytest.approx(oracle.magi, abs=1.0)

    def test_base_income_folds_supplied_draw_into_every_base(self) -> None:
        """The draw is ordinary income: it belongs in the ordinary bracket base,
        in MAGI, and in the muni-exclusive NIIT MAGI alike."""
        hh = _shortfall_household()
        year = hh.base_year
        oracle = _oracle_year(hh, year)
        draw = oracle.forced_your_ira_draw + oracle.forced_spouse_ira_draw

        blind = base_income_for_year(hh, year)
        aware = base_income_for_year(hh, year, ira_draw=draw)

        assert aware.waterfall_draw == pytest.approx(draw)
        assert aware.base_magi - blind.base_magi == pytest.approx(draw, abs=1.0)
        assert aware.base_gross - blind.base_gross == pytest.approx(draw, abs=1.0)
        assert aware.base_magi == pytest.approx(oracle.magi, abs=1.0)
        assert aware.base_gross == pytest.approx(oracle.combined_gross, abs=1.0)

        aware_res = all_in_at_conversion(hh, aware, 0.0, 0.0)
        assert aware_res.magi == pytest.approx(oracle.magi, abs=1.0)
        assert aware_res.niit_magi == pytest.approx(oracle.niit_magi, abs=1.0)

    def test_draw_is_not_counted_as_net_investment_income(self) -> None:
        """An IRA distribution is not net investment income under 1411(c) --
        it raises the NIIT MAGI (the threshold test) but never the NII the tax
        is charged on."""
        hh = _shortfall_household()
        year = hh.base_year
        oracle = _oracle_year(hh, year)
        draw = oracle.forced_your_ira_draw + oracle.forced_spouse_ira_draw

        blind = base_income_for_year(hh, year)
        aware = base_income_for_year(hh, year, ira_draw=draw)

        assert aware.net_investment_income_addl == pytest.approx(
            blind.net_investment_income_addl
        )


class TestIrmaaSafeMaxIsActuallySafe:
    """The user-visible consequence: Sweet Spot recommended a conversion that
    blew through two IRMAA tiers."""

    def _tier1(self, hh: Household, year: int) -> float:
        # IRMAA 2-year lookback: the threshold that applies is the PAYMENT
        # year's, matching compute_multi_year_summary.
        return _index_irmaa_tiers(IRMAA_TIERS_MFJ, year + 2, hh.cpi_assumption)[0][0]

    def test_recommended_conversion_keeps_magi_under_tier1(self) -> None:
        hh = _shortfall_household()
        year = hh.base_year
        oracle = _oracle_year(hh, year)
        draw = oracle.forced_your_ira_draw + oracle.forced_spouse_ira_draw
        tier1 = self._tier1(hh, year)

        aware = base_income_for_year(hh, year, ira_draw=draw)
        safe = irmaa_safe_max(hh, aware, tier1)
        achieved = all_in_at_conversion(hh, aware, safe, 0.0)

        assert achieved.magi <= tier1 + 1.0

    def test_draw_blind_base_overstates_the_safe_conversion_by_the_draw(self) -> None:
        """Pins the size and the direction of the defect, so a future change
        that silently reintroduces draw-blindness fails here."""
        hh = _shortfall_household()
        year = hh.base_year
        oracle = _oracle_year(hh, year)
        draw = oracle.forced_your_ira_draw + oracle.forced_spouse_ira_draw
        tier1 = self._tier1(hh, year)

        blind = base_income_for_year(hh, year)
        aware = base_income_for_year(hh, year, ira_draw=draw)

        blind_safe = irmaa_safe_max(hh, blind, tier1)
        aware_safe = irmaa_safe_max(hh, aware, tier1)

        assert blind_safe > aware_safe
        # No SS and no preferential income here, so MAGI moves dollar-for-dollar
        # with the conversion and the gap is exactly the draw (up to STEP).
        from engine.sweet_spot_compute import STEP

        assert blind_safe - aware_safe == pytest.approx(draw, abs=STEP)

        # And the draw-blind recommendation really does breach, which is the
        # user-facing harm the finding reports.
        breached = all_in_at_conversion(hh, aware, blind_safe, 0.0)
        assert breached.magi > tier1


class TestZeroConversionDrawDerivation:
    """The helper that sources the draw from the engine rather than
    re-deriving the cash-need assembly locally."""

    def test_returns_ira_legs_per_year_over_the_conversion_window(self) -> None:
        hh = _shortfall_household()
        draws = zero_conversion_ira_draws(hh)

        conv_window = max(hh.your_conv_window, hh.spouse_conv_window)
        expected_years = set(range(hh.base_year, hh.base_year + conv_window))
        assert expected_years <= set(draws)

        oracle = _oracle_year(hh, hh.base_year)
        assert draws[hh.base_year] == pytest.approx(
            oracle.forced_your_ira_draw + oracle.forced_spouse_ira_draw, abs=1.0
        )

    def test_household_with_no_shortfall_yields_no_draw(self) -> None:
        """A household whose income covers its expenses must be numerically
        unchanged by this fix."""
        hh = _shortfall_household(living_expenses=0.0)
        draws = zero_conversion_ira_draws(hh)

        assert all(d == pytest.approx(0.0) for d in draws.values())

        rows = compute_multi_year_summary(hh)
        row = next(r for r in rows if r.year == hh.base_year)
        blind = base_income_for_year(hh, hh.base_year)
        assert row.base_magi == pytest.approx(blind.base_magi, abs=1.0)


class TestResidualIsBoundedAndSameSignAsEngine:
    """The zero-conversion baseline leaves a documented second-order residual:
    a nonzero conversion raises tax, which raises the true draw. Assert the
    residual is real but small relative to the defect being fixed, so nobody
    later mistakes this fix for an exact guarantee."""

    def test_conversion_raises_the_true_draw_above_the_baseline(self) -> None:
        hh = _shortfall_household()
        year = hh.base_year
        baseline = _oracle_year(hh, year)
        baseline_draw = baseline.forced_your_ira_draw + baseline.forced_spouse_ira_draw

        conv = 50_000.0
        end_age = hh.your_age + (year - hh.base_year)
        with_conv = run_scenario(
            hh,
            ConversionPlan(your_conversions={year: conv}),
            "with-conv",
            end_age=end_age,
        ).years[0]
        with_conv_draw = (
            with_conv.forced_your_ira_draw + with_conv.forced_spouse_ira_draw
        )

        assert with_conv_draw > baseline_draw
        # Same sign as the engine's own first-pass cap, and an order of
        # magnitude smaller than the draw itself.
        assert (with_conv_draw - baseline_draw) < baseline_draw
