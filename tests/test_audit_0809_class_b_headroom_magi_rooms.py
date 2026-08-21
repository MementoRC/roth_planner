"""audit-0809 Class B — engine/headroom.py's IRMAA/NIIT rooms must bisect, not subtract.

The bracket rooms in ``engine/headroom.py`` already route through
``engine.tax.bisect_conversion_for_ceiling`` (audit-0805 C81). Their MAGI
siblings a few lines below -- ``room_to_irmaa_t1`` / ``room_to_niit``, on both
the locked and the planned path -- kept the closed form
``max(threshold - magi, 0)``. That form assumes MAGI rises exactly $1 per $1
converted. It does not: once provisional income sits in the IRC 86(b) 50%/85%
partial-taxability band, each converted dollar ALSO drags more Social Security
into MAGI, so the true boundary sits BELOW the naive estimate and the page
overstates how much may be converted before the cliff.

Same defect and same page-level symptom as audit-0809 #01 (PR #436, Sweet Spot
fill cards) and the Sweet Spot IRMAA/NIIT vlines (PR #438). This is the third
and last Class B site.

NON-VACUITY, and it is the whole reason this file exists: every pre-existing
headroom test runs on the default 61/55 household, where NEITHER spouse has
claimed Social Security, ``combined_ss`` is 0, and the naive and bisected forms
are algebraically IDENTICAL. A fixture without claimed SS cannot fail under the
defect. These tests therefore pin two explicit preconditions before asserting
anything:

  1. combined SS > 0 (somebody has claimed), and
  2. base taxable SS is UNSATURATED -- strictly below the 85% cap.

(2) matters as much as (1): if base provisional income already saturates the
cap, taxable SS is conversion-invariant again over the whole search range and
the two forms re-converge. A fixture that is merely "has SS" is not enough.
"""

from dataclasses import replace

import pytest

from engine.headroom import compute_headroom
from engine.ira import ss_benefit_at_age, ss_with_cola
from engine.irmaa import IRMAA_TIERS_MFJ
from engine.niit import NIIT_THRESHOLD_MFJ
from engine.tax import taxable_ss
from engine.tax_indexing import index_value
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot

BASE_YEAR = 2026
# Bisection runs 60 iterations, so it lands well inside a cent of the boundary.
CENT = 0.01


def _combined_ss(hh: Household) -> float:
    """Reproduce engine/headroom.py's own combined-SS derivation.

    Reconstructed rather than read off HeadroomResult because the preconditions
    below must be able to prove the fixture is in the 86(b) band BEFORE trusting
    any figure the module under test produced.
    """
    your_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
    spouse_base = ss_benefit_at_age(hh.spouse_ss_fra, hh.spouse_ss_start_age, hh.spouse_fra_age)
    your_ss = (
        ss_with_cola(your_base, hh.your_age - hh.your_ss_start_age, hh.ss_cola)
        if hh.your_age >= hh.your_ss_start_age
        else 0.0
    )
    spouse_ss = (
        ss_with_cola(spouse_base, hh.spouse_age - hh.spouse_ss_start_age, hh.ss_cola)
        if hh.spouse_age >= hh.spouse_ss_start_age
        else 0.0
    )
    return your_ss + spouse_ss


def _ss_household() -> Household:
    """MFJ, both spouses claiming, other income low enough to sit on the 86(b) ramp.

    cpi/COLA are pinned to 0.0 so the thresholds this file asserts against are
    the unindexed base constants and the arithmetic stays checkable by hand.
    """
    return Household(
        your_age=72,
        spouse_age=71,
        your_ss_start_age=70,
        spouse_ss_start_age=70,
        your_ss_fra=3_000.0,
        spouse_ss_fra=2_000.0,
        ss_cola=0.0,
        cpi_assumption=0.0,
        base_year=BASE_YEAR,
    )


def _ss_household_with_expiring_grant() -> Household:
    """As ``_ss_household()``, plus a grant expiring in the base year.

    ``ExerciseSchedule.default_at_expiry`` exercises a grant in its EXPIRY year,
    and the default grants expire 2030-2032 -- so on the plain fixture
    ``option_income(2026)`` is legitimately 0, ``planned_option_income`` is 0,
    and the planned path collapses onto the locked one, asserting nothing about
    the planned code. A grant expiring in the base year is what makes the
    planned assertions actually exercise the planned path.

    400 shares at a $100 spread = $40,000, and the size is load-bearing in BOTH
    directions. Too little and the planned path barely differs from the locked
    one; too much and it OVERSHOOTS the other way -- provisional income is
    ``other + 0.5*SS``, so $100,000 of option income puts it at $137,200, past
    the ~$111,341 point where taxable SS saturates at its 0.85 cap. Beyond that
    point taxable SS is conversion-INVARIANT, MAGI really does rise $1-per-$1,
    and the bisected room CORRECTLY equals the naive subtraction -- a test
    asserting they differ would then be asserting a defect that is not there.
    $40,000 keeps provisional income at $77,200, on the 85% ramp.
    """
    hh = _ss_household()
    hh.txn_price_now = 200.0
    hh.grants = [
        StockGrant(year=2019, strike=100.0, shares=400, expiry_year=BASE_YEAR, grant_id="g1")
    ]
    return hh


def _assert_on_the_86b_ramp(hh: Household, ytd: YTDSnapshot) -> float:
    """Precondition: SS is claimed AND base taxable SS is strictly unsaturated.

    Returns combined SS so callers can size their own expectations from it.
    """
    combined_ss = _combined_ss(hh)
    assert combined_ss > 0.0, "fixture has no claimed SS — the two formulas would coincide"
    base_tss = taxable_ss(combined_ss, ytd.magi_ytd, filing_status="MFJ")
    cap = 0.85 * combined_ss
    assert base_tss < cap - 1.0, (
        f"fixture starts with taxable SS pinned at the 85% cap "
        f"({base_tss:,.2f} vs cap {cap:,.2f}) — taxable SS would be "
        f"conversion-invariant and the naive form would not overshoot"
    )
    return combined_ss


def _irmaa_t1(hh: Household) -> float:
    # 2-year lookback: the threshold that binds is the PAYMENT year's.
    return index_value(IRMAA_TIERS_MFJ[0][0], hh.base_year + 2, hh.cpi_assumption)


def _magi_after_converting(hh: Household, ytd: YTDSnapshot, conv: float) -> float:
    """MAGI the engine itself reports once ``conv`` has actually been converted.

    Routed back through compute_headroom rather than recomputed inline: a
    conversion lands in ``ira_conversions_ytd``, which flows into ``magi_ytd``
    and thence into the 86(b) provisional-income base, so this is exactly the
    measure the room figure claims to have respected.
    """
    after = replace(ytd, ira_conversions_ytd=ytd.ira_conversions_ytd + conv)
    return compute_headroom(hh, after).locked_magi


def _niit_magi_after_converting(hh: Household, ytd: YTDSnapshot, conv: float) -> float:
    """NIIT MAGI after converting ``conv``.

    HeadroomResult exposes ``locked_magi`` but not its NIIT variant, so this
    reconstructs the engine's ``ytd.niit_magi_ytd + taxable_ss(...)`` shape.
    """
    after = replace(ytd, ira_conversions_ytd=ytd.ira_conversions_ytd + conv)
    tss = taxable_ss(_combined_ss(hh), after.magi_ytd, filing_status="MFJ")
    return after.niit_magi_ytd + tss


class TestHeadroomIrmaaRoomRespectsTheSSTorpedo:
    def test_converting_the_advertised_irmaa_room_does_not_cross_tier1(self):
        """The advertised room must be convertible without breaching tier 1."""
        hh = _ss_household()
        ytd = YTDSnapshot(tax_year=BASE_YEAR)
        _assert_on_the_86b_ramp(hh, ytd)

        room = compute_headroom(hh, ytd).room_to_irmaa_t1
        threshold = _irmaa_t1(hh)
        assert room > 0.0, "no room to test against"

        achieved = _magi_after_converting(hh, ytd, room)
        assert achieved <= threshold + CENT, (
            f"converting the advertised IRMAA room of ${room:,.2f} lands MAGI at "
            f"${achieved:,.2f}, ${achieved - threshold:,.2f} PAST the tier-1 "
            f"threshold of ${threshold:,.2f}"
        )

    def test_the_irmaa_room_is_tight_not_merely_safe(self):
        """A trivially small room would satisfy the test above. Pin the boundary."""
        hh = _ss_household()
        ytd = YTDSnapshot(tax_year=BASE_YEAR)
        _assert_on_the_86b_ramp(hh, ytd)

        room = compute_headroom(hh, ytd).room_to_irmaa_t1
        threshold = _irmaa_t1(hh)

        # One dollar more must breach — otherwise the room is understated.
        assert _magi_after_converting(hh, ytd, room + 1.0) > threshold

    def test_naive_subtraction_overshoots_and_the_oracle_beats_it(self):
        """Reproduce the superseded closed form inline and show it overshoots.

        Follows the pattern established by PR #438's vline tests: assert against
        the OLD arithmetic reproduced here, not against the module, so the test
        keeps its meaning once the module no longer contains that arithmetic.
        """
        hh = _ss_household()
        ytd = YTDSnapshot(tax_year=BASE_YEAR)
        _assert_on_the_86b_ramp(hh, ytd)

        hr = compute_headroom(hh, ytd)
        threshold = _irmaa_t1(hh)

        naive_room = max(threshold - hr.locked_magi, 0.0)
        assert _magi_after_converting(hh, ytd, naive_room) > threshold + 1.0, (
            "the naive room did NOT overshoot on this fixture — it no longer "
            "exercises the 86(b) nonlinearity and proves nothing"
        )

        assert hr.room_to_irmaa_t1 < naive_room, (
            "the reported room still equals the naive subtraction"
        )
        assert _magi_after_converting(hh, ytd, hr.room_to_irmaa_t1) == pytest.approx(
            threshold, abs=1.0
        )


class TestHeadroomNiitRoomRespectsTheSSTorpedo:
    def test_converting_the_advertised_niit_room_does_not_cross_the_threshold(self):
        hh = _ss_household()
        ytd = YTDSnapshot(tax_year=BASE_YEAR)
        _assert_on_the_86b_ramp(hh, ytd)

        room = compute_headroom(hh, ytd).room_to_niit
        assert room > 0.0, "no room to test against"

        achieved = _niit_magi_after_converting(hh, ytd, room)
        assert achieved <= NIIT_THRESHOLD_MFJ + CENT, (
            f"converting the advertised NIIT room of ${room:,.2f} lands NIIT MAGI "
            f"at ${achieved:,.2f}, ${achieved - NIIT_THRESHOLD_MFJ:,.2f} PAST the "
            f"${NIIT_THRESHOLD_MFJ:,.2f} threshold"
        )

    def test_the_niit_room_is_tight_not_merely_safe(self):
        hh = _ss_household()
        ytd = YTDSnapshot(tax_year=BASE_YEAR)
        _assert_on_the_86b_ramp(hh, ytd)

        room = compute_headroom(hh, ytd).room_to_niit
        assert _niit_magi_after_converting(hh, ytd, room + 1.0) > NIIT_THRESHOLD_MFJ


class TestHeadroomPlannedPathMatchesTheLockedPath:
    """The planned path carries the identical closed form and must move with it."""

    def test_planned_irmaa_room_does_not_cross_tier1(self):
        hh = _ss_household_with_expiring_grant()
        ytd = YTDSnapshot(tax_year=BASE_YEAR)
        _assert_on_the_86b_ramp(hh, ytd)

        hr = compute_headroom(hh, ytd)
        assert hr.planned_option_income > 0.0, (
            "fixture has no planned option income — the planned path would "
            "collapse onto the locked path and prove nothing separately"
        )

        # _assert_on_the_86b_ramp checks the LOCKED base only. Option income
        # moves the PLANNED base, and can push it past saturation on its own --
        # where bisected == naive is the correct answer, not a defect.
        _combined = _combined_ss(hh)
        _planned_other = ytd.magi_ytd + hr.planned_option_income
        assert (
            taxable_ss(_combined, _planned_other, filing_status="MFJ") < 0.85 * _combined - 1.0
        ), (
            "planned base already saturates the 85% SS cap — taxable SS is "
            "conversion-invariant there, so the bisection correctly EQUALS the "
            "naive subtraction and the assertions below would claim a false defect"
        )

        room = hr.room_to_irmaa_t1_with_planned
        threshold = _irmaa_t1(hh)
        assert room > 0.0, "no planned room to test against"
        assert room < max(threshold - hr.projected_magi_base, 0.0), (
            "the planned room still equals the naive subtraction"
        )

        # Realising the planned option income alongside the conversion is what
        # the planned figure promises is safe.
        after = replace(
            ytd,
            ira_conversions_ytd=ytd.ira_conversions_ytd + room,
            nqo_exercise_ytd=ytd.nqo_exercise_ytd + hr.planned_option_income,
        )
        achieved = compute_headroom(hh, after).locked_magi
        assert achieved <= threshold + CENT, (
            f"converting the advertised planned IRMAA room of ${room:,.2f} lands "
            f"MAGI at ${achieved:,.2f}, past tier 1 at ${threshold:,.2f}"
        )

    def test_planned_niit_room_does_not_cross_the_threshold(self):
        hh = _ss_household_with_expiring_grant()
        ytd = YTDSnapshot(tax_year=BASE_YEAR)
        _assert_on_the_86b_ramp(hh, ytd)

        hr = compute_headroom(hh, ytd)
        assert hr.planned_option_income > 0.0

        # _assert_on_the_86b_ramp checks the LOCKED base only. Option income
        # moves the PLANNED base, and can push it past saturation on its own --
        # where bisected == naive is the correct answer, not a defect.
        _combined = _combined_ss(hh)
        _planned_other = ytd.magi_ytd + hr.planned_option_income
        assert (
            taxable_ss(_combined, _planned_other, filing_status="MFJ") < 0.85 * _combined - 1.0
        ), (
            "planned base already saturates the 85% SS cap — taxable SS is "
            "conversion-invariant there, so the bisection correctly EQUALS the "
            "naive subtraction and the assertions below would claim a false defect"
        )

        room = hr.room_to_niit_with_planned
        assert room < max(NIIT_THRESHOLD_MFJ - hr.projected_magi_base, 0.0), (
            "the planned NIIT room still equals the naive subtraction"
        )
        after = replace(
            ytd,
            ira_conversions_ytd=ytd.ira_conversions_ytd + room,
            nqo_exercise_ytd=ytd.nqo_exercise_ytd + hr.planned_option_income,
        )
        tss = taxable_ss(_combined_ss(hh), after.magi_ytd, filing_status="MFJ")
        achieved = after.niit_magi_ytd + tss
        assert achieved <= NIIT_THRESHOLD_MFJ + CENT


class TestAcaCliffRoomStaysAClosedForm:
    """GUARD on a deliberate NON-change — do not "fix" the remaining asymmetry.

    ``room_to_aca_cliff`` measures against ``ytd.magi_ytd + combined_ss``. ACA
    MAGI (IRC 36B(d)(2)(B)(iii)) adds back the FULL benefit, taxable and
    non-taxable alike, so it does not move with the taxable share at all: a
    conversion raises it exactly $1 per $1 and the closed form is EXACT. Routing
    it through a bisection would be pure cost, and worse, would suggest to a
    later reader that the ACA figure had the same defect as its neighbours.
    """

    def _aca_household(self) -> Household:
        return Household(
            your_age=63,
            spouse_age=62,
            your_ss_start_age=62,
            spouse_ss_start_age=62,
            your_ss_fra=3_000.0,
            spouse_ss_fra=2_000.0,
            ss_cola=0.0,
            cpi_assumption=0.0,
            base_year=BASE_YEAR,
            your_aca_enrolled=True,
            spouse_aca_enrolled=True,
        )

    def test_converting_the_aca_room_lands_exactly_on_the_cliff(self):
        hh = self._aca_household()
        ytd = YTDSnapshot(tax_year=BASE_YEAR)
        combined_ss = _assert_on_the_86b_ramp(hh, ytd)

        room = compute_headroom(hh, ytd).room_to_aca_cliff
        assert room > 0.0, "fixture is already over the cliff — nothing pinned"

        after = replace(ytd, ira_conversions_ytd=ytd.ira_conversions_ytd + room)
        # Recomputed room must be exactly exhausted: full-SS ACA MAGI is linear.
        assert compute_headroom(hh, after).room_to_aca_cliff == pytest.approx(0.0, abs=1.0)
        # And the linearity itself, stated directly.
        assert (after.magi_ytd + combined_ss) - (ytd.magi_ytd + combined_ss) == pytest.approx(
            room, abs=CENT
        )


class TestUnclaimedSSHouseholdsAreUnaffected:
    """The fix must be a no-op wherever SS is not claimed.

    The maintained 61/55 household is exactly that case, as is every
    pre-existing test in tests/test_headroom.py. With combined_ss == 0 the
    bisection and the subtraction agree to the cent, so no golden may move.
    """

    def test_default_household_rooms_still_equal_the_closed_form(self):
        hh = Household(base_year=BASE_YEAR, cpi_assumption=0.0)
        ytd = YTDSnapshot(tax_year=BASE_YEAR, wages_ytd=100_000.0)

        assert _combined_ss(hh) == 0.0, "fixture unexpectedly claims SS"

        hr = compute_headroom(hh, ytd)
        assert hr.room_to_irmaa_t1 == pytest.approx(
            max(_irmaa_t1(hh) - hr.locked_magi, 0.0), abs=CENT
        )
        niit_magi = ytd.niit_magi_ytd + taxable_ss(0.0, ytd.magi_ytd, filing_status="MFJ")
        assert hr.room_to_niit == pytest.approx(
            max(NIIT_THRESHOLD_MFJ - niit_magi, 0.0), abs=CENT
        )
