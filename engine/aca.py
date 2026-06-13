"""ACA Marketplace subsidy calculator for pre-Medicare coverage.

Applies only ages 61-64 (before Medicare at 65).
Enhanced subsidies (ARPA/IRA) cap premiums at % of income based on FPL.

NOTE: Enhanced subsidies expired Dec 31, 2025. The House passed a 3-year
extension on Jan 8, 2026, but it has not been signed into law yet.
Toggle ENHANCED_SUBSIDIES_ACTIVE to model either scenario.
"""

# 2025 Federal Poverty Level guidelines (used for 2026 coverage, continental US)
FPL_1 = 15_060  # single person
FPL_2 = 21_150  # family of 2

# Legislative status: Enhanced subsidies (ARPA/IRA) expired Dec 31, 2025.
# Pending 3-year extension as of Jan 2026. Toggle this flag to model scenarios.
ENHANCED_SUBSIDIES_ACTIVE = False

# Enhanced premium cap schedule (% of income) — ARPA/IRA rules
# (upper_fpl_multiple, premium_cap_rate)
ACA_ENHANCED_SCHEDULE = [
    (1.50, 0.00),  # Below 150% FPL: $0 premium
    (2.00, 0.02),  # 150-200%: up to 2%
    (2.50, 0.04),  # 200-250%: up to 4%
    (3.00, 0.06),  # 250-300%: up to 6%
    (4.00, 0.075),  # 300-400%: up to 7.5%
    (float("inf"), 0.085),  # 400%+: 8.5% cap
]

# Pre-ARP schedule (reverted Jan 1, 2026 — subsidies only up to 400% FPL)
# Source: Rev. Proc. 2025-25 (IRB 2025-32, Aug 4 2025)
# Each tuple is (upper_fpl_multiple, applicable_pct_at_bracket_start).
# The IRS table defines linear ramps within each bracket; these entries capture
# the rate at the START of each bracket (i.e. the lower-bound applicable %).
ACA_PRE_ARP_SCHEDULE = [
    (1.33, 0.0210),  # 100-133% FPL: 2.10% flat
    (1.50, 0.0314),  # 133-150%: ramp 3.14% → 4.19%
    (2.00, 0.0419),  # 150-200%: ramp 4.19% → 6.60%
    (2.50, 0.0660),  # 200-250%: ramp 6.60% → 8.44%
    (3.00, 0.0844),  # 250-300%: ramp 8.44% → 9.96%
    (4.00, 0.0996),  # 300-400%: 9.96% flat
]

# Approximate annual benchmark silver plan premium for couple age ~60-64
# (varies by state/county — $1,600-$2,000/mo range; using $1,800/mo)
BENCHMARK_PREMIUM_ANNUAL = 1_800 * 12


def _aca_cap_schedule(enhanced: bool) -> list[tuple[float, float]]:
    """Return the premium cap schedule for the given subsidy law state."""
    return ACA_ENHANCED_SCHEDULE if enhanced else ACA_PRE_ARP_SCHEDULE


def _fpl(filing_status: str) -> float:
    """Return the applicable FPL for the given filing status."""
    return FPL_1 if filing_status == "Single" else FPL_2


def aca_premium_cap_rate(
    magi: float,
    enhanced_subsidies_active: bool = ENHANCED_SUBSIDIES_ACTIVE,
    filing_status: str = "MFJ",
) -> float:
    """Premium cap as fraction of income based on FPL multiple."""
    fpl_ratio = magi / _fpl(filing_status)
    # Pre-ARP cliff: above 400% FPL there is no subsidy, so cap rate is irrelevant.
    # Guard here prevents AssertionError on direct callers that skip aca_subsidy().
    if not enhanced_subsidies_active and fpl_ratio > 4.0:
        return 0.0
    for upper_fpl, cap_rate in _aca_cap_schedule(enhanced_subsidies_active):
        if fpl_ratio <= upper_fpl:
            return cap_rate
    # Unreachable for pre-ARP: guard above handles fpl_ratio > 4.0.
    # For enhanced schedule the final entry is (inf, 0.085) which always matches.
    raise AssertionError(
        f"aca_premium_cap_rate: no schedule entry matched fpl_ratio={fpl_ratio:.3f}"
    )


def aca_subsidy(
    magi: float,
    benchmark: float = BENCHMARK_PREMIUM_ANNUAL,
    enhanced_subsidies_active: bool = ENHANCED_SUBSIDIES_ACTIVE,
    filing_status: str = "MFJ",
) -> float:
    """
    Calculate ACA premium tax credit (subsidy).

    Subsidy = benchmark_premium - (income × cap_rate)
    Cannot be negative.

    When using pre-ARP schedule, no subsidies above 400% FPL.
    """
    # Check 400% FPL cliff for pre-ARP schedule
    if not enhanced_subsidies_active and magi > 4.0 * _fpl(filing_status):
        return 0.0

    cap_rate = aca_premium_cap_rate(magi, enhanced_subsidies_active, filing_status)
    expected_contribution = magi * cap_rate
    return max(benchmark - expected_contribution, 0)


def aca_subsidy_loss(
    base_magi: float,
    new_magi: float,
    benchmark: float = BENCHMARK_PREMIUM_ANNUAL,
    enhanced_subsidies_active: bool = ENHANCED_SUBSIDIES_ACTIVE,
    filing_status: str = "MFJ",
) -> float:
    """
    How much ACA subsidy is lost due to additional income (e.g., conversion).
    """
    base = aca_subsidy(base_magi, benchmark, enhanced_subsidies_active, filing_status)
    new = aca_subsidy(new_magi, benchmark, enhanced_subsidies_active, filing_status)
    return max(base - new, 0)


def aca_net_cost(
    magi: float,
    benchmark: float = BENCHMARK_PREMIUM_ANNUAL,
    enhanced_subsidies_active: bool = ENHANCED_SUBSIDIES_ACTIVE,
    filing_status: str = "MFJ",
) -> float:
    """What you actually pay for the silver plan after subsidy."""
    return max(
        benchmark - aca_subsidy(magi, benchmark, enhanced_subsidies_active, filing_status), 0
    )


def aca_applies(your_age: int, enrolled: bool = True) -> bool:
    """ACA marketplace only relevant if under 65 AND enrolled."""
    return your_age < 65 and enrolled
