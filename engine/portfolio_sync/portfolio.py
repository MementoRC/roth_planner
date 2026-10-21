"""Top-level orchestrator — fetch_portfolio + portfolio snapshot cache."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

from engine.secure_io import read_pii_json, write_pii_json
from models.legacy_keys import migrate_legacy_keys

if TYPE_CHECKING:
    pass

from .awards import fetch_equity_awards, fetch_shares
from .classify import _classify_account, _classify_symbol, _parse_quantity
from .client import _get
from .holdings import fetch_holdings_snapshot
from .shapes import (
    AccountSummary,
    EquityGrant,
    Holding,
    PortfolioSnapshot,
    account_summary_from_dict,
    known_fields,
)


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
    # PS-1: /status can succeed while /query/brokerage fails. Reporting that as
    # an empty-but-available snapshot let the caller persist accounts=[] over a
    # good cache behind a success toast, so surface it as unavailable instead —
    # sync_portfolio_from_finextract() returns before save_snapshot() on this.
    holdings = fetch_holdings_snapshot()
    if not holdings.server_available:
        snap.server_available = False
        snap.error = f"holdings fetch failed: {holdings.error}"
        return snap
    holdings_raw = holdings.rows
    accounts_map: dict[str, AccountSummary] = {}

    row_dates = holdings.row_as_of if len(holdings.row_as_of) == len(holdings_raw) else []
    for idx, row in enumerate(holdings_raw):
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
            inst_date = row_dates[idx] if row_dates else ""
            accounts_map[key] = AccountSummary(
                account_type=acct_type,
                owner=owner,
                account_name=acct_name,
                as_of=inst_date or holdings.as_of,
                as_of_source="finextract" if inst_date else holdings.as_of_source,
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

    # --- Employer-stock shares held ---
    shares_raw = fetch_shares()
    snap.stock_shares_held = sum(r.get("shares_available", 0) for r in shares_raw)
    snap.stock_shares_value = sum(r.get("available_value", 0) for r in shares_raw)

    return snap


_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".portfolio_cache.json"


class EmptySnapshotWriteRefusedError(RuntimeError):
    """save_snapshot() refused to erase a populated cache with an empty snapshot.

    Raised rather than skipped on purpose. A silent no-op would reproduce the
    exact shape of the defect this guards against (audit-0823 PS-1): a caller
    reporting success over a write that did not do what it claimed.
    """


class CorruptPortfolioCacheError(Exception):
    """save_snapshot() found ``.portfolio_cache.json`` unreadable and refused to write.

    Mirrors ``CorruptCommittedCacheError`` / ``CorruptCandidateStoreError``:
    overwriting a corrupt (or non-object) cache with a fresh snapshot would
    destroy the only copy of whatever bytes are on disk, including the
    FinExtract-owned ``equity_sales`` / ``sources`` sections (audit-1018 W8 follow-up).
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"portfolio cache at {path!r} is corrupt: {cause!r}")


def save_snapshot(snap: PortfolioSnapshot, *, allow_empty: bool | None = None) -> None:
    """Save portfolio snapshot to disk as JSON.

    FinExtract-owned fields (equity_sales_lots, equity_sales_executions,
    order_detail_summary_captured_at) are never serialized from the in-memory
    snapshot — live HTTP sync has no equivalent source for these.  Instead,
    the existing on-disk equity_sales and sources sections are preserved so
    FinExtract's rebuild writes are not clobbered.

    ``allow_empty`` controls whether a snapshot carrying zero accounts may
    erase a populated cache (audit-0823 PS-2b).  It defaults to
    ``snap.server_available``: on the sync path that flag is live and truthful,
    so a verified "the household holds nothing" still persists while an
    unanswered fetch is refused.  Callers where the flag is stale — the
    data-bridge import inherits it from disk through ``apply_bundle`` — must
    pass it explicitly rather than depend on the inference.

    Raises:
        CorruptPortfolioCacheError: the cache exists but is unreadable or not a
            JSON object; the file is left untouched.
        EmptySnapshotWriteRefusedError: the write would drop every account from a
            cache that currently holds some, and emptiness was not verified.
    """
    existing: dict[str, Any] = {}
    if _CACHE_PATH.exists():
        try:
            loaded = read_pii_json(_CACHE_PATH)
        except FileNotFoundError:  # absent, or hidden in demo mode
            loaded = {}
        except (json.JSONDecodeError, OSError) as exc:
            raise CorruptPortfolioCacheError(_CACHE_PATH, exc) from exc
        if not isinstance(loaded, dict):
            raise CorruptPortfolioCacheError(
                _CACHE_PATH, TypeError(f"expected a JSON object, got {type(loaded).__name__}")
            )
        existing = loaded

    if allow_empty is None:
        allow_empty = snap.server_available
    existing_accounts = existing.get("accounts") or []
    if not snap.accounts and not allow_empty and existing_accounts:
        raise EmptySnapshotWriteRefusedError(
            f"Refusing to overwrite {_CACHE_PATH.name} "
            f"({len(existing_accounts)} account(s) on disk) with a snapshot "
            "holding none. The fetch that produced it never confirmed the "
            "household actually holds nothing, so this would be data loss."
        )

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
        data = read_pii_json(_CACHE_PATH)
    except (json.JSONDecodeError, OSError):
        return None

    try:
        return _snapshot_from_cache(data)
    except (TypeError, ValueError, AttributeError):
        # A shape this build cannot read (a required field missing, a list
        # where a dict belongs) is treated like a corrupt file rather than
        # crashing app boot (audit-1008 F-56). Unknown keys never get here:
        # they are dropped field by field.
        return None


def _snapshot_from_cache(data: dict) -> PortfolioSnapshot:
    # A cache written before the stock_* rename carries the old key names.
    data = migrate_legacy_keys(data)
    equity_sales = data.get("equity_sales") or {"lots": [], "executions": []}
    sources = data.get("sources") or {}
    ods_meta = sources.get("order_detail_summary")
    snap = PortfolioSnapshot(
        stock_shares_held=data.get("stock_shares_held", 0),
        stock_shares_value=data.get("stock_shares_value", 0.0),
        server_available=data.get("server_available", False),
        error=data.get("error"),
        imported_from_bundle=bool(data.get("imported_from_bundle", False)),
        equity_grants_owner=str(data.get("equity_grants_owner", "") or ""),
        equity_sales_lots=equity_sales.get("lots") or [],
        equity_sales_executions=equity_sales.get("executions") or [],
        order_detail_summary_captured_at=(ods_meta or {}).get("captured_at", "") or "",
    )
    snap.accounts = [account_summary_from_dict(a, tolerant=True) for a in data.get("accounts", [])]
    for g in data.get("equity_grants", []):
        snap.equity_grants.append(EquityGrant(**known_fields(EquityGrant, g)))
    return snap
