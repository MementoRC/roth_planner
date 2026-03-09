"""Sync live portfolio data from the FinExtract ingestion server.

Fetches brokerage holdings and equity compensation data, then maps
them into Household parameters and GrowthProfile overrides.

The ingestion server runs at http://127.0.0.1:7890 and requires
Bearer token authentication via FINEXTRACT_TOKEN env var.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import requests

BASE_URL = os.environ.get("FINEXTRACT_URL", "http://127.0.0.1:7890")
TOKEN = os.environ.get("FINEXTRACT_TOKEN", "")

# Asset classification: symbol -> "equity" or "bond"
ASSET_CLASS = {
    # Vanguard ETFs
    "VTI": "equity",    # Total Stock Market
    "VXUS": "equity",   # Total Intl Stock
    "BND": "bond",      # Total Bond Market
    "BNDX": "bond",     # Total Intl Bond
    # Vanguard Admiral/Investor funds
    "VEMAX": "equity",  # Emerging Markets
    "VIMAX": "equity",  # Mid Cap
    "VPADX": "equity",  # Pacific Stock
    "VWESX": "bond",    # Long-Term Investment Grade
    # Company stock
    "TXN": "equity",
}

# Expected long-term returns by asset class
EXPECTED_RETURNS = {
    "equity": 0.09,
    "bond": 0.04,
}


@dataclass
class Holding:
    """A single position in a brokerage account."""

    symbol: str
    description: str
    quantity: float
    market_value: float
    account_name: str
    asset_class: str  # "equity" or "bond"
    total_gain_loss: float | None = None
    total_gain_loss_pct: float | None = None


@dataclass
class AccountSummary:
    """Aggregated view of one account."""

    account_type: str  # "brokerage", "roth_ira", "trad_ira"
    owner: str  # "you" or "spouse"
    total_value: float = 0.0
    equity_value: float = 0.0
    bond_value: float = 0.0
    holdings: list[Holding] = field(default_factory=list)

    @property
    def equity_pct(self) -> float:
        return self.equity_value / self.total_value if self.total_value > 0 else 0.0

    @property
    def weighted_return(self) -> float:
        """Expected return based on current allocation."""
        if self.total_value <= 0:
            return 0.0
        eq_ret = self.equity_value * EXPECTED_RETURNS["equity"]
        bd_ret = self.bond_value * EXPECTED_RETURNS["bond"]
        return (eq_ret + bd_ret) / self.total_value


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

    def account_by_type(self, acct_type: str) -> AccountSummary | None:
        """Find first account matching type."""
        return next((a for a in self.accounts if a.account_type == acct_type), None)

    @property
    def total_portfolio_value(self) -> float:
        return sum(a.total_value for a in self.accounts) + self.txn_shares_value


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _classify_account(account_name: str) -> tuple[str, str]:
    """Determine account type and owner from account name string.

    Returns (account_type, owner) where:
        account_type: "brokerage", "roth_ira", or "trad_ira"
        owner: "you" or "spouse"
    """
    name_lower = account_name.lower()

    if "roth ira" in name_lower:
        acct_type = "roth_ira"
    elif "ira" in name_lower and "roth" not in name_lower:
        acct_type = "trad_ira"
    else:
        acct_type = "brokerage"

    # For now, all Vanguard accounts belong to "you"
    # Spouse accounts would be detected by name or separate institution
    owner = "you"

    return acct_type, owner


def _classify_symbol(symbol: str) -> str:
    """Classify a symbol as equity or bond. Unknown defaults to equity."""
    return ASSET_CLASS.get(symbol, "equity")


def fetch_holdings() -> list[dict[str, Any]]:
    """Fetch brokerage holdings from the ingestion server."""
    try:
        resp = requests.get(
            f"{BASE_URL}/query/brokerage",
            params={"data_type": "holdings"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except (requests.RequestException, ValueError):
        return []


def fetch_equity_awards() -> list[dict[str, Any]]:
    """Fetch equity compensation awards."""
    try:
        resp = requests.get(
            f"{BASE_URL}/query/equity_comp",
            params={"data_type": "equity_awards"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except (requests.RequestException, ValueError):
        return []


def fetch_shares() -> list[dict[str, Any]]:
    """Fetch equity compensation shares held."""
    try:
        resp = requests.get(
            f"{BASE_URL}/query/equity_comp",
            params={"data_type": "shares"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except (requests.RequestException, ValueError):
        return []


def fetch_portfolio() -> PortfolioSnapshot:
    """Fetch and assemble the complete portfolio snapshot."""
    snap = PortfolioSnapshot()

    # Check server availability
    try:
        resp = requests.get(f"{BASE_URL}/status", headers=_headers(), timeout=3)
        resp.raise_for_status()
        snap.server_available = True
    except requests.RequestException as e:
        snap.error = str(e)
        return snap

    # --- Brokerage holdings ---
    holdings_raw = fetch_holdings()
    accounts_map: dict[str, AccountSummary] = {}

    for row in holdings_raw:
        acct_name = row.get("account", "")
        acct_type, owner = _classify_account(acct_name)
        symbol = row.get("symbol", "")
        asset_class = _classify_symbol(symbol)
        mv = row.get("market_value", 0) or 0

        h = Holding(
            symbol=symbol,
            description=row.get("description", ""),
            quantity=row.get("quantity", 0),
            market_value=mv,
            account_name=acct_name,
            asset_class=asset_class,
            total_gain_loss=row.get("total_gain_loss"),
            total_gain_loss_pct=row.get("total_gain_loss_pct"),
        )

        key = f"{acct_type}:{owner}"
        if key not in accounts_map:
            accounts_map[key] = AccountSummary(account_type=acct_type, owner=owner)
        acct = accounts_map[key]
        acct.holdings.append(h)
        acct.total_value += mv
        if asset_class == "equity":
            acct.equity_value += mv
        else:
            acct.bond_value += mv

    snap.accounts = list(accounts_map.values())

    # --- Equity awards (NQO grants) ---
    awards_raw = fetch_equity_awards()
    for row in awards_raw:
        if row.get("outstanding", 0) > 0:
            snap.equity_grants.append(EquityGrant(
                grant_id=row.get("grant_id", ""),
                grant_type=row.get("grant_type", ""),
                grant_date=row.get("grant_date", ""),
                shares_granted=row.get("shares_granted", 0),
                outstanding=row.get("outstanding", 0),
                current_value=row.get("current_value", 0),
            ))

    # --- TXN shares held ---
    shares_raw = fetch_shares()
    snap.txn_shares_held = sum(r.get("shares_available", 0) for r in shares_raw)
    snap.txn_shares_value = sum(r.get("available_value", 0) for r in shares_raw)

    return snap
