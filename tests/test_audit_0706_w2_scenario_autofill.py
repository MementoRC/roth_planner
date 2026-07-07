"""Audit-0706 wave-2: scenario_autofill.py regression tests.

Covers two findings:
  scenario-autofill-1 (MONEY BUG): NQO income double-counted in base-year
    other_fixed / base_magi path.
    When ytd.nqo_exercise_ytd > 0 and hh.option_income() > 0 (same event),
    ordinary_core includes opt (NQO) AND ytd.magi_ytd also includes NQO,
    causing other_fixed to carry NQO twice.
    Fix: subtract nqo_ytd from other_fixed before adding ytd.magi_ytd (mirrors
    scenario.py C-7, lines 324-326). ordinary_core / fixed_gross unchanged —
    NQO remains in bracket base once via opt (nqo_exercise_ytd is not in the
    ytd ordinary add-back list for fixed_gross).

  scenario-autofill-6 (wrapper removal): _room_to_12_fs / _room_to_22_fs are
    redundant because room_to_12 / room_to_22 in engine/tax.py already accept
    filing_status= directly.  After removal the lambdas call room_to_12 / room_to_22
    directly and produce identical results.
"""

from __future__ import annotations

from dataclasses import replace

from engine.scenario_autofill import (
    auto_fill_12,
    auto_fill_22,
    auto_fill_irmaa_safe,
)
from engine.tax import BRACKETS_SINGLE, room_to_12, room_to_22
from engine.tax_indexing import index_value
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _base_household() -> Household:
    """Minimal household with no grants and no SS."""
    return replace(
        Household(),
        your_age=61,
        spouse_age=61,
        your_ira=1_000_000.0,
        spouse_ira=1_000_000.0,
        your_rmd_start_age=75,
        spouse_rmd_start_age=75,
        your_ss_fra=30_000.0,   # non-zero so SS matters for tss / other_fixed path
        spouse_ss_fra=30_000.0,
        your_ss_start_age=62,   # claiming early so SS is active from base year
        spouse_ss_start_age=62,
        grants=[],  # no option income
    )


def _household_with_nqo(nqo_income: float = 50_000.0) -> Household:
    """Household where option_income(base_year, early=True) == nqo_income.

    StockGrant.spread(price) = shares * (price - strike).
    shares=1, strike=1.0, txn_price_now = nqo_income + 1.0
    → spread = 1 * ((nqo_income+1) - 1) = nqo_income.
    Grant at index 0 is exercised in base_year under early=True.
    """
    hh = _base_household()
    grant = StockGrant(
        year=hh.base_year - 4,
        strike=1.0,
        shares=1,
        expiry_year=hh.base_year + 2,
    )
    return replace(hh, grants=[grant], txn_price_now=nqo_income + 1.0)


# ---------------------------------------------------------------------------
# scenario-autofill-1: NQO double-count in base-year other_fixed / base_magi
# ---------------------------------------------------------------------------


class TestNQONoDoubleCountMAGI:
    """base-year other_fixed must not carry NQO twice when ytd carries nqo_exercise_ytd.

    The double-count occurs when both ordinary_core (via opt) and ytd.magi_ytd
    carry the same NQO amount.  The fix subtracts nqo_ytd from other_fixed
    before adding ytd.magi_ytd, mirroring C-7 in scenario.py:324-326.

    Test strategy: compare a household with a grant exercised in the base year
    (opt=NQO, ytd.nqo_exercise_ytd=NQO) against the same household without any
    ytd (opt=NQO, no ytd → nqo_ytd=0).  With the fix both should produce the
    same other_fixed → same tss → same irmaa_room.  With the bug, the ytd case
    inflates other_fixed by NQO → smaller IRMAA room → fewer conversions.
    """

    def test_irmaa_safe_ytd_nqo_matches_no_ytd(self) -> None:
        """auto_fill_irmaa_safe: base-year conversions identical with/without ytd NQO.

        When ytd.nqo_exercise_ytd == opt (same income event), the fix nets them
        to a single NQO in other_fixed.  Without ytd, other_fixed also has NQO
        once (from ordinary_core).  Both paths yield the same base_magi and
        same IRMAA room.

        With the bug: ytd path has base_magi = 2*NQO instead of NQO → less room.
        """
        nqo_amount = 40_000.0
        hh = _household_with_nqo(nqo_amount)
        base_year = hh.base_year

        # No ytd: other_fixed = ordinary_core = NQO, base_magi = NQO + tss
        plan_no_ytd = auto_fill_irmaa_safe(hh)

        # With ytd (nqo matches opt): after fix other_fixed = NQO - NQO + NQO = NQO
        # Without fix: other_fixed = NQO + NQO = 2*NQO
        ytd = YTDSnapshot(tax_year=base_year, nqo_exercise_ytd=nqo_amount)
        plan_with_ytd = auto_fill_irmaa_safe(hh, ytd=ytd)

        conv_no_ytd = (
            plan_no_ytd.your_conversions.get(base_year, 0.0)
            + plan_no_ytd.spouse_conversions.get(base_year, 0.0)
        )
        conv_with_ytd = (
            plan_with_ytd.your_conversions.get(base_year, 0.0)
            + plan_with_ytd.spouse_conversions.get(base_year, 0.0)
        )

        assert abs(conv_no_ytd - conv_with_ytd) < 500.0, (
            f"BUG: NQO double-counted in base_magi for IRMAA-safe fill.\n"
            f"  conv_no_ytd={conv_no_ytd:.0f}, conv_with_ytd={conv_with_ytd:.0f}\n"
            f"  Difference={abs(conv_no_ytd-conv_with_ytd):.0f} should be <500.\n"
            f"  Double-count inflates base_magi by nqo_amount={nqo_amount:.0f}, "
            f"shrinking IRMAA room by the same."
        )

    def test_fill12_ytd_nqo_matches_no_ytd_ss_path(self) -> None:
        """auto_fill_12 with SS active: base-year conversions identical with/without ytd NQO.

        When SS is active, other_fixed feeds taxable_ss → fixed_gross.  With the
        bug, double-counted NQO inflates other_fixed → higher tss → larger fixed_gross
        → less bracket room → fewer conversions in the ytd case.

        Fix: nqo_ytd subtraction nets other_fixed to NQO (same as no-ytd case),
        so tss is identical and bracket room is the same.
        """
        nqo_amount = 30_000.0
        hh = _household_with_nqo(nqo_amount)
        base_year = hh.base_year

        plan_no_ytd = auto_fill_12(hh)

        ytd = YTDSnapshot(tax_year=base_year, nqo_exercise_ytd=nqo_amount)
        plan_with_ytd = auto_fill_12(hh, ytd=ytd)

        conv_no_ytd = (
            plan_no_ytd.your_conversions.get(base_year, 0.0)
            + plan_no_ytd.spouse_conversions.get(base_year, 0.0)
        )
        conv_with_ytd = (
            plan_with_ytd.your_conversions.get(base_year, 0.0)
            + plan_with_ytd.spouse_conversions.get(base_year, 0.0)
        )

        assert abs(conv_no_ytd - conv_with_ytd) < 500.0, (
            f"BUG: double-counted NQO in other_fixed inflates tss and shrinks fill_12 room.\n"
            f"  conv_no_ytd={conv_no_ytd:.0f}, conv_with_ytd={conv_with_ytd:.0f}\n"
            f"  Difference={abs(conv_no_ytd-conv_with_ytd):.0f} should be <500."
        )

    def test_fill22_ytd_nqo_matches_no_ytd(self) -> None:
        """auto_fill_22: same as fill12 check but for the 22% ceiling."""
        nqo_amount = 45_000.0
        hh = _household_with_nqo(nqo_amount)
        base_year = hh.base_year

        plan_no_ytd = auto_fill_22(hh)
        ytd = YTDSnapshot(tax_year=base_year, nqo_exercise_ytd=nqo_amount)
        plan_with_ytd = auto_fill_22(hh, ytd=ytd)

        conv_no_ytd = (
            plan_no_ytd.your_conversions.get(base_year, 0.0)
            + plan_no_ytd.spouse_conversions.get(base_year, 0.0)
        )
        conv_with_ytd = (
            plan_with_ytd.your_conversions.get(base_year, 0.0)
            + plan_with_ytd.spouse_conversions.get(base_year, 0.0)
        )

        assert abs(conv_no_ytd - conv_with_ytd) < 500.0, (
            f"BUG: double-counted NQO in other_fixed inflates tss and shrinks fill_22 room.\n"
            f"  conv_no_ytd={conv_no_ytd:.0f}, conv_with_ytd={conv_with_ytd:.0f}\n"
            f"  Difference={abs(conv_no_ytd-conv_with_ytd):.0f} should be <500."
        )

    def test_forecast_year_unaffected_by_ytd_nqo(self) -> None:
        """Forecast year conversions are identical with or without base-year YTD NQO.

        In forecast years ytd_year is None so nqo_ytd=0 and the fix is a no-op.
        """
        nqo_amount = 50_000.0
        hh = _base_household()
        ytd = YTDSnapshot(tax_year=hh.base_year, nqo_exercise_ytd=nqo_amount)

        plan_with_ytd = auto_fill_12(hh, ytd=ytd)
        plan_without_ytd = auto_fill_12(hh)

        future_year = hh.base_year + 3
        conv_ytd = (
            plan_with_ytd.your_conversions.get(future_year, 0.0)
            + plan_with_ytd.spouse_conversions.get(future_year, 0.0)
        )
        conv_no_ytd = (
            plan_without_ytd.your_conversions.get(future_year, 0.0)
            + plan_without_ytd.spouse_conversions.get(future_year, 0.0)
        )

        assert abs(conv_ytd - conv_no_ytd) < 1.0, (
            f"YTD NQO must not affect forecast year conversions.\n"
            f"  future_year={future_year}, conv_ytd={conv_ytd:.0f}, conv_no_ytd={conv_no_ytd:.0f}"
        )


# ---------------------------------------------------------------------------
# scenario-autofill-6: wrapper removal — room_to_12/22 already handle filing_status
# ---------------------------------------------------------------------------


class TestRoomWrapperRemoval:
    """_room_to_12_fs/_room_to_22_fs must be gone; call sites use room_to_12/22 directly."""

    def test_room_to_12_fs_not_exported(self) -> None:
        """_room_to_12_fs wrapper must not exist in the module (removed by fix)."""
        import engine.scenario_autofill as sa_mod

        assert not hasattr(sa_mod, "_room_to_12_fs"), (
            "BUG: _room_to_12_fs wrapper still present; should have been removed."
        )

    def test_room_to_22_fs_not_exported(self) -> None:
        """_room_to_22_fs wrapper must not exist in the module."""
        import engine.scenario_autofill as sa_mod

        assert not hasattr(sa_mod, "_room_to_22_fs"), (
            "BUG: _room_to_22_fs wrapper still present; should have been removed."
        )

    def test_room_to_12_filing_status_single_matches_bracket(self) -> None:
        """room_to_12 with filing_status='Single' uses BRACKETS_SINGLE[1][0]."""
        year = 2026
        cpi = 1.025
        gross = 30_000.0
        ded = 16_100.0

        result = room_to_12(gross, ded, year=year, cpi=cpi, filing_status="Single")
        expected_ceiling = index_value(BRACKETS_SINGLE[1][0], year, cpi)
        expected = max(ded + expected_ceiling - gross, 0.0)
        assert abs(result - expected) < 0.01

    def test_room_to_22_filing_status_single_matches_bracket(self) -> None:
        """room_to_22 with filing_status='Single' uses BRACKETS_SINGLE[2][0]."""
        year = 2026
        cpi = 1.025
        gross = 30_000.0
        ded = 16_100.0

        result = room_to_22(gross, ded, year=year, cpi=cpi, filing_status="Single")
        expected_ceiling = index_value(BRACKETS_SINGLE[2][0], year, cpi)
        expected = max(ded + expected_ceiling - gross, 0.0)
        assert abs(result - expected) < 0.01

    def test_auto_fill_12_mfj_result_unchanged(self) -> None:
        """Removing wrappers must not change auto_fill_12 results for MFJ households."""
        hh = _base_household()
        plan = auto_fill_12(hh)
        total = sum(plan.your_conversions.values()) + sum(plan.spouse_conversions.values())
        assert total > 0, "auto_fill_12 should produce non-zero conversions"

    def test_auto_fill_22_mfj_result_unchanged(self) -> None:
        """Removing wrappers must not change auto_fill_22 results for MFJ households."""
        hh = _base_household()
        plan = auto_fill_22(hh)
        total = sum(plan.your_conversions.values()) + sum(plan.spouse_conversions.values())
        assert total > 0, "auto_fill_22 should produce non-zero conversions"

    def test_auto_fill_irmaa_safe_mfj_result_unchanged(self) -> None:
        """Removing wrappers must not change auto_fill_irmaa_safe results."""
        hh = _base_household()
        plan = auto_fill_irmaa_safe(hh)
        total = sum(plan.your_conversions.values()) + sum(plan.spouse_conversions.values())
        assert total > 0, "auto_fill_irmaa_safe should produce non-zero conversions"

    def test_room_to_12_single_lower_than_mfj(self) -> None:
        """Single 12% ceiling is lower than MFJ; room_to_12 must reflect this."""
        year, cpi = 2026, 1.025
        gross, ded = 30_000.0, 16_000.0
        room_single = room_to_12(gross, ded, year=year, cpi=cpi, filing_status="Single")
        room_mfj = room_to_12(gross, ded, year=year, cpi=cpi, filing_status="MFJ")
        assert room_single < room_mfj, (
            f"Single 12% ceiling must be lower than MFJ: {room_single:.0f} vs {room_mfj:.0f}"
        )

    def test_room_to_22_single_lower_than_mfj(self) -> None:
        """Single 22% ceiling is lower than MFJ; room_to_22 must reflect this."""
        year, cpi = 2026, 1.025
        gross, ded = 30_000.0, 16_000.0
        room_single = room_to_22(gross, ded, year=year, cpi=cpi, filing_status="Single")
        room_mfj = room_to_22(gross, ded, year=year, cpi=cpi, filing_status="MFJ")
        assert room_single < room_mfj, (
            f"Single 22% ceiling must be lower than MFJ: {room_single:.0f} vs {room_mfj:.0f}"
        )
