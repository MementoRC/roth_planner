"""RED gate for issue #465 -- "Fill to 24%" is silently identical to a 22% fill.

``engine.scenario_autofill.auto_fill_24`` sizes a plan to the 24% bracket
ceiling, but ``run_scenario``'s conversion cap resolved its ceiling through
``_base_headroom``, which returned ``zero_conv_nd.yr.room_22`` unconditionally
-- a HARDCODED bracket identity. ``ConversionPlan`` carried no record of which
bracket it had been sized to, so every bracket-fill plan, whatever ceiling it
targeted, was clipped back to the 22% bracket room before a single dollar was
applied.

WHY THE EXISTING PIN MISSED IT. ``tests/test_auto_fill_ceilings.py::
test_auto_fill_24_converts_more_than_22`` already asserts a 24% plan converts
more than a 22% plan -- and passes, because it asserts on the PLAN
``auto_fill_24`` returns and never runs it through ``run_scenario``. The clamp
lives strictly between the plan and the applied conversion, which is the one
place that pin does not look. Every gate in this module therefore asserts on
APPLIED conversions, never on planned ones.

SCOPE. The cap is too TIGHT for a 24%-targeted plan and this fixes exactly
that. It deliberately does NOT tighten the 12% case: ``_base_headroom``'s
docstring records that selecting ``room_12`` was tried and rejected, because
choosing the ceiling from the ACHIEVED MARGINAL RATE made a deliberate
22%-fill plan self-limiting and inverted the tool's own invariant
(``test_scenario_core.py::test_22pct_fill_reduces_ira_more``). Keying the
ceiling off the plan's DECLARED target is a different mechanism and does not
revive that experiment, but 12%/22%/custom plans keep their existing
``room_22`` ceiling untouched here so this fix carries no behaviour change
beyond the filed defect.
"""

from __future__ import annotations

from engine.scenario import run_scenario
from engine.scenario_autofill import auto_fill_12, auto_fill_22, auto_fill_24
from models.household import Household


def _fixture_household() -> Household:
    """Large-IRA household, mirroring test_auto_fill_ceilings.py.

    The $10M balances matter: with the default $1.7M fixture the more
    aggressive 24%-fill exhausts the IRA a few years before the 22%-fill does,
    so later years convert LESS under 24% purely as an IRA-cap artifact. That
    artifact would mask the very difference these gates measure.
    """
    return Household(your_age=61, spouse_age=55, your_ira=10_000_000, spouse_ira=10_000_000)


def _applied_total(hh: Household, plan) -> float:
    result = run_scenario(hh, plan)
    return result.total_your_conv + result.total_spouse_conv


def test_fill_24_applies_more_than_fill_22() -> None:
    """The headline defect: run_scenario must APPLY more under a 24% plan.

    Pre-fix both plans are clipped to the same room_22 ceiling, so the two
    applied totals are equal and the 24% strategy is indistinguishable in
    output from the 22% one.

    WEAK INVARIANT, not a discriminating gate: this passes pre-fix too,
    because the room_22 cap only binds in years where the planned amount
    exceeds room_22, and aggregate totals across all years already differed
    enough to satisfy a bare ">" before the fix landed. The gates that
    actually discriminate fixed from unfixed are
    ``test_fill_24_is_not_clipped_to_the_22_ceiling_in_the_base_year`` (which
    pins the specific base-year clipping) and
    ``test_fill_24_applied_tracks_its_own_plan`` (which checks the applied
    total against the plan's own size, not just against the 22% plan).
    """
    hh = _fixture_household()

    applied_22 = _applied_total(hh, auto_fill_22(hh))
    applied_24 = _applied_total(hh, auto_fill_24(hh))

    assert applied_24 > applied_22, (
        f"24%-fill applied {applied_24:,.0f} but 22%-fill applied {applied_22:,.0f} -- "
        "run_scenario clipped the 24% plan back to the 22% bracket room, making "
        '"Fill to 24%" indistinguishable from a 22% fill (issue #465)'
    )


def test_fill_24_is_not_clipped_to_the_22_ceiling_in_the_base_year() -> None:
    """Pin the specific clipping the issue measured.

    The issue reports a base-year plan of $439,050 applied as $258,900, and
    $258,900 is exactly the indexed 22% bracket ceiling plus deductions. So the
    base-year applied conversion must exceed what the 22% plan applies in that
    same year -- if it does not, the room_22 cap is still binding.
    """
    hh = _fixture_household()
    base_year = hh.base_year

    def _applied_in_base_year(plan) -> float:
        result = run_scenario(hh, plan)
        for yr in result.years:
            if yr.year == base_year:
                return yr.your_conversion + yr.spouse_conversion
        raise AssertionError(f"base year {base_year} missing from scenario result")

    applied_22 = _applied_in_base_year(auto_fill_22(hh))
    applied_24 = _applied_in_base_year(auto_fill_24(hh))

    assert applied_24 > applied_22 + 1.0, (
        f"base year {base_year}: 24%-fill applied {applied_24:,.0f}, 22%-fill applied "
        f"{applied_22:,.0f} -- the 24% plan is still being clipped to the 22% room"
    )


def test_fill_24_applied_tracks_its_own_plan() -> None:
    """The applied conversion must actually reach the plan the strategy wrote.

    Distinct from the two gates above, which only compare 24% against 22%: this
    one catches a partial fix that raises the ceiling without raising it far
    enough to stop binding on a 24%-targeted plan.
    """
    hh = _fixture_household()
    plan_24 = auto_fill_24(hh)

    planned = sum(plan_24.your_conversions.values()) + sum(plan_24.spouse_conversions.values())
    applied = _applied_total(hh, plan_24)

    # Applied can legitimately fall short of planned in LATER years (IRA
    # exhaustion, filing-status changes, the waterfall draw), so this asserts a
    # generous floor rather than equality -- pre-fix the ratio is far below it.
    assert applied > planned * 0.75, (
        f"24%-fill planned {planned:,.0f} but only {applied:,.0f} was applied "
        f"({applied / planned:.0%} of plan) -- the bracket cap is still clipping it"
    )


def test_fill_12_and_22_keep_their_existing_ceiling() -> None:
    """Guard the deliberate non-change.

    Only the 24% strategy opts into a raised ceiling. If a later edit gives
    auto_fill_12 a declared 12% target, a 12%-fill plan starts being clipped to
    room_12 -- reviving precisely the experiment _base_headroom's docstring
    records as tried and WRONG. This fails loudly if that happens.
    """
    hh = _fixture_household()

    assert getattr(auto_fill_12(hh), "bracket_target", None) is None, (
        "auto_fill_12 declared a bracket target -- that re-enables room_12 capping, "
        "which _base_headroom documents as tried and rejected (it inverts "
        "test_22pct_fill_reduces_ira_more)"
    )
    assert getattr(auto_fill_22(hh), "bracket_target", None) is None, (
        "auto_fill_22 declared a bracket target -- the 22% ceiling is already the "
        "default, so declaring it changes nothing and should stay unset"
    )
