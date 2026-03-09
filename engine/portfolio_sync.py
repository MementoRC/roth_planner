"""Sync live portfolio data from the FinExtract ingestion server.

Fetches brokerage holdings and equity compensation data, then maps
them into Household parameters and GrowthProfile overrides.

The ingestion server runs at http://127.0.0.1:7890 and requires
Bearer token authentication via FINEXTRACT_TOKEN env var.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

BASE_URL = os.environ.get("FINEXTRACT_URL", "http://127.0.0.1:7890")
TOKEN = os.environ.get("FINEXTRACT_TOKEN", "")

# Asset classification: symbol -> asset class
# "equity", "bond", "cash", "crypto", "target_date" (blended)
ASSET_CLASS: dict[str, str] = {
    # --- iShares ETFs (Fidelity) ---
    "ITOT": "equity",   # Core S&P Total US Stock Market
    "AGG": "bond",      # Core US Aggregate Bond
    "IXUS": "equity",   # Core MSCI Total Intl
    "SHV": "cash",      # 0-1 Year Treasury (cash equivalent)
    "IVV": "equity",    # Core S&P 500
    "IDEV": "equity",   # Core MSCI Intl Developed
    # --- Fidelity crypto ---
    "FBTC": "crypto",   # Wise Origin Bitcoin
    "FETH": "crypto",   # Ethereum Fund
    # --- Fidelity funds ---
    "FFIZX": "target_date",  # Freedom Index 2040
    "FLRG": "equity",   # US Multifactor
    "FIGB": "bond",     # Investment Grade Bond
    "FDEV": "equity",   # Intl Multifactor
    # --- Vanguard target-date ---
    "VTTHX": "target_date",  # Target Ret 2035
    "VTHRX": "target_date",  # Target Ret 2030
    # --- Vanguard active/value ---
    "DFFVX": "equity",  # DFA US Target Value
    "VDIGX": "equity",  # Dividend Growth
    "HLMIX": "equity",  # Harding Loevner Intl Eq
    # --- Vanguard ETFs ---
    "VTI": "equity",    # Total Stock Market
    "VXUS": "equity",   # Total Intl Stock
    "BND": "bond",      # Total Bond Market
    "BNDX": "bond",     # Total Intl Bond
    # --- Vanguard Admiral/Investor ---
    "VEMAX": "equity",  # Emerging Markets
    "VIMAX": "equity",  # Mid Cap
    "VPADX": "equity",  # Pacific Stock
    "VWESX": "bond",    # Long-Term Investment Grade
    # --- Company stock ---
    "TXN": "equity",
}

# Expected long-term returns by asset class
EXPECTED_RETURNS: dict[str, float] = {
    "equity": 0.09,
    "bond": 0.04,
    "cash": 0.045,      # money market / short-term treasury
    "crypto": 0.00,     # too volatile to project — use 0 for planning
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

    def accounts_by_type(self, acct_type: str) -> list[AccountSummary]:
        """Find all accounts matching type."""
        return [a for a in self.accounts if a.account_type == acct_type]

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
        """Weighted return across all pre-tax accounts."""
        total = self.pretax_total
        if total <= 0:
            return 0.0
        return sum(a.total_value * a.weighted_return for a in self.pretax_accounts) / total

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

    Handles Vanguard ("Claude R. Cirba — Roth IRA Brokerage Account — ..."),
    Fidelity ("Rollover IRA233813501"), and 403b/HSA patterns.

    Returns (account_type, owner).
    """
    name_lower = account_name.lower()

    if "roth ira" in name_lower or "roth" in name_lower:
        acct_type = "roth_ira"
    elif "403b" in name_lower or "403(b)" in name_lower:
        acct_type = "403b"
    elif "health savings" in name_lower or "hsa" in name_lower:
        acct_type = "hsa"
    elif "ira" in name_lower:
        # "Rollover IRA", "Traditional IRA", just "IRA"
        acct_type = "trad_ira"
    else:
        acct_type = "brokerage"

    # All accounts are "you" for now — spouse detection would need
    # separate institution or name matching
    owner = "you"

    return acct_type, owner


def _classify_symbol(symbol: str) -> str:
    """Classify a symbol as an asset class.

    Cash holdings from Fidelity have symbol like "Cash HELD IN MONEY MARKET"
    or "Cash FDIC-INSURED DEPOSIT SWEEP".
    """
    if symbol.lower().startswith("cash"):
        return "cash"
    return ASSET_CLASS.get(symbol, "equity")


def _parse_quantity(raw: Any) -> float:
    """Parse quantity which may be a string with commas or a number."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    # String like "2,182.861"
    try:
        return float(str(raw).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


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

        # For cash rows, description may be embedded in symbol
        description = row.get("description", "")
        if not description and symbol.lower().startswith("cash"):
            description = symbol
            symbol = "CASH"

        h = Holding(
            symbol=symbol,
            description=description,
            quantity=_parse_quantity(row.get("quantity")),
            market_value=mv,
            account_name=acct_name,
            asset_class=asset_class,
            total_gain_loss=row.get("total_gain_loss"),
            total_gain_loss_pct=row.get("total_gain_loss_pct"),
        )

        key = f"{acct_type}:{owner}:{acct_name}"
        if key not in accounts_map:
            accounts_map[key] = AccountSummary(
                account_type=acct_type, owner=owner, account_name=acct_name,
            )
        acct = accounts_map[key]
        acct.holdings.append(h)
        acct.total_value += mv

        # Accumulate by asset class
        attr = f"{asset_class}_value"
        if hasattr(acct, attr):
            setattr(acct, attr, getattr(acct, attr) + mv)
        else:
            acct.equity_value += mv  # fallback

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


# --- Persistence ---

_CACHE_PATH = Path(__file__).resolve().parent.parent / ".portfolio_cache.json"


def save_snapshot(snap: PortfolioSnapshot) -> None:
    """Save portfolio snapshot to disk as JSON."""
    _CACHE_PATH.write_text(json.dumps(asdict(snap), indent=2))


def load_snapshot() -> PortfolioSnapshot | None:
    """Load cached portfolio snapshot from disk, or None if not available."""
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    snap = PortfolioSnapshot(
        txn_shares_held=data.get("txn_shares_held", 0),
        txn_shares_value=data.get("txn_shares_value", 0.0),
        server_available=data.get("server_available", False),
        error=data.get("error"),
    )
    for a in data.get("accounts", []):
        holdings = [Holding(**h) for h in a.pop("holdings", [])]
        snap.accounts.append(AccountSummary(**a, holdings=holdings))
    for g in data.get("equity_grants", []):
        snap.equity_grants.append(EquityGrant(**g))
    return snap
