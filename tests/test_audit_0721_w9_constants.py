"""Regression test for audit-0721 C-const / B2.

ACA_ENHANCED_SCHEDULE's 300-400% FPL band was 0.075, breaking the
ARPA/IRA monotonic applicable-percentage ramp (0%/2%/4%/6%/8.5% across
150-400% FPL, 8.5% flat above 400%). Fixed to 0.085.
"""

from engine.aca import ACA_ENHANCED_SCHEDULE


def test_aca_enhanced_schedule_applicable_pct_is_monotonic_non_decreasing() -> None:
    rates = [rate for _upper_fpl, rate in ACA_ENHANCED_SCHEDULE]
    for earlier, later in zip(rates, rates[1:], strict=True):
        assert earlier <= later, (
            f"ACA_ENHANCED_SCHEDULE applicable-% must be non-decreasing, "
            f"found {earlier} followed by {later}"
        )


def test_aca_enhanced_schedule_300_to_400_pct_fpl_band_is_085() -> None:
    band = next(rate for upper_fpl, rate in ACA_ENHANCED_SCHEDULE if upper_fpl == 4.00)
    assert band == 0.085
