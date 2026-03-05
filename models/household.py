"""Household data model — single source of truth for all personal inputs."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StockGrant:
    """Non-qualified stock option grant."""

    year: int  # grant year (e.g. 2019)
    strike: float  # strike price per share
    shares: int  # exercisable shares
    expiry_year: int  # expiration year

    def spread(self, price: float) -> float:
        return max(price - self.strike, 0) * self.shares


@dataclass
class Household:
    """All inputs for the Roth conversion model."""

    # Ages (in base_year)
    your_age: int = 61
    spouse_age: int = 55
    base_year: int = 2026

    # IRA balances (beginning of base_year)
    your_ira: float = 1_700_000
    spouse_ira: float = 1_700_000

    # Social Security (monthly at FRA 67)
    your_ss_fra: float = 3_800  # $/month at FRA
    spouse_ss_fra: float = 3_800
    ss_start_age: int = 70  # both delay to 70
    ss_cola: float = 0.025  # 2.5% annual COLA

    # Growth & inflation
    growth_rate: float = 0.07
    expense_inflation: float = 0.03

    # Living expenses (annual, today's dollars)
    living_expenses: float = 30_000

    # Tax parameters (2025 TCJA/OBBBA permanent)
    std_deduction: float = 32_200  # MFJ
    senior_extra: float = 1_650  # per person 65+
    filing_status: str = "MFJ"

    # Brokerage assumptions
    brok_turnover: float = 0.30  # 30% annual turnover
    ltcg_rate: float = 0.15

    # Stock option grants
    grants: list[StockGrant] = field(
        default_factory=lambda: [
            StockGrant(2019, 104.41, 650, 2029),
            StockGrant(2020, 130.52, 763, 2030),
            StockGrant(2021, 169.23, 450, 2031),
        ]
    )
    txn_price_now: float = 212  # current TXN price
    txn_price_late: float = 250  # projected price at expiry

    # RMD
    rmd_start_age: int = 75  # SECURE 2.0 for born after 1960

    # QCD
    qcd_limit: float = 105_000  # 2025 annual limit per person

    @property
    def age_gap(self) -> int:
        return self.your_age - self.spouse_age

    @property
    def your_conv_window(self) -> int:
        """Years you can convert (age now through 74)."""
        return max(self.rmd_start_age - 1 - self.your_age + 1, 0)

    @property
    def spouse_conv_window(self) -> int:
        """Years spouse can convert (age 60 through 74)."""
        start = max(self.spouse_age, 60)
        return max(self.rmd_start_age - 1 - start + 1, 0)

    def your_age_in(self, year: int) -> int:
        return self.your_age + (year - self.base_year)

    def spouse_age_in(self, year: int) -> int:
        return self.spouse_age + (year - self.base_year)

    def your_ss_at_70(self) -> float:
        """Annual SS if delayed to 70 (8%/yr for 3 years past FRA 67)."""
        delay_years = self.ss_start_age - 67
        factor = 1 + delay_years * 0.08  # 24% increase
        return self.your_ss_fra * factor * 12

    def spouse_ss_at_70(self) -> float:
        delay_years = self.ss_start_age - 67
        factor = 1 + delay_years * 0.08
        return self.spouse_ss_fra * factor * 12

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
