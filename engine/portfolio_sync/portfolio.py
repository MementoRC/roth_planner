"""Top-level orchestrator — fetch_portfolio + portfolio snapshot cache."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

from engine.secure_io import write_pii_json

if TYPE_CHECKING:
    pass

from .awards import fetch_equity_awards, fetch_shares
from .classify import _classify_account, _classify_symbol, _parse_quantity
from .client import _get
from .holdings import fetch_holdings
from .shapes import AccountSummary, EquityGrant, Holding, PortfolioSnapshot


def fetch_portfolio(
    account_type_overrides: dict[str, str | dict[str, str]] | None = None,
) -> PortfolioSnapshot:
    """Fetch and assemble the complete portfolio snapshot.

    ``account_type_overrides`` is forwarded to :func:`_classify_account`
    so that raw IBKR account IDs (e.g. ``U1234567``) can be mapped to the
    correct account type before the substring scan runs.
    """
    snap = PortfolioSnapshot()

    # Check server availability
    try:
        resp = _get("/status", timeout=3)
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
        acct_type, owner = _classify_account(
            acct_name,
            overrides=account_type_overrides,
            owner_hint=row.get("owner"),
        )
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
                account_type=acct_type,
                owner=owner,
                account_name=acct_name,
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
            snap.equity_grants.append(
                EquityGrant(
                    grant_id=row.get("grant_id", ""),
                    grant_type=row.get("grant_type", ""),
                    grant_date=row.get("grant_date", ""),
                    shares_granted=row.get("shares_granted", 0),
                    outstanding=row.get("outstanding", 0),
                    current_value=row.get("current_value", 0),
                )
            )

    # --- TXN shares held ---
    shares_raw = fetch_shares()
    snap.txn_shares_held = sum(r.get("shares_available", 0) for r in shares_raw)
    snap.txn_shares_value = sum(r.get("available_value", 0) for r in shares_raw)

    return snap


_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".portfolio_cache.json"


def save_snapshot(snap: PortfolioSnapshot) -> None:
    """Save portfolio snapshot to disk as JSON.

    FinExtract-owned fields (equity_sales_lots, equity_sales_executions,
    order_detail_summary_captured_at) are never serialized from the in-memory
    snapshot — live HTTP sync has no equivalent source for these.  Instead,
    the existing on-disk equity_sales and sources sections are preserved so
    FinExtract's rebuild writes are not clobbered.
    """
    existing: dict[str, Any] = {}
    if _CACHE_PATH.exists():
        try:
            existing = json.loads(_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    data = asdict(snap)
    # Drop the fields that only FinExtract populates; they live under
    # "equity_sales" and "sources.order_detail_summary" in the JSON schema.
    data.pop("equity_sales_lots", None)
    data.pop("equity_sales_executions", None)
    data.pop("order_detail_summary_captured_at", None)

    # Preserve FinExtract-owned sections already on disk.
    if "equity_sales" in existing:
        data["equity_sales"] = existing["equity_sales"]
    if "sources" in existing:
        data["sources"] = existing["sources"]

    write_pii_json(_CACHE_PATH, data)


def load_snapshot() -> PortfolioSnapshot | None:
    """Load cached portfolio snapshot from disk, or None if not available."""
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    equity_sales = data.get("equity_sales") or {"lots": [], "executions": []}
    sources = data.get("sources") or {}
    ods_meta = sources.get("order_detail_summary")
    snap = PortfolioSnapshot(
        txn_shares_held=data.get("txn_shares_held", 0),
        txn_shares_value=data.get("txn_shares_value", 0.0),
        server_available=data.get("server_available", False),
        error=data.get("error"),
        equity_sales_lots=equity_sales.get("lots") or [],
        equity_sales_executions=equity_sales.get("executions") or [],
        order_detail_summary_captured_at=(ods_meta or {}).get("captured_at", "") or "",
    )
    for a in data.get("accounts", []):
        holdings = [Holding(**h) for h in a.pop("holdings", [])]
        snap.accounts.append(AccountSummary(**a, holdings=holdings))
    for g in data.get("equity_grants", []):
        snap.equity_grants.append(EquityGrant(**g))
    return snap
