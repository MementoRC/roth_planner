"""Tests for audit-0707 batch C2 fixes.

UU4-UI-03: trad_deduction_phaseout_for_year CPI indexing (Fix A).
UU5-UI-05: RMD age widget guard (Fix B) — verified by inspection (widget-only, no unit test).
"""

import pytest

from views._format import fmt_pct
from views.roth_eligibility import trad_deduction_phaseout_for_year

# ---------------------------------------------------------------------------
# fmt_pct tests (UU3 — fix already applied in views/_format.py)
# ---------------------------------------------------------------------------


class TestFmtPct:
    def test_zero(self) -> None:
        assert fmt_pct(0.0) == "0.0%"

    def test_whole_number(self) -> None:
        assert fmt_pct(0.25) == "25.0%"

    def test_fractional(self) -> None:
        assert fmt_pct(0.1234) == "12.3%"

    def test_one_hundred(self) -> None:
        assert fmt_pct(1.0) == "100.0%"

    def test_negative(self) -> None:
        assert fmt_pct(-0.05) == "-5.0%"


# ---------------------------------------------------------------------------
# trad_deduction_phaseout_for_year tests (UU4-UI-03 — Fix A)
# ---------------------------------------------------------------------------


class TestTradDeductionPhaseoutForYear:
    """Verify that trad_deduction_phaseout_for_year CPI-indexes out-years."""

    def test_2026_mfj_active_unchanged(self) -> None:
        """Published 2026 value must be returned exactly."""
        low, high = trad_deduction_phaseout_for_year(2026, "MFJ_active")
        assert low == 129_000.0
        assert high == 149_000.0

    def test_2026_single_unchanged(self) -> None:
        low, high = trad_deduction_phaseout_for_year(2026, "Single")
        assert low == 81_000.0
        assert high == 91_000.0

    def test_2025_mfj_active_unchanged(self) -> None:
        """Published 2025 value must be returned exactly."""
        low, high = trad_deduction_phaseout_for_year(2025, "MFJ_active")
        assert low == 126_000.0
        assert high == 146_000.0

    def test_out_year_strictly_exceeds_2026_with_default_cpi(self) -> None:
        """2030 with default CPI must be strictly greater than the 2026 base."""
        low_2026, high_2026 = trad_deduction_phaseout_for_year(2026, "MFJ_active")
        low_2030, high_2030 = trad_deduction_phaseout_for_year(2030, "MFJ_active")
        assert low_2030 > low_2026
        assert high_2030 > high_2026

    def test_out_year_cpi_zero_equals_2026(self) -> None:
        """With cpi=0 the out-year values must equal the 2026 base (no inflation)."""
        low_2026, high_2026 = trad_deduction_phaseout_for_year(2026, "MFJ_active")
        low_2030, high_2030 = trad_deduction_phaseout_for_year(2030, "MFJ_active", cpi=0.0)
        assert low_2030 == pytest.approx(low_2026)
        assert high_2030 == pytest.approx(high_2026)

    def test_out_year_single_strictly_exceeds_2026(self) -> None:
        low_2026, _ = trad_deduction_phaseout_for_year(2026, "Single")
        low_2030, _ = trad_deduction_phaseout_for_year(2030, "Single")
        assert low_2030 > low_2026
