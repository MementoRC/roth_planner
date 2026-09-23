"""Persistence for parsed UBS ACTIVITY CSV orders (:class:`UbsActivityOrder`).

This is NOT mere convenience -- it is required for CORRECTNESS. The STCG
phantom-income correction in :mod:`engine.ubs_exercise_ytd`
(``correct_ubs_option_basis``) is re-derived from the persisted order ledger
on EVERY scan, not stored as a one-time delta. If the parsed orders are lost
(a page refresh, a new Streamlit session, a restart) the next scan has no
record of which bargain element to subtract back out of the UBS statement's
reported short-term capital gain, and silently restores the phantom STCG that
double-counts the NQO spread as both ordinary income and a capital gain. This
store exists solely to prevent that.

Pure I/O module -- no Streamlit, mirrors the ``write_pii_json``/
``read_pii_json`` pattern used by engine.portfolio_sync.ytd's YTD snapshot
cache.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from engine.secure_io import read_pii_json, write_pii_json
from engine.ubs_activity_csv import UbsActivityOrder, UbsExerciseLot

# One level shallower than engine.portfolio_sync.ytd's _YTD_CACHE_PATH: this
# module lives directly in engine/, not engine/portfolio_sync/, so it only
# needs to climb two parents (engine/ -> repo root) instead of three.
_UBS_ACTIVITY_CACHE_PATH = Path(__file__).resolve().parent.parent / ".ubs_activity_cache.json"


class UbsActivityStoreError(Exception):
    """Raised when the UBS activity cache file exists but cannot be parsed.

    Deliberately NOT swallowed into an empty list: a silently-returned ``[]``
    here is indistinguishable from "no exercises this year" and would
    reintroduce the exact phantom-STCG failure this store exists to prevent,
    with no signal that anything went wrong.
    """


def _lot_to_dict(lot: UbsExerciseLot) -> dict[str, Any]:
    data = asdict(lot)
    data["grant_date"] = lot.grant_date.isoformat()
    data["funds_available_date"] = (
        lot.funds_available_date.isoformat() if lot.funds_available_date is not None else None
    )
    return data


def _lot_from_dict(data: dict[str, Any]) -> UbsExerciseLot:
    return UbsExerciseLot(
        grant_number=data["grant_number"],
        grant_date=date.fromisoformat(data["grant_date"]),
        grant_price=data["grant_price"],
        grant_quantity=data["grant_quantity"],
        execution_quantity=data["execution_quantity"],
        open_quantity=data["open_quantity"],
        cancel_quantity=data["cancel_quantity"],
        total_fees=data["total_fees"],
        total_taxes=data["total_taxes"],
        gross_proceeds=data["gross_proceeds"],
        net_proceeds=data["net_proceeds"],
        funds_available_date=(
            date.fromisoformat(data["funds_available_date"])
            if data["funds_available_date"] is not None
            else None
        ),
    )


def order_to_dict(order: UbsActivityOrder) -> dict[str, Any]:
    """Serialize *order* to a JSON-safe dict. Dates become ISO strings via
    ``.isoformat()``; ``None`` (e.g. no limit price, no funds-available date)
    stays ``None``."""
    return {
        "reference_number": order.reference_number,
        "source": order.source,
        "entry_date": order.entry_date.isoformat(),
        "execution_date": order.execution_date.isoformat(),
        "quantity": order.quantity,
        "order_type": order.order_type,
        "transaction_type": order.transaction_type,
        "limit_price": order.limit_price,
        "execution_price": order.execution_price,
        "entered_through": order.entered_through,
        "proceeds_method": order.proceeds_method,
        "funds_available_date": (
            order.funds_available_date.isoformat()
            if order.funds_available_date is not None
            else None
        ),
        "total_shares_delivered": order.total_shares_delivered,
        "lots": [_lot_to_dict(lot) for lot in order.lots],
    }


def order_from_dict(data: dict[str, Any]) -> UbsActivityOrder:
    """Inverse of :func:`order_to_dict`."""
    return UbsActivityOrder(
        reference_number=data["reference_number"],
        source=data["source"],
        entry_date=date.fromisoformat(data["entry_date"]),
        execution_date=date.fromisoformat(data["execution_date"]),
        quantity=data["quantity"],
        order_type=data["order_type"],
        transaction_type=data["transaction_type"],
        limit_price=data["limit_price"],
        execution_price=data["execution_price"],
        entered_through=data["entered_through"],
        proceeds_method=data["proceeds_method"],
        funds_available_date=(
            date.fromisoformat(data["funds_available_date"])
            if data["funds_available_date"] is not None
            else None
        ),
        total_shares_delivered=data["total_shares_delivered"],
        lots=tuple(_lot_from_dict(lot) for lot in data["lots"]),
    )


def save_ubs_orders(orders: Sequence[UbsActivityOrder]) -> None:
    """Persist *orders* to disk as JSON, replacing whatever was there."""
    write_pii_json(_UBS_ACTIVITY_CACHE_PATH, [order_to_dict(o) for o in orders])


def load_ubs_orders() -> list[UbsActivityOrder]:
    """Load cached UBS activity orders from disk.

    Returns ``[]`` when the file is simply absent (nothing has been scanned
    yet -- a legitimate, distinct state from "scanned and found nothing").
    If the file EXISTS but cannot be parsed, raises
    :class:`UbsActivityStoreError` rather than returning ``[]`` -- see that
    class's docstring for why a silent empty-list fallback here would be
    actively dangerous rather than merely inconvenient.
    """
    if not _UBS_ACTIVITY_CACHE_PATH.exists():
        return []
    try:
        data = read_pii_json(_UBS_ACTIVITY_CACHE_PATH)
    except (json.JSONDecodeError, OSError) as exc:
        raise UbsActivityStoreError(
            f"Could not read UBS activity cache at {_UBS_ACTIVITY_CACHE_PATH}: {exc}"
        ) from exc
    return [order_from_dict(d) for d in data]
