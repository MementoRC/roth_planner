"""Household data model — single source of truth for all personal inputs."""

from __future__ import annotations

from dataclasses import dataclass, field

from config.loader import load_defaults
from models.grants import StockGrant

_D = load_defaults()


@dataclass
class GrowthProfile:
    """Per-account growth rate with optional yield/appreciation split.

    default_rate: TOTAL annual return (yield + appreciation), e.g. 0.07 for 7%
    yearly_overrides: {year: total_rate} for years with known/historical returns
    yield_rate: dividend yield component (taxable brokerage only; 0.0 for IRAs)
    qualified_fraction: share of yield_rate that is qualified dividends (LTCG-rate)
    yield_overrides: {year: yield_rate} for known-yield years
    """

    default_rate: float = 0.07
    yearly_overrides: dict[int, float] = field(default_factory=dict)
    yield_rate: float = 0.0
    qualified_fraction: float = 1.0
    yield_overrides: dict[int, float] = field(default_factory=dict)

    def rate_for(self, year: int) -> float:
        return self.yearly_overrides.get(year, self.default_rate)

    def yield_for(self, year: int) -> float:
        return self.yield_overrides.get(year, self.yield_rate)

    def appreciation_for(self, year: int) -> float:
        return self.rate_for(year) - self.yield_for(year)

    def qualified_div_for(self, year: int, balance: float) -> float:
        return balance * self.yield_for(year) * self.qualified_fraction

    def ordinary_div_for(self, year: int, balance: float) -> float:
        return balance * self.yield_for(year) * (1.0 - self.qualified_fraction)


@dataclass
class Household:
    """All inputs for the Roth conversion model."""

    # Ages (in base_year)
    your_age: int = _D["your_age"]
    spouse_age: int = _D["spouse_age"]
    base_year: int = 2026

    # IRA balances (beginning of base_year)
    your_ira: float = _D["your_ira"]
    spouse_ira: float = _D["spouse_ira"]

    # Social Security (monthly at FRA 67)
    your_ss_fra: float = _D["your_ss_fra"]  # $/month at FRA
    spouse_ss_fra: float = _D["spouse_ss_fra"]
    ss_start_age: int = 70  # DEPRECATED — use your_ss_start_age / spouse_ss_start_age
    your_ss_start_age: int = 70  # age you begin claiming SS (62–70)
    spouse_ss_start_age: int = 70  # age spouse begins claiming SS (62–70)
    ss_cola: float = 0.025  # 2.5% annual COLA

    # Growth & inflation
    growth_rate: float = 0.07  # legacy flat rate (used as default for all accounts)
    expense_inflation: float = 0.03

    # Per-account growth profiles (None = use growth_rate as default)
    your_ira_growth: GrowthProfile | None = None
    spouse_ira_growth: GrowthProfile | None = None
    brokerage_growth: GrowthProfile | None = None

    # Living expenses (annual, today's dollars)
    living_expenses: float = _D["living_expenses"]

    # Tax parameters (2025 TCJA/OBBBA permanent)
    std_deduction: float = 32_200  # MFJ
    senior_extra: float = 1_650  # per person 65+
    filing_status: str = "MFJ"

    # Brokerage assumptions
    brok_turnover: float = 0.30  # 30% annual turnover
    ltcg_rate: float = 0.15

    # Stock option grants
    grants: list[StockGrant] = field(default_factory=lambda: list(_D["grants"]))
    txn_price_now: float = _D["stock_price_now"]  # current stock price
    txn_price_late: float = _D["stock_price_late"]  # projected price at expiry

    # FRA (Full Retirement Age for SS benefit calculation)
    your_fra_age: int = 67  # 67 for 1960+ cohort; 66 or 66+N/12 for earlier cohorts
    spouse_fra_age: int = 67  # 67 for 1960+ cohort; 66 or 66+N/12 for earlier cohorts

    # RMD
    rmd_start_age: int = 75  # DEPRECATED — use your_rmd_start_age / spouse_rmd_start_age
    your_rmd_start_age: int = 75  # SECURE 2.0 default; pre-1960 cohort uses 73
    spouse_rmd_start_age: int = 75  # SECURE 2.0 default; pre-1960 cohort uses 73

    # Healthcare coverage
    your_aca_enrolled: bool = False  # you on ACA marketplace (vs employer plan)
    spouse_aca_enrolled: bool = False  # spouse on ACA marketplace
    aca_benchmark_premium_annual: float = 21_600.0  # 2nd-lowest-cost Silver plan annual cost (household-level; varies by state/county/age)

    # QCD
    qcd_limit: float = 111_000  # 2026 annual limit per person (inflation-indexed)

    @property
    def age_gap(self) -> int:
        return self.your_age - self.spouse_age

    @property
    def your_conv_window(self) -> int:
        """Years you can convert (age now through your_rmd_start_age - 1)."""
        return max(self.your_rmd_start_age - 1 - self.your_age + 1, 0)

    @property
    def spouse_conv_window(self) -> int:
        """Years spouse can convert (age now through spouse_rmd_start_age - 1)."""
        return max(self.spouse_rmd_start_age - 1 - self.spouse_age + 1, 0)

    def your_age_in(self, year: int) -> int:
        return self.your_age + (year - self.base_year)

    def spouse_age_in(self, year: int) -> int:
        return self.spouse_age + (year - self.base_year)

    def your_ss_at_70(self) -> float:
        """Annual SS if delayed to 70 (8%/yr past your FRA)."""
        delay_years = self.your_ss_start_age - self.your_fra_age
        factor = 1 + delay_years * 0.08
        return self.your_ss_fra * factor * 12

    def spouse_ss_at_70(self) -> float:
        """Annual SS if delayed to 70 (8%/yr past spouse FRA)."""
        delay_years = self.spouse_ss_start_age - self.spouse_fra_age
        factor = 1 + delay_years * 0.08
        return self.spouse_ss_fra * factor * 12

    def your_ira_rate(self, year: int) -> float:
        """Growth rate for your IRA in a given year."""
        if self.your_ira_growth is not None:
            return self.your_ira_growth.rate_for(year)
        return self.growth_rate

    def spouse_ira_rate(self, year: int) -> float:
        """Growth rate for spouse's IRA in a given year."""
        if self.spouse_ira_growth is not None:
            return self.spouse_ira_growth.rate_for(year)
        return self.growth_rate

    def brokerage_rate(self, year: int) -> float:
        """Growth rate for brokerage in a given year."""
        if self.brokerage_growth is not None:
            return self.brokerage_growth.rate_for(year)
        return self.growth_rate

    def option_income(self, year: int, early: bool = True) -> float:
        """Ordinary income from exercising the grant expiring ~this year."""
        if early:
            # Early exercise: 2026=grant0, 2027=grant1, 2028=grant2
            idx = year - self.base_year
            if 0 <= idx < len(self.grants):
                return self.grants[idx].spread(self.txn_price_now)
        else:
            # Late exercise: at expiry
            for g in self.grants:
                if g.expiry_year == year:
                    return g.spread(self.txn_price_late)
        return 0.0
