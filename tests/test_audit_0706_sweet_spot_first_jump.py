"""Regression test: find_sweet_spots must detect the first marginal-cost jump (i==1).

Audit 2026-07-06 — high severity.

Bug: engine/sweet_spot_compute.py line ~361 used `i > 1` instead of `i >= 1`.
When base MAGI sits just below an IRMAA tier threshold the very first conversion
increment (results[1]) crosses the tier and produces a large marginal jump.
Because `i > 1` is False at i==1, the jump was never appended.  The tier-crossing
cliff was entirely invisible in the returned spots list.

Fix: change the guard to `i >= 1` so the seeded prev_marginal=0.0 baseline is
used correctly for the first step.
"""

from __future__ import annotations

import pytest

from engine.sweet_spot_compute import (
    ConversionResult,
    find_sweet_spots,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "conv_tax": 0.0,
    "irmaa_delta": 0.0,
    "aca_loss": 0.0,
    "niit_delta": 0.0,
    "ltcg_delta": 0.0,
    "magi": 0.0,
    "taxable_inc": 0.0,
    "room_12": 0.0,
    "room_22": 0.0,
}


def _cr(conv: float, all_in: float, irmaa_delta: float = 0.0) -> ConversionResult:
    """Build a minimal ConversionResult for find_sweet_spots testing."""
    return ConversionResult(
        conv=conv,
        all_in=all_in,
        irmaa_delta=irmaa_delta,
        **{k: v for k, v in _DEFAULTS.items() if k != "irmaa_delta"},
    )


# ---------------------------------------------------------------------------
# Core regression
# ---------------------------------------------------------------------------


class TestFindSweetSpotsFirstJump:
    """find_sweet_spots must surface the first-increment jump (i==1, not i>1)."""

    def test_first_step_irmaa_cliff_detected(self) -> None:
        """Scenario: base MAGI $217,500 just below MFJ IRMAA T1 ($218,000).

        results[0]: conv=0,      all_in=0        (no IRMAA)
        results[1]: conv=1_000,  all_in=4_854    (IRMAA T1 for 2 people)
        results[2]: conv=2_000,  all_in=4_854+22 (next $1K stays in same tier)

        Marginal at i==1: (4854 - 0) / 1000 * 100 = 485.4% — far above the 2% threshold.
        With the bug (`i > 1`) this jump is silently dropped.
        With the fix (`i >= 1`) it is included.
        """
        irmaa_surcharge = 4_854.0  # MFJ Tier-1 surcharge (2 enrollees, 2026 base)
        marginal_tax_per_step = 22.0  # ~22% bracket, low-value stand-in

        results = [
            _cr(conv=0, all_in=0.0, irmaa_delta=0.0),
            _cr(conv=1_000, all_in=irmaa_surcharge, irmaa_delta=irmaa_surcharge),
            _cr(
                conv=2_000,
                all_in=irmaa_surcharge + marginal_tax_per_step,
                irmaa_delta=irmaa_surcharge,
            ),
        ]

        spots = find_sweet_spots(results)

        assert len(spots) >= 1, (
            "find_sweet_spots returned no spots — first-increment IRMAA cliff was missed. "
            "Bug: guard is `i > 1`; fix to `i >= 1`."
        )

        first = spots[0]
        # The jump is recorded at the *previous* conv (0) — the last safe point.
        assert first.conv == pytest.approx(0.0), (
            f"Expected jump recorded at conv=0 (last safe point), got conv={first.conv}"
        )
        assert first.marginal_before == pytest.approx(0.0), (
            f"Expected marginal_before=0.0 (seeded baseline), got {first.marginal_before}"
        )
        # Marginal after should reflect the enormous IRMAA cliff
        assert first.marginal_after > 2.0, (
            f"Expected marginal_after > 2.0 (%), got {first.marginal_after}"
        )

    def test_minimal_two_element_first_jump(self) -> None:
        """Two-element list: the single step (i==1) is a large jump — must be caught."""
        results = [
            _cr(conv=0, all_in=0.0),
            _cr(conv=1_000, all_in=100.0),  # 10% marginal — well above 2% threshold
        ]

        spots = find_sweet_spots(results)

        assert len(spots) == 1, (
            f"Expected 1 spot from a two-element list with a 10%-per-$1K jump; got {len(spots)}"
        )
        assert spots[0].conv == pytest.approx(0.0)
        assert spots[0].marginal_before == pytest.approx(0.0)
        assert spots[0].marginal_after == pytest.approx(10.0)

    def test_no_false_positive_when_first_step_is_small(self) -> None:
        """First step is only 1% marginal — below threshold; no spot expected."""
        results = [
            _cr(conv=0, all_in=0.0),
            _cr(conv=1_000, all_in=10.0),  # 1% marginal — below 2% threshold
            _cr(conv=2_000, all_in=50.0),  # 4% marginal — jump here at i==2
        ]

        spots = find_sweet_spots(results)

        # The i==1 step (1%) must NOT produce a spot (delta vs 0.0 baseline = 1.0 < 2.0).
        # The i==2 step (4% vs 1%) must produce a spot (delta = 3.0 > 2.0).
        assert len(spots) == 1, f"Expected exactly 1 spot (at i==2); got {len(spots)}: {spots}"
        assert spots[0].conv == pytest.approx(1_000.0)

    def test_no_false_positive_small_first_step_only(self) -> None:
        """Only two elements; first step is below threshold — no spot."""
        results = [
            _cr(conv=0, all_in=0.0),
            _cr(conv=1_000, all_in=5.0),  # 0.5% marginal
        ]

        spots = find_sweet_spots(results)

        assert len(spots) == 0, f"Expected no spots; got {spots}"
