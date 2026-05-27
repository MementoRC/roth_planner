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

import requests  # type: ignore[import-untyped]

from models.ytd_income import RealizedGainEvent, YTDSnapshot

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


def positions_for_forecast(brok_snapshot: AccountSummary) -> list:
    """Convert brokerage holdings into Position records for dividend forecast.

    Args:
        brok_snapshot: an AccountSummary for a brokerage account (from
            PortfolioSnapshot.account_by_type("brokerage")).

    Returns a list of engine.dividend_forecast.Position, one per Holding.
    Positions with zero market_value are skipped.
    """
    from engine.dividend_forecast import Position

    positions = []
    for h in brok_snapshot.holdings:
        if h.market_value <= 0:
            continue
        positions.append(Position(
            ticker=h.symbol,
            shares=h.quantity,
            balance=h.market_value,
            ttm_dividends=0.0,  # Holding has no dividend history field; TTM unknown
        ))
    return positions


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
        data: dict[str, list[dict[str, Any]]] = resp.json()
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
        data: dict[str, list[dict[str, Any]]] = resp.json()
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
        data: dict[str, list[dict[str, Any]]] = resp.json()
        return data.get("rows", [])
    except (requests.RequestException, ValueError):
        return []


@dataclass
class TaxReturnSnapshot:
    """Parsed TurboTax income and deduction data from FinExtract."""

    tax_year: str = "current"  # "current" or "prior"
    wages: float = 0.0  # W-2 wages
    nec_income: float = 0.0  # 1099-NEC (self-employment/contract)
    investment_income: float = 0.0  # Investments and savings (1099-B/DIV/INT)
    ira_distributions: float = 0.0  # 1099-R IRA/401k/pension withdrawals
    hsa_distributions: float = 0.0  # 1099-SA HSA/MSA
    misc_income: float = 0.0  # 1099-MISC, 1099-A, 1099-C
    hsa_contributions: float = 0.0  # Form 5498 HSA
    ira_contributions: float = 0.0  # Form 5498 IRA (Traditional + Roth combined)
    sales_tax: float = 0.0
    foreign_tax_credit: float = 0.0
    server_available: bool = False
    error: str | None = None

    @property
    def total_income(self) -> float:
        """Sum of all income sources (rough AGI proxy)."""
        return (
            self.wages + self.nec_income + self.investment_income
            + self.ira_distributions + self.hsa_distributions + self.misc_income
        )

    @property
    def estimated_magi(self) -> float:
        """Rough MAGI estimate: total income minus above-the-line deductions.

        For Roth eligibility, MAGI ≈ AGI + foreign income exclusion.
        HSA contributions are above-the-line. Half SE tax on 1099-NEC is too.
        This is approximate — TurboTax shows the real number.
        """
        se_deduction = self.nec_income * 0.0765  # half SE tax
        return self.total_income - self.hsa_contributions - se_deduction


def _parse_tax_rows(
    rows: list[dict[str, Any]], year_key: str,
) -> dict[str, float]:
    """Extract amounts from tax return rows for current or prior year."""
    result: dict[str, float] = {}
    for row in rows:
        label = row.get("form_label", "")
        amount = row.get(year_key) or 0
        if not amount:
            continue
        label_lower = label.lower()
        if "wages" in label_lower or "w-2" in label_lower:
            result["wages"] = result.get("wages", 0) + amount
        elif "1099-nec" in label_lower:
            result["nec_income"] = result.get("nec_income", 0) + amount
        elif "investment" in label_lower or "savings" in label_lower:
            result["investment_income"] = result.get("investment_income", 0) + amount
        elif "1099-r" in label_lower or "pension" in label_lower:
            result["ira_distributions"] = result.get("ira_distributions", 0) + amount
        elif "1099-sa" in label_lower or "hsa" in label_lower and "contribution" not in label_lower:
            result["hsa_distributions"] = result.get("hsa_distributions", 0) + amount
        elif "miscellaneous" in label_lower or "1099-a" in label_lower or "1099-c" in label_lower:
            result["misc_income"] = result.get("misc_income", 0) + amount
        # Deduction rows
        elif "hsa" in label_lower and "contribution" in label_lower:
            result["hsa_contributions"] = result.get("hsa_contributions", 0) + amount
        elif "ira contribution" in label_lower:
            result["ira_contributions"] = result.get("ira_contributions", 0) + amount
        elif "sales tax" in label_lower:
            result["sales_tax"] = result.get("sales_tax", 0) + amount
        elif "foreign tax" in label_lower:
            result["foreign_tax_credit"] = result.get("foreign_tax_credit", 0) + amount
    return result


def fetch_tax_return() -> TaxReturnSnapshot:
    """Fetch TurboTax income and deduction data from FinExtract."""
    snap = TaxReturnSnapshot()

    try:
        resp = requests.get(f"{BASE_URL}/status", headers=_headers(), timeout=3)
        resp.raise_for_status()
        snap.server_available = True
    except requests.RequestException as e:
        snap.error = str(e)
        return snap

    # Fetch income rows
    income_rows: list[dict[str, Any]] = []
    try:
        resp = requests.get(
            f"{BASE_URL}/query/tax_return",
            params={"data_type": "income"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        income_rows = resp.json().get("rows", [])
    except (requests.RequestException, ValueError):
        pass

    # Fetch deduction rows
    deduction_rows: list[dict[str, Any]] = []
    try:
        resp = requests.get(
            f"{BASE_URL}/query/tax_return",
            params={"data_type": "deductions"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        deduction_rows = resp.json().get("rows", [])
    except (requests.RequestException, ValueError):
        pass

    # Parse current year amounts from both income and deduction rows
    all_rows = income_rows + deduction_rows
    parsed = _parse_tax_rows(all_rows, "amount_current")

    snap.wages = parsed.get("wages", 0)
    snap.nec_income = parsed.get("nec_income", 0)
    snap.investment_income = parsed.get("investment_income", 0)
    snap.ira_distributions = parsed.get("ira_distributions", 0)
    snap.hsa_distributions = parsed.get("hsa_distributions", 0)
    snap.misc_income = parsed.get("misc_income", 0)
    snap.hsa_contributions = parsed.get("hsa_contributions", 0)
    snap.ira_contributions = parsed.get("ira_contributions", 0)
    snap.sales_tax = parsed.get("sales_tax", 0)
    snap.foreign_tax_credit = parsed.get("foreign_tax_credit", 0)

    return snap


_TAX_CACHE_PATH = Path(__file__).resolve().parent.parent / ".tax_return_cache.json"


def save_tax_snapshot(snap: TaxReturnSnapshot) -> None:
    """Save tax return snapshot to disk as JSON."""
    _TAX_CACHE_PATH.write_text(json.dumps(asdict(snap), indent=2))


def load_tax_snapshot() -> TaxReturnSnapshot | None:
    """Load cached tax return snapshot from disk, or None if not available."""
    if not _TAX_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_TAX_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return TaxReturnSnapshot(**data)


def fetch_ytd_snapshot() -> YTDSnapshot:
    """Fetch year-to-date income data from FinExtract.

    Queries the brokerage realized_gains endpoint and tax_return ytd_income
    endpoint.  Returns an empty snapshot if FinExtract is unavailable (the UI
    then falls back to manual entry).
    """
    ytd = YTDSnapshot()

    # Check server
    try:
        resp = requests.get(f"{BASE_URL}/status", headers=_headers(), timeout=3)
        resp.raise_for_status()
    except requests.RequestException:
        return ytd

    ytd.manually_entered = False

    # Realized gains
    try:
        resp = requests.get(
            f"{BASE_URL}/query/brokerage",
            params={"data_type": "realized_gains"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        institution = data.get("institution", "")
        captured_at = data.get("captured_at", "")
        # Extract date portion from ISO timestamp (e.g. "2026-03-17T16:47:58.063Z" → "2026-03-17")
        captured_date = captured_at[:10] if captured_at else ""
        rows = data.get("rows", [])
        for row in rows:
            if "long_term_gain" in row or "short_term_gain" in row:
                # Schwab aggregated summary format (schwab-realized-gains-v2)
                ltcg = row.get("long_term_gain", 0.0) or 0.0
                stcg = row.get("short_term_gain", 0.0) or 0.0
                ytd.ltcg_ytd += ltcg
                ytd.stcg_ytd += stcg
                if ltcg:
                    ytd.gain_events.append(RealizedGainEvent(
                        date=captured_date,
                        description=f"{institution.title()} realized gains (YTD)",
                        proceeds=0.0, cost_basis=0.0,
                        holding_period="long", account_name=institution.title(),
                    ))
                if stcg:
                    ytd.gain_events.append(RealizedGainEvent(
                        date=captured_date,
                        description=f"{institution.title()} realized gains (YTD)",
                        proceeds=0.0, cost_basis=0.0,
                        holding_period="short", account_name=institution.title(),
                    ))
            else:
                # Per-event format (date, description, proceeds, cost_basis)
                event = RealizedGainEvent(
                    date=row.get("date", ""),
                    description=row.get("description", ""),
                    proceeds=row.get("proceeds", 0.0),
                    cost_basis=row.get("cost_basis", 0.0),
                    holding_period=row.get("holding_period", "long"),
                    account_name=row.get("account", ""),
                )
                ytd.gain_events.append(event)
                if event.is_ltcg:
                    ytd.ltcg_ytd += event.gain_loss
                else:
                    ytd.stcg_ytd += event.gain_loss
    except (requests.RequestException, ValueError):
        pass

    # Investment income (dividends + interest from brokerage)
    try:
        resp = requests.get(
            f"{BASE_URL}/query/brokerage",
            params={"data_type": "investment_income"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
        for row in rows:
            ytd.ordinary_dividends_ytd += row.get("received_dividends", 0.0) or 0.0
            ytd.interest_ytd += row.get("received_interest", 0.0) or 0.0
    except (requests.RequestException, ValueError):
        pass

    # YTD income summary (tax return endpoint — wages, conversions, etc.)
    try:
        resp = requests.get(
            f"{BASE_URL}/query/tax_return",
            params={"data_type": "ytd_income"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
        parsed = _parse_ytd_income_rows(rows)
        ytd.wages_ytd = parsed.get("wages", 0.0)
        ytd.nec_income_ytd = parsed.get("nec_income", 0.0)
        # Split 1099-DIV: box 1a (total) minus box 1b (qualified) = non-qualified residual
        _total_div = parsed.get("total_dividends", 0.0)
        _qual_div = parsed.get("qualified_dividends", 0.0)
        ytd.qualified_dividends_ytd += _qual_div
        ytd.ordinary_dividends_ytd += max(_total_div - _qual_div, 0.0)
        ytd.interest_ytd += parsed.get("interest", 0.0)
        ytd.ira_conversions_ytd = parsed.get("ira_conversions", 0.0)
        ytd.ira_distributions_ytd = parsed.get("ira_distributions", 0.0)
    except (requests.RequestException, ValueError):
        pass

    ytd.with_snapshot_date()
    return ytd


def _parse_ytd_income_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Parse partial-year income rows from FinExtract."""
    result: dict[str, float] = {}
    for row in rows:
        label = row.get("label", "").lower()
        amount = row.get("amount", 0) or 0
        if not amount:
            continue
        if "wage" in label or "w-2" in label:
            result["wages"] = result.get("wages", 0) + amount
        elif "qualified" in label and "dividend" in label:
            # 1099-DIV box 1b — qualified dividends (subset of total ordinary)
            result["qualified_dividends"] = result.get("qualified_dividends", 0) + amount
        elif "dividend" in label or "1099-div" in label:
            # 1099-DIV box 1a — total ordinary dividends (includes qualified)
            result["total_dividends"] = result.get("total_dividends", 0) + amount
        elif "interest" in label:
            result["interest"] = result.get("interest", 0) + amount
        elif "conversion" in label:
            result["ira_conversions"] = result.get("ira_conversions", 0) + amount
        elif "distribution" in label or "1099-r" in label:
            result["ira_distributions"] = result.get("ira_distributions", 0) + amount
        elif "nec" in label or "self-employment" in label:
            result["nec_income"] = result.get("nec_income", 0) + amount
    return result


# --- YTD Persistence ---

_YTD_CACHE_PATH = Path(__file__).resolve().parent.parent / ".ytd_cache.json"


def save_ytd_snapshot(ytd: YTDSnapshot) -> None:
    """Save YTD snapshot to disk as JSON."""
    data = {
        "tax_year": ytd.tax_year,
        "snapshot_date": ytd.snapshot_date,
        "wages_ytd": ytd.wages_ytd,
        "nec_income_ytd": ytd.nec_income_ytd,
        "ira_conversions_ytd": ytd.ira_conversions_ytd,
        "ira_distributions_ytd": ytd.ira_distributions_ytd,
        "ltcg_ytd": ytd.ltcg_ytd,
        "stcg_ytd": ytd.stcg_ytd,
        "qualified_dividends_ytd": ytd.qualified_dividends_ytd,
        "ordinary_dividends_ytd": ytd.ordinary_dividends_ytd,
        "interest_ytd": ytd.interest_ytd,
        "gain_events": [asdict(e) for e in ytd.gain_events],
        "manually_entered": ytd.manually_entered,
    }
    _YTD_CACHE_PATH.write_text(json.dumps(data, indent=2))


def load_ytd_snapshot() -> YTDSnapshot | None:
    """Load cached YTD snapshot from disk, or None if not available."""
    if not _YTD_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_YTD_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    events = [RealizedGainEvent(**e) for e in data.pop("gain_events", [])]
    # Migrate old cache files that stored a single dividends_ytd key.
    if "dividends_ytd" in data and "ordinary_dividends_ytd" not in data:
        data["ordinary_dividends_ytd"] = data.pop("dividends_ytd")
    else:
        data.pop("dividends_ytd", None)
    return YTDSnapshot(**data, gain_events=events)


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
