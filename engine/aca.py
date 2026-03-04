"""ACA Marketplace subsidy calculator for pre-Medicare coverage.

Applies only ages 61-64 (before Medicare at 65).
Enhanced subsidies (ARPA/IRA) cap premiums at % of income based on FPL.
"""

# 2025 Federal Poverty Level, family of 2 (continental US)
FPL_2 = 20_440

# Enhanced premium cap schedule (% of income)
# (upper_fpl_multiple, premium_cap_rate)
ACA_CAP_SCHEDULE = [
    (1.50, 0.00),    # Below 150% FPL: $0 premium
    (2.00, 0.02),    # 150-200%: up to 2%
    (2.50, 0.04),    # 200-250%: up to 4%
    (3.00, 0.06),    # 250-300%: up to 6%
    (4.00, 0.075),   # 300-400%: up to 7.5%
    (float('inf'), 0.085),  # 400%+: 8.5% cap
]

# Approximate annual benchmark silver plan premium for couple age ~60-64
# (varies by state/county — $1,600-$2,000/mo range; using $1,800/mo)
BENCHMARK_PREMIUM_ANNUAL = 1_800 * 12


def aca_premium_cap_rate(magi: float) -> float:
    """Premium cap as fraction of income based on FPL multiple."""
    fpl_ratio = magi / FPL_2
    for upper_fpl, cap_rate in ACA_CAP_SCHEDULE:
        if fpl_ratio <= upper_fpl:
            return cap_rate
    return 0.085


def aca_subsidy(magi: float, benchmark: float = BENCHMARK_PREMIUM_ANNUAL) -> float:
    """
    Calculate ACA premium tax credit (subsidy).

    Subsidy = benchmark_premium - (income × cap_rate)
    Cannot be negative.
    """
    cap_rate = aca_premium_cap_rate(magi)
    expected_contribution = magi * cap_rate
    subsidy = max(benchmark - expected_contribution, 0)
    return subsidy


def aca_subsidy_loss(base_magi: float, new_magi: float,
                     benchmark: float = BENCHMARK_PREMIUM_ANNUAL) -> float:
    """
    How much ACA subsidy is lost due to additional income (e.g., conversion).
    """
    base = aca_subsidy(base_magi, benchmark)
    new = aca_subsidy(new_magi, benchmark)
    return max(base - new, 0)


def aca_net_cost(magi: float, benchmark: float = BENCHMARK_PREMIUM_ANNUAL) -> float:
    """What you actually pay for the silver plan after subsidy."""
    return max(benchmark - aca_subsidy(magi, benchmark), 0)


def aca_applies(your_age: int) -> bool:
    """ACA marketplace only relevant if under 65 (pre-Medicare)."""
    return your_age < 65
