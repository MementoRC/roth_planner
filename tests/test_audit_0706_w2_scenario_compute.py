"""Regression tests for audit-0706 wave-2 scenario_compute.py findings.

Findings:
  scenario-core-8: compute_phase returns "squeeze" for single-filer (sa=0) in RMD years
  models-config-1: compute_brokerage_dividends: brokerage_growth=None case documented/clarified
"""

from __future__ import annotations

import pytest

from engine.scenario_compute import (
    compute_brokerage_dividends,
    compute_phase,
)
from models.household import GrowthProfile, Household

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_hh(**kwargs) -> Household:
    """Minimal Household — all keyword overrides accepted."""
    defaults: dict = {
        "your_age": 62,
        "spouse_age": 56,
        "base_year": 2026,
        "your_ira": 1_000_000.0,
        "spouse_ira": 500_000.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "your_ss_start_age": 70,
        "spouse_ss_start_age": 70,
        "living_expenses": 60_000.0,
        "brokerage_start": 0.0,
    }
    defaults.update(kwargs)
    return Household(**defaults)


# ===========================================================================
# Finding scenario-core-8: single-filer RMD phase mislabelled as "squeeze"
# ===========================================================================


class TestComputePhaseSingleFilerRmd:
    """Single-filer (sa=0) in an RMD year must get phase "rmd", not "squeeze".

    Bug: line 65 returns "squeeze" when sa < rmd_spouse without checking sa > 0.
    For single-filers sa=0 and rmd_spouse (e.g. 73) satisfies sa < rmd_spouse,
    so the year is wrongly labelled "squeeze".

    Fix: return "squeeze" only when (sa > 0 and sa < rmd_spouse).
    """

    def _hh_single_filer(self) -> Household:
        """Single-filer household: spouse_age=0 signals no spouse."""
        return _base_hh(
            your_age=75,
            spouse_age=0,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
        )

    def test_single_filer_rmd_year_phase_is_rmd_not_squeeze(self) -> None:
        """ya >= rmd_yours and sa == 0 must produce phase 'rmd', not 'squeeze'."""
        hh = self._hh_single_filer()
        phase = compute_phase(
            ya=75,
            sa=0,
            year=hh.base_year,
            hh=hh,
            early_exercise=False,
        )
        assert phase == "rmd", (
            f"Expected 'rmd' for single-filer (sa=0) in RMD year, got {phase!r}"
        )

    def test_mfj_filer_pre_spouse_rmd_is_squeeze(self) -> None:
        """MFJ: ya >= rmd_yours but sa < rmd_spouse must still produce 'squeeze'."""
        hh = _base_hh(
            your_age=75,
            spouse_age=69,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
        )
        phase = compute_phase(
            ya=75,
            sa=69,
            year=hh.base_year,
            hh=hh,
            early_exercise=False,
        )
        assert phase == "squeeze", (
            f"Expected 'squeeze' for MFJ spouse pre-RMD year, got {phase!r}"
        )

    def test_mfj_filer_both_rmd_is_rmd(self) -> None:
        """MFJ: ya >= rmd_yours and sa >= rmd_spouse must produce 'rmd'."""
        hh = _base_hh(
            your_age=75,
            spouse_age=75,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
        )
        phase = compute_phase(
            ya=75,
            sa=75,
            year=hh.base_year,
            hh=hh,
            early_exercise=False,
        )
        assert phase == "rmd", (
            f"Expected 'rmd' for MFJ both-in-RMD year, got {phase!r}"
        )

    def test_single_filer_exactly_at_rmd_start_is_rmd(self) -> None:
        """ya == rmd_start and sa == 0: first RMD year must be 'rmd'."""
        hh = _base_hh(
            your_age=73,
            spouse_age=0,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
        )
        phase = compute_phase(
            ya=73,
            sa=0,
            year=hh.base_year,
            hh=hh,
            early_exercise=False,
        )
        assert phase == "rmd", (
            f"Expected 'rmd' for single-filer at exact RMD start, got {phase!r}"
        )


# ===========================================================================
# Finding models-config-1: brokerage_growth=None dividend behaviour
# ===========================================================================


class TestComputeBrokerageDividendsNoneGrowthProfile:
    """compute_brokerage_dividends with brokerage_growth=None returns (0,0).

    Design intent: when no GrowthProfile is configured there is no yield_rate,
    so dividend income is zero by definition (the brokerage_rate() covers total
    return; no separate yield component exists). The (0,0) return is correct.

    This test class documents the intended behaviour so any future change is
    deliberate, not accidental.

    TDD: these tests were written BEFORE any code change to establish whether
    the current behaviour is a bug or intentional.  Assessment: INTENTIONAL —
    see inline comments and scenario.py line comment "yield_rate defaults to
    0.0 on GrowthProfile, so this is zero-cost when not configured."
    """

    def test_none_growth_profile_returns_zero_dividends(self) -> None:
        """brokerage_growth=None -> (0.0, 0.0) regardless of brokerage balance."""
        qual, ord_ = compute_brokerage_dividends(
            year=2027,
            base_year=2026,
            brokerage=500_000.0,
            brokerage_growth=None,
            ytd=None,
        )
        assert qual == 0.0
        assert ord_ == 0.0

    def test_none_growth_profile_base_year_no_ytd_returns_zero(self) -> None:
        """Base year, no YTD snapshot, no GrowthProfile -> (0.0, 0.0)."""
        qual, ord_ = compute_brokerage_dividends(
            year=2026,
            base_year=2026,
            brokerage=500_000.0,
            brokerage_growth=None,
            ytd=None,
        )
        assert qual == 0.0
        assert ord_ == 0.0

    def test_with_growth_profile_yield_produces_dividends(self) -> None:
        """Sanity: a configured GrowthProfile with yield_rate > 0 produces dividends."""
        profile = GrowthProfile(default_rate=0.07, yield_rate=0.02, qualified_fraction=0.8)
        qual, ord_ = compute_brokerage_dividends(
            year=2027,
            base_year=2026,
            brokerage=100_000.0,
            brokerage_growth=profile,
            ytd=None,
        )
        # 100_000 * 0.02 * 0.8 = 1_600 qualified; 100_000 * 0.02 * 0.2 = 400 ordinary
        assert qual == pytest.approx(1_600.0)
        assert ord_ == pytest.approx(400.0)

    def test_with_growth_profile_zero_yield_returns_zero(self) -> None:
        """GrowthProfile with default yield_rate=0 matches the None-profile result."""
        profile = GrowthProfile(default_rate=0.07)  # yield_rate defaults to 0.0
        qual, ord_ = compute_brokerage_dividends(
            year=2027,
            base_year=2026,
            brokerage=500_000.0,
            brokerage_growth=profile,
            ytd=None,
        )
        assert qual == 0.0
        assert ord_ == 0.0
