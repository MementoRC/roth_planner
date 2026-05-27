"""Year-to-date income tracking for mid-year Roth conversion planning.

Captures realized capital gains from stop-loss triggers, wages, and other
income events so the conversion planner can compute accurate remaining
headroom against IRMAA, NIIT, and bracket thresholds.

Key distinction: LTCG affects MAGI (IRMAA/NIIT) but NOT ordinary bracket room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class RealizedGainEvent:
    """Individual sale/stop-loss event for display drill-down."""

    date: str  # ISO date string
    description: str
    proceeds: float
    cost_basis: float
    holding_period: str  # "short" or "long"
    account_name: str = ""

    @property
    def gain_loss(self) -> float:
        return self.proceeds - self.cost_basis

    @property
    def is_ltcg(self) -> bool:
        return self.holding_period == "long"


@dataclass
class YTDSnapshot:
    """Aggregate year-to-date income actuals.

    Used to override base-year projections with real-world events
    (stop-loss triggers, partial-year wages, etc.) so the conversion
    planner shows accurate remaining headroom.
    """

    tax_year: int = 2026
    snapshot_date: str = ""  # ISO date of last update

    # Ordinary income components
    wages_ytd: float = 0.0
    nec_income_ytd: float = 0.0  # 1099-NEC / self-employment
    ira_conversions_ytd: float = 0.0  # conversions already done this year
    ira_distributions_ytd: float = 0.0  # non-conversion IRA withdrawals

    # Investment income components
    ltcg_ytd: float = 0.0  # long-term capital gains (stop-loss triggers)
    stcg_ytd: float = 0.0  # short-term capital gains
    qualified_dividends_ytd: float = 0.0
    ordinary_dividends_ytd: float = 0.0
    interest_ytd: float = 0.0

    # Drill-down events
    gain_events: list[RealizedGainEvent] = field(default_factory=list)

    # Metadata
    manually_entered: bool = True

    @property
    def dividends_ytd(self) -> float:
        """Total YTD dividends (qualified + ordinary). Backward-compat."""
        return self.qualified_dividends_ytd + self.ordinary_dividends_ytd

    @property
    def total_ordinary_income(self) -> float:
        """Income that stacks into ordinary tax brackets.

        Includes: wages, NEC, STCG, conversions, distributions.
        Excludes: LTCG (taxed at preferential rate, not in brackets).
        """
        return (
            self.wages_ytd
            + self.nec_income_ytd
            + self.stcg_ytd
            + self.ira_conversions_ytd
            + self.ira_distributions_ytd
        )

    @property
    def total_investment_income(self) -> float:
        """Net investment income for NIIT calculation.

        NIIT applies to: LTCG + STCG + dividends + interest.
        Does NOT include wages, SS, or IRA distributions.
        """
        return self.ltcg_ytd + self.stcg_ytd + self.dividends_ytd + self.interest_ytd

    @property
    def magi_ytd(self) -> float:
        """Modified AGI for IRMAA/ACA threshold checks.

        Includes ALL income: ordinary + LTCG + dividends + interest.
        LTCG is in MAGI even though it's not in ordinary brackets.
        """
        return (
            self.wages_ytd
            + self.nec_income_ytd
            + self.stcg_ytd
            + self.ira_conversions_ytd
            + self.ira_distributions_ytd
            + self.ltcg_ytd
            + self.dividends_ytd
            + self.interest_ytd
        )

    def with_snapshot_date(self) -> YTDSnapshot:
        """Return copy with snapshot_date set to today."""
        self.snapshot_date = date.today().isoformat()
        return self
