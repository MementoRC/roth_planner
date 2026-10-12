"""Issue #598: "Fill 12%" must land each conversion year on the 12% ceiling.

Reported before #595: the base-year taxable ordinary income sat ~$15.9k below the
top of the 12% bracket. Root cause: auto-fill sized the conversion from fixed
income and deductions only, while run_scenario's withdrawal waterfall forces a
taxable IRA draw whenever nothing else can fund living expenses plus the year's
tax. "Fill to X" now means the year's TOTAL ordinary income -- conversion plus
any forced IRA draw -- lands on X (and the forced draw counts toward MAGI for
the MAGI-based targets).

Guarded across a pre-senior household, a 65+ household (OBBBA senior deduction)
and a household drawing Social Security (taxable-SS torpedo). Synthetic values
only.
"""

from collections.abc import Callable

import pytest

from engine import scenario_autofill
from engine.irmaa import irmaa_tier1_threshold
from engine.scenario import project_year_with_draw, run_scenario
from engine.scenario_autofill import (
    auto_fill_12,
    auto_fill_22,
    auto_fill_24,
    auto_fill_aca,
    auto_fill_irmaa_safe,
)
from engine.scenario_types import ConversionPlan, YearResult
from engine.tax import indexed_bracket_ceiling
from models.household import Household

BASE_YEAR = 2026
TOLERANCE = 100.0
MAGI_TOL = 1.0


def _hh(your_age: int, spouse_age: int, **kwargs: float) -> Household:
    fields: dict[str, object] = {
        "brokerage_start": 0.0,
        "living_expenses": 0.0,
        "your_ira": 900_000.0,
        "spouse_ira": 700_000.0,
    }
    fields.update(kwargs)
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        base_year=BASE_YEAR,
        cpi_assumption=0.0,
        grants=[],
        **fields,  # type: ignore[arg-type]
    )


HOUSEHOLDS = {
    "pre_senior": lambda: _hh(61, 55, your_ss_fra=0.0, spouse_ss_fra=0.0),
    "senior_65_plus": lambda: _hh(67, 66, your_ss_fra=0.0, spouse_ss_fra=0.0),
    "receiving_ss": lambda: _hh(
        70,
        68,
        your_ss_fra=3_000.0,
        spouse_ss_fra=1_500.0,
        your_ss_start_age=70,
        spouse_ss_start_age=68,
    ),
}


def _forced_ira_draw(yr: YearResult) -> float:
    return yr.forced_your_ira_draw + yr.forced_spouse_ira_draw


def _assert_base_year_on_ceiling(hh: Household, label: str) -> YearResult:
    plan = auto_fill_12(hh, None)
    result = run_scenario(hh, plan, "fill12", end_age=95, ytd=None)
    base = next(y for y in result.years if y.year == BASE_YEAR)

    # Conversion room only exists before RMDs begin and with IRA balance to convert.
    assert hh.your_age < hh.your_rmd_start_age
    assert hh.spouse_age < hh.spouse_rmd_start_age
    ceiling = indexed_bracket_ceiling(1, year=BASE_YEAR, cpi=0.0, filing_status=base.filing_status)
    converted = base.your_conversion + base.spouse_conversion
    assert converted > 0.0
    assert base.your_ira_begin + base.spouse_ira_begin >= converted

    assert abs(base.taxable_income - ceiling) <= TOLERANCE, (
        f"{label}: taxable={base.taxable_income:.0f} ceiling={ceiling:.0f} "
        f"conv={converted:.0f} draw={_forced_ira_draw(base):.0f} "
        f"gross={base.combined_gross:.0f} ded={base.total_deductions:.0f} "
        f"tss={base.taxable_ss_amt:.0f}"
    )
    return base


@pytest.mark.parametrize("name", sorted(HOUSEHOLDS))
def test_598_fill_12_base_year_lands_on_ceiling_without_ytd(name: str) -> None:
    _assert_base_year_on_ceiling(HOUSEHOLDS[name](), name)


def test_598_fill_12_with_default_living_expenses_and_no_brokerage() -> None:
    """Default $60k living expenses, no brokerage: the waterfall draws the IRA to
    fund spending AND the conversion tax, so the conversion must shrink by it."""
    hh = _hh(61, 55, your_ss_fra=0.0, spouse_ss_fra=0.0, living_expenses=60_000.0)
    base = _assert_base_year_on_ceiling(hh, "living_expenses_60k")
    assert _forced_ira_draw(base) > 60_000.0


def test_598_fill_12_with_brokerage_covering_spending_forces_no_ira_draw() -> None:
    """Regression guard: brokerage funds spending and tax, so no IRA draw is
    forced and the conversion alone fills the bracket."""
    hh = _hh(
        61,
        55,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        living_expenses=60_000.0,
        brokerage_start=2_000_000.0,
    )
    base = _assert_base_year_on_ceiling(hh, "brokerage_funded")
    assert _forced_ira_draw(base) == 0.0
    assert base.forced_brokerage_draw > 0.0


def test_598_fill_12_every_conversion_year_lands_on_ceiling() -> None:
    """Not just the base year: the forced draws are carried into later balances,
    so every conversion year with IRA left to convert lands on the ceiling."""
    hh = _hh(61, 55, your_ss_fra=0.0, spouse_ss_fra=0.0, living_expenses=60_000.0)
    plan = auto_fill_12(hh, None)
    result = run_scenario(hh, plan, "fill12", end_age=95, ytd=None)

    checked = 0
    for yr in result.years:
        converted = yr.your_conversion + yr.spouse_conversion
        if converted <= 0.0:
            continue
        ceiling = indexed_bracket_ceiling(1, year=yr.year, cpi=0.0, filing_status=yr.filing_status)
        assert abs(yr.taxable_income - ceiling) <= TOLERANCE, (
            f"{yr.year}: taxable={yr.taxable_income:.0f} ceiling={ceiling:.0f} "
            f"conv={converted:.0f} draw={_forced_ira_draw(yr):.0f}"
        )
        checked += 1
    assert checked >= 5


def test_598_irmaa_safe_counts_forced_draw_toward_magi() -> None:
    """IRMAA-safe with a forced draw: the plan itself (not the projection's
    shrink-and-resolve cap) lands MAGI on the tier-1 threshold."""
    hh = _hh(67, 66, your_ss_fra=0.0, spouse_ss_fra=0.0, living_expenses=60_000.0)
    plan = auto_fill_irmaa_safe(hh, None)
    result = run_scenario(hh, plan, "irmaa_safe", end_age=95, ytd=None)
    base = next(y for y in result.years if y.year == BASE_YEAR)

    threshold = irmaa_tier1_threshold(BASE_YEAR, base.filing_status, 0.0)
    planned = plan.your_conversions.get(BASE_YEAR, 0.0) + plan.spouse_conversions.get(
        BASE_YEAR, 0.0
    )
    converted = base.your_conversion + base.spouse_conversion
    assert _forced_ira_draw(base) > 0.0
    assert converted > 0.0
    # The projection converted what the plan asked for -- no cap had to bind.
    assert converted == pytest.approx(planned, abs=MAGI_TOL)
    assert base.magi <= threshold + MAGI_TOL
    assert base.magi >= threshold - TOLERANCE


def test_598_fill_12_forced_draw_with_taxable_ss_lands_on_ceiling() -> None:
    """One spouse draws Social Security that does not cover spending and there
    is no brokerage, so the waterfall forces an IRA draw that also drags more
    of the benefit into taxability (IRC §86) -- the fill still lands."""
    hh = _hh(
        66,
        64,
        your_ss_fra=2_000.0,
        spouse_ss_fra=0.0,
        your_ss_start_age=66,
        living_expenses=80_000.0,
    )
    base = _assert_base_year_on_ceiling(hh, "forced_draw_taxable_ss")
    assert _forced_ira_draw(base) > 0.0
    assert base.taxable_ss_amt > 0.0


def test_598_irmaa_safe_counts_forced_sale_gain_toward_magi() -> None:
    """A forced brokerage sale realizes a gain that is MAGI: the IRMAA-safe plan
    sizes for it, so the projection's shrink-and-resolve cap never binds."""
    hh = _hh(
        67,
        66,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        living_expenses=80_000.0,
        brokerage_start=30_000.0,
        brokerage_start_basis=0.0,
    )
    plan = auto_fill_irmaa_safe(hh, None)
    result = run_scenario(hh, plan, "irmaa_safe", end_age=95, ytd=None)
    base = next(y for y in result.years if y.year == BASE_YEAR)

    threshold = irmaa_tier1_threshold(BASE_YEAR, base.filing_status, 0.0)
    planned = plan.your_conversions.get(BASE_YEAR, 0.0) + plan.spouse_conversions.get(
        BASE_YEAR, 0.0
    )
    assert base.forced_brokerage_draw > 0.0
    assert base.your_conversion + base.spouse_conversion == pytest.approx(planned, abs=MAGI_TOL)
    assert threshold - TOLERANCE <= base.magi <= threshold + MAGI_TOL


@pytest.mark.parametrize(
    ("fill", "strategy"),
    [(auto_fill_irmaa_safe, "irmaa_safe"), (auto_fill_aca, "aca_safe"), (auto_fill_12, None)],
)
def test_598_probes_see_the_final_magi_strategy(
    monkeypatch: pytest.MonkeyPatch, fill: Callable[..., ConversionPlan], strategy: str | None
) -> None:
    """magi_strategy is on the plan BEFORE any probe runs, not stamped on after."""
    seen: list[str | None] = []

    def _spy(yr_idx, hh, plan, ytd, nii, state, draw):  # type: ignore[no-untyped-def]
        seen.append(plan.magi_strategy)
        return project_year_with_draw(yr_idx, hh, plan, ytd, nii, state, draw)

    monkeypatch.setattr(scenario_autofill, "project_year_with_draw", _spy)
    hh = _hh(61, 55, your_ss_fra=0.0, spouse_ss_fra=0.0, living_expenses=60_000.0)
    plan = fill(hh, None)
    assert seen
    assert set(seen) == {strategy}
    assert plan.magi_strategy == strategy


@pytest.mark.parametrize(
    "fill", [auto_fill_12, auto_fill_22, auto_fill_24, auto_fill_irmaa_safe, auto_fill_aca]
)
def test_598_auto_fill_passes_net_inv_income_to_probes(
    monkeypatch: pytest.MonkeyPatch, fill: Callable[..., ConversionPlan]
) -> None:
    seen: list[float] = []

    def _spy(yr_idx, hh, plan, ytd, nii, state, draw):  # type: ignore[no-untyped-def]
        seen.append(nii)
        return project_year_with_draw(yr_idx, hh, plan, ytd, nii, state, draw)

    monkeypatch.setattr(scenario_autofill, "project_year_with_draw", _spy)
    hh = _hh(61, 55, your_ss_fra=0.0, spouse_ss_fra=0.0, living_expenses=60_000.0)
    fill(hh, None, net_inv_income=12_345.0)
    assert seen
    assert set(seen) == {12_345.0}


def test_598_conversion_for_draw_clamps_negative_room_to_zero() -> None:
    inputs = scenario_autofill._RoomInputs(
        ordinary_gross=0.0,
        ytd_ordinary=None,
        other_fixed=0.0,
        combined_ss=0.0,
        ded_no_senior=0.0,
        phaseout_muni=0.0,
        ya_eff=61,
        sa_eff=55,
        year=BASE_YEAR,
        cpi=0.0,
        filing_status="MFJ",
    )

    def _negative_room(*_args: object) -> float:
        return -5_000.0

    assert scenario_autofill._conversion_for_draw(inputs, None, _negative_room) == 0.0
