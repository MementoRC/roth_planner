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

Pure I/O module -- no Streamlit; the orders live in the document store
namespace ``ubs_activity``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from typing import Any

from engine.store import CorruptStoreError, get_store
from engine.store.namespaces import UBS_ACTIVITY
from engine.ubs_activity_csv import UbsActivityOrder, UbsExerciseLot


class UbsActivityStoreError(Exception):
    """Raised when the saved UBS activity orders exist but cannot be read.

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
    """Persist *orders*, replacing whatever was saved.

    Refuses (raises :class:`UbsActivityStoreError`) when the saved orders
    cannot be read: callers merge new orders into the loaded ones, so a write
    over an unreadable row would replace orders it never saw (audit-1008
    F-70). The unreadable row is left as it is.
    """
    load_ubs_orders()
    get_store().put(UBS_ACTIVITY, [order_to_dict(o) for o in orders])


def load_ubs_orders() -> list[UbsActivityOrder]:
    """The saved UBS activity orders.

    Returns ``[]`` when nothing has been saved yet (nothing scanned -- a
    legitimate state, distinct from "scanned and found nothing"). Saved orders
    that cannot be read raise :class:`UbsActivityStoreError` rather than
    returning ``[]`` -- see that class's docstring for why a silent empty-list
    fallback here would be actively dangerous.
    """
    try:
        data = get_store().get(UBS_ACTIVITY)
    except CorruptStoreError as exc:
        raise UbsActivityStoreError(f"Could not read the saved UBS activity orders: {exc}") from exc
    if data is None:
        return []
    # Valid JSON of the wrong shape is just as unreadable as broken JSON.
    if not isinstance(data, list):
        raise UbsActivityStoreError("The saved UBS activity orders are not a list of orders")
    try:
        return [order_from_dict(d) for d in data]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise UbsActivityStoreError(
            f"The saved UBS activity orders hold a malformed order: {exc!r}"
        ) from exc
