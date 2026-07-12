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
class IncomeEvent:
    """A single logged Roth conversion or IRA distribution, entered as it happens.

    Custodian statements lag; the user is the only real-time source of truth
    for "I converted/withdrew $X today" — this is an audit-trail log, not a
    derived/synced value.
    """

    date: str  # ISO date string
    amount: float
    kind: str  # "conversion" or "distribution"
    owner: str = "you"  # "you" or "spouse"


def sum_income_events(events: list[IncomeEvent], *, kind: str, owner: str | None = None) -> float:
    """Sum event amounts matching kind (and owner, if given)."""
    return sum(e.amount for e in events if e.kind == kind and (owner is None or e.owner == owner))


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
    ira_conversions_ytd: float = 0.0  # conversions already done this year (your side)
    spouse_ira_conversions_ytd: float = 0.0  # spouse's conversions already done this year
    ira_distributions_ytd: float = 0.0  # non-conversion IRA withdrawals
    income_events: list[IncomeEvent] = field(default_factory=list)

    # Investment income components
    ltcg_ytd: float = 0.0  # long-term capital gains (stop-loss triggers)
    stcg_ytd: float = 0.0  # short-term capital gains
    qualified_dividends_ytd: float = 0.0
    ordinary_dividends_ytd: float = 0.0
    interest_ytd: float = 0.0
    tax_exempt_interest_ytd: float = 0.0  # muni bond interest: in MAGI, NOT in ordinary brackets
    nqo_exercise_ytd: float = 0.0  # NQO ordinary-income spread from realized exercises

    # Withholding / payments
    federal_withholding_ytd: float = 0.0  # W-2 federal tax withheld YTD

    # Above-the-line adjustments
    hsa_contribution_ytd: float = 0.0  # Form 8889 deductible HSA contribution (reduces AGI/MAGI)
    deductible_ira_contribution_ytd: float = (
        0.0  # Sch 1 deductible traditional-IRA contribution (reduces AGI/MAGI)
    )

    # Drill-down events
    gain_events: list[RealizedGainEvent] = field(default_factory=list)

    # Metadata
    manually_entered: bool = True

    @property
    def dividends_ytd(self) -> float:
        """Total YTD dividends (qualified + ordinary). Backward-compat."""
        return self.qualified_dividends_ytd + self.ordinary_dividends_ytd

    @property
    def above_the_line_adjustments_ytd(self) -> float:
        """HSA + deductible-IRA contributions; above-the-line, reduce AGI (hence MAGI and ordinary bracket base)."""
        return self.hsa_contribution_ytd + self.deductible_ira_contribution_ytd

    @property
    def total_ordinary_income(self) -> float:
        """Income that stacks into ordinary tax brackets.

        Includes: wages, NEC, STCG, conversions, distributions, ordinary dividends, interest.
        Excludes: LTCG and qualified dividends (taxed at preferential rates, not in brackets).
        Ordinary (non-qualified) dividends and interest are taxed as ordinary income and must
        count toward bracket headroom and SS taxation.
        Net of above-the-line HSA/IRA adjustments — this is the AGI-basis figure used for
        the ordinary bracket walk, since those deductions apply before brackets are computed.
        """
        return (
            self.wages_ytd
            + self.nec_income_ytd
            + self.stcg_ytd
            + self.ira_conversions_ytd
            + self.spouse_ira_conversions_ytd
            + self.ira_distributions_ytd
            + self.ordinary_dividends_ytd
            + self.interest_ytd
            + self.nqo_exercise_ytd
            - self.above_the_line_adjustments_ytd
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
        Tax-exempt interest (muni bonds) is included: IRMAA MAGI = AGI +
        tax-exempt interest + non-taxable SS + foreign earned income exclusion.
        MAGI is AGI-basis: above-the-line HSA/IRA adjustments reduce AGI and are
        therefore subtracted here too.
        """
        return (
            self.wages_ytd
            + self.nec_income_ytd
            + self.stcg_ytd
            + self.ira_conversions_ytd
            + self.spouse_ira_conversions_ytd
            + self.ira_distributions_ytd
            + self.ltcg_ytd
            + self.dividends_ytd
            + self.interest_ytd
            + self.tax_exempt_interest_ytd
            + self.nqo_exercise_ytd
            - self.above_the_line_adjustments_ytd
        )

    @property
    def niit_magi_ytd(self) -> float:
        """MAGI variant for NIIT (IRC §1411(d)(3)): excludes tax-exempt interest.

        NIIT MAGI differs from IRMAA MAGI in one respect: tax-exempt (muni bond)
        interest is excluded per §1411(d)(3). Use this when computing NIIT liability;
        use magi_ytd for IRMAA/ACA threshold checks.
        """
        return self.magi_ytd - self.tax_exempt_interest_ytd

    def with_snapshot_date(self) -> YTDSnapshot:
        """Set snapshot_date to today (in place) and return self for chaining."""
        self.snapshot_date = date.today().isoformat()
        return self
