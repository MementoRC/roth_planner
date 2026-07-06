"""Regression test for audit-0706: Roth phase-out must apply to full IRA limit.

Bug: views/roth_eligibility.py line 438 applied _phase_out() to `remaining`
(limit minus prior Trad IRA contribution) instead of the full `limit`.
For a filer in the phase-out band who already made a Trad IRA contribution,
this understated the allowable Roth contribution.

Fix: allowed = min(_phase_out(magi, lower, upper, float(limit)), float(remaining))
"""

import pytest

from views.roth_eligibility import (
    _phase_out,
    _roth_allowed,
    contrib_limit_for_year,
    roth_phaseout_for_year,
)


class TestRothPhaseoutFullLimit:
    """Audit-0706: phase-out fraction must be applied to the full IRA limit."""

    def test_phase_out_uses_full_limit_not_remaining(self) -> None:
        """A filer mid-phase-out with a prior Trad contribution must get
        min(phase_out(full_limit), remaining), not phase_out(remaining).

        Scenario (MFJ 2025):
          limit       = $7,000
          trad_contrib = $2,000  → remaining = $5,000
          MAGI        = $241,000  (50 % through $236k–$246k phase-out)
          phase_out(limit)    = $7,000 × 50% = $3,500  ✓ correct
          phase_out(remaining)= $5,000 × 50% = $2,500  ✗ buggy result
          allowed     = min($3,500, $5,000)   = $3,500
        """
        tax_year = 2025
        filing = "MFJ"
        magi = 241_000.0  # midpoint of $236k–$246k band
        trad_contrib = 2_000.0

        limit = contrib_limit_for_year(tax_year)
        remaining = limit - trad_contrib
        lower, upper = roth_phaseout_for_year(tax_year, filing)

        # Confirm the scenario constants we depend on
        assert lower == 236_000.0
        assert upper == 246_000.0
        assert limit == 7_000.0
        assert remaining == 5_000.0

        # The correct calculation (fix)
        allowed = _roth_allowed(magi, lower, upper, limit, remaining)

        # Buggy path would give phase_out(remaining) = 2500; correct is 3500
        assert allowed == pytest.approx(3_500.0), (
            "Expected allowed=3500 (phase_out applied to full limit $7000, "
            f"capped at remaining $5000); got {allowed}"
        )

    def test_phase_out_capped_at_remaining_when_below_phaseout(self) -> None:
        """When MAGI is below the phase-out start, phase_out returns full limit,
        so allowed is capped at remaining."""
        tax_year = 2025
        filing = "MFJ"
        magi = 235_000.0  # below lower → phase_out returns full limit ($7000)
        trad_contrib = 1_000.0

        limit = contrib_limit_for_year(tax_year)
        remaining = limit - trad_contrib  # $6000
        lower, upper = roth_phaseout_for_year(tax_year, filing)

        allowed = _roth_allowed(magi, lower, upper, limit, remaining)
        assert allowed == pytest.approx(remaining)  # capped at $6000

    def test_no_trad_contrib_unchanged(self) -> None:
        """Without any prior Trad contribution (remaining == limit), the fix
        produces the same result as the old code (remaining == limit → same arg)."""
        tax_year = 2025
        filing = "MFJ"
        magi = 241_000.0
        trad_contrib = 0.0

        limit = contrib_limit_for_year(tax_year)
        remaining = limit - trad_contrib  # == limit
        lower, upper = roth_phaseout_for_year(tax_year, filing)

        allowed_new = _roth_allowed(magi, lower, upper, limit, remaining)
        # Old code: _phase_out(magi, lower, upper, remaining) — same since remaining==limit
        allowed_old = _phase_out(magi, lower, upper, remaining)
        assert allowed_new == pytest.approx(allowed_old)

    def test_single_filer_mid_phaseout_with_trad_contrib(self) -> None:
        """Single filer in phase-out band with prior Trad contribution.

        Scenario (Single 2025):
          limit      = $7,000
          trad_contrib= $3,000  → remaining = $4,000
          MAGI       = $157,500  (50% through $150k–$165k band)
          phase_out(limit) = $7,000 × 50% = $3,500
          allowed    = min($3,500, $4,000) = $3,500
        """
        tax_year = 2025
        filing = "Single"
        magi = 157_500.0
        trad_contrib = 3_000.0

        limit = contrib_limit_for_year(tax_year)
        remaining = limit - trad_contrib  # $4000
        lower, upper = roth_phaseout_for_year(tax_year, filing)  # $150k–$165k

        assert lower == 150_000.0
        assert upper == 165_000.0

        allowed = _roth_allowed(magi, lower, upper, limit, remaining)
        assert allowed == pytest.approx(3_500.0)
