"""Dataclass shapes and constants for portfolio sync (sealed data types)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


ASSET_CLASS: dict[str, str] = {
    # --- iShares ETFs (Fidelity) ---
    "ITOT": "equity",  # Core S&P Total US Stock Market
    "AGG": "bond",  # Core US Aggregate Bond
    "IXUS": "equity",  # Core MSCI Total Intl
    "SHV": "cash",  # 0-1 Year Treasury (cash equivalent)
    "IVV": "equity",  # Core S&P 500
    "IDEV": "equity",  # Core MSCI Intl Developed
    # --- Fidelity crypto ---
    "FBTC": "crypto",  # Wise Origin Bitcoin
    "FETH": "crypto",  # Ethereum Fund
    # --- Fidelity funds ---
    "FFIZX": "target_date",  # Freedom Index 2040
    "FLRG": "equity",  # US Multifactor
    "FIGB": "bond",  # Investment Grade Bond
    "FDEV": "equity",  # Intl Multifactor
    # --- Vanguard target-date ---
    "VTTHX": "target_date",  # Target Ret 2035
    "VTHRX": "target_date",  # Target Ret 2030
    # --- Vanguard active/value ---
    "DFFVX": "equity",  # DFA US Target Value
    "VDIGX": "equity",  # Dividend Growth
    "HLMIX": "equity",  # Harding Loevner Intl Eq
    # --- Vanguard ETFs ---
    "VTI": "equity",  # Total Stock Market
    "VXUS": "equity",  # Total Intl Stock
    "BND": "bond",  # Total Bond Market
    "BNDX": "bond",  # Total Intl Bond
    # --- Vanguard Admiral/Investor ---
    "VEMAX": "equity",  # Emerging Markets
    "VIMAX": "equity",  # Mid Cap
    "VPADX": "equity",  # Pacific Stock
    "VWESX": "bond",  # Long-Term Investment Grade
    # --- Company stock ---
    "TXN": "equity",
}


EXPECTED_RETURNS: dict[str, float] = {
    "equity": 0.09,
    "bond": 0.04,
    "cash": 0.045,  # money market / short-term treasury
    "crypto": 0.00,  # too volatile to project — use 0 for planning
    "target_date": 0.07,  # blended (typically ~60/40 glide path)
}


@dataclass
class Holding:
    """A single position in a brokerage account."""

    symbol: str
    description: str
    quantity: float
    market_value: float
    account_name: str
    asset_class: str  # "equity", "bond", "cash", "crypto", "target_date"
    total_gain_loss: float | None = None
    total_gain_loss_pct: float | None = None
    # FinExtract Phase B: per-holding dividend history (populated by ingestion server's
    # /query/brokerage?data_type=dividends_rollup endpoint; None when not yet available).
    dividends_by_year: dict[str, float] | None = None
    dividends_window: dict[str, str] | None = None
    dividends_is_stale: bool | None = None


@dataclass
class AccountSummary:
    """Aggregated view of one account."""

    account_type: str  # "brokerage", "roth_ira", "trad_ira", "403b", "hsa"
    owner: str  # "you" or "spouse"
    account_name: str = ""  # raw account name from scraper
    total_value: float = 0.0
    equity_value: float = 0.0
    bond_value: float = 0.0
    cash_value: float = 0.0
    crypto_value: float = 0.0
    target_date_value: float = 0.0
    holdings: list[Holding] = field(default_factory=list)

    @property
    def equity_pct(self) -> float:
        return self.equity_value / self.total_value if self.total_value > 0 else 0.0

    @property
    def weighted_return(self) -> float:
        """Expected return based on current allocation."""
        if self.total_value <= 0:
            return 0.0
        total = 0.0
        for cls, ret in EXPECTED_RETURNS.items():
            total += getattr(self, f"{cls}_value", 0.0) * ret
        return total / self.total_value

    @property
    def is_pretax(self) -> bool:
        """True if this is a pre-tax retirement account (IRA, 403b)."""
        return self.account_type in ("trad_ira", "403b")

    @property
    def is_roth(self) -> bool:
        """True if this is a Roth IRA account."""
        return self.account_type == "roth_ira"


@dataclass
class EquityGrant:
    """An active stock option or RSU grant."""

    grant_id: str
    grant_type: str  # "NQO" or "RSU"
    grant_date: str
    shares_granted: int
    outstanding: int
    current_value: float


@dataclass
class PortfolioSnapshot:
    """Complete portfolio state from the scraper."""

    accounts: list[AccountSummary] = field(default_factory=list)
    equity_grants: list[EquityGrant] = field(default_factory=list)
    txn_shares_held: int = 0
    txn_shares_value: float = 0.0
    server_available: bool = False
    error: str | None = None
    # FinExtract PRs #19/#20/#21: equity_sales from .portfolio_cache.json
    equity_sales_lots: list[dict[str, Any]] = field(default_factory=list)
    equity_sales_executions: list[dict[str, Any]] = field(default_factory=list)
    order_detail_summary_captured_at: str = ""

    def account_by_type(self, acct_type: str) -> AccountSummary | None:
        """Find first account matching type."""
        return next((a for a in self.accounts if a.account_type == acct_type), None)

    @property
    def pretax_accounts(self) -> list[AccountSummary]:
        """All pre-tax retirement accounts (IRA + 403b)."""
        return [a for a in self.accounts if a.is_pretax]

    @property
    def pretax_total(self) -> float:
        """Total value of all pre-tax retirement accounts."""
        return sum(a.total_value for a in self.pretax_accounts)

    @property
    def pretax_weighted_return(self) -> float:
        """Weighted return across all pre-tax accounts (both owners combined)."""
        total = self.pretax_total
        if total <= 0:
            return 0.0
        return sum(a.total_value * a.weighted_return for a in self.pretax_accounts) / total

    def pretax_weighted_return_for(self, owner: str) -> float:
        """Weighted return across pre-tax accounts for a single owner.

        Returns the joint pretax_weighted_return as fallback when the owner
        has no pretax accounts (avoids divide-by-zero and preserves prior
        behaviour for callers that don't know the owner's balance upfront).
        """
        accounts = [a for a in self.pretax_accounts if a.owner == owner]
        total = sum(a.total_value for a in accounts)
        if total <= 0:
            return self.pretax_weighted_return
        return sum(a.total_value * a.weighted_return for a in accounts) / total

    @property
    def brokerage_accounts(self) -> list[AccountSummary]:
        """All accounts where account_type == 'brokerage', regardless of owner."""
        return [a for a in self.accounts if a.account_type == "brokerage"]

    @property
    def brokerage_total(self) -> float:
        """Total value across all brokerage accounts, both owners."""
        return sum(a.total_value for a in self.brokerage_accounts)

    @property
    def brokerage_weighted_return(self) -> float:
        """Balance-weighted average return across all brokerage accounts."""
        accounts = self.brokerage_accounts
        total = sum(a.total_value for a in accounts)
        if total <= 0:
            return 0.0
        return sum(a.weighted_return * a.total_value for a in accounts) / total

    @property
    def total_portfolio_value(self) -> float:
        return sum(a.total_value for a in self.accounts) + self.txn_shares_value


@dataclass
class SSABenefitEstimate:
    """One row from FinExtract's ssa-retirement-benefit-estimates-v1 schema."""

    retirement_age: int
    claim_date: str
    benefit_type: str
    monthly_amount: float


@dataclass
class SSASnapshot:
    """Parsed SSA benefit-estimate data for one person (you or spouse)."""

    estimates: list[SSABenefitEstimate] = field(default_factory=list)
    server_available: bool = False
    error: str | None = None


@dataclass
class DividendsRollupSnapshot:
    """FinExtract /query/brokerage?data_type=dividends_rollup response.

    Preserves the FinExtract emitted shape verbatim — translation to the
    per-Holding fields happens in apply_dividends_rollup().
    """

    server_available: bool = False
    by_symbol: dict[str, dict[str, Any]] = field(default_factory=dict)
    window: dict[str, Any] = field(
        default_factory=dict
    )  # original from/to keys (plus optional metadata)
    freshness: dict[str, Any] = field(default_factory=dict)  # original is_stale, as_of, etc.
    error: str | None = None


@dataclass
class OptionExercisesSnapshot:
    """FinExtract /query/equity_compensation?data_type=order_detail_summary response.

    Aggregates NQO ordinary-income spread from UBS EPAS order_detail_summary rows.
    server_available=False means transport failure; server_available=True with
    total_spread=0 means the endpoint responded but no exercises are recorded yet.
    """

    server_available: bool = False
    error: str = ""
    total_spread: float = 0.0  # Aggregate NQO ordinary spread across all rows
    by_grant_id: dict[str, float] = field(default_factory=dict)  # grant_id -> spread $
    warnings: list[str] = field(default_factory=list)
    rows_count: int = 0
    captured_at: str = ""
    sale_info_by_grant: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # grant_id -> {grant_year, strike, shares_ytd}


@dataclass
class MagiSnapshot:
    """MAGI history from FinExtract /query/tax_return?data_type=magi.

    Shipped with 2-year coverage (batchTaxYear, batchTaxYear-1) per A3.
    Feeds Household.prior_year_magi for IRMAA 2-year lookback in engine/scenario.py.
    """

    fetched_at: datetime
    prior_year_magi: dict[int, float] = field(default_factory=dict)
    agi: dict[int, float] = field(default_factory=dict)
    filing_status: dict[int, str] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
