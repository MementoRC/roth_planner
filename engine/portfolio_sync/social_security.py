"""SSA retirement-benefit-estimate fetch/parse/match/cache."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import requests  # type: ignore[import-untyped]

from engine.store import CorruptDocumentError, CorruptStoreError, get_store
from engine.store.namespaces import SSA

from .client import _flatten_query_rows, _get
from .shapes import SSABenefitEstimate, SSASnapshot, known_fields


def fetch_ssa_benefit_estimates() -> list[dict[str, Any]]:
    """GET /query/social_security?data_type=benefit_estimates, flattened rows."""
    resp = _get("/query/social_security", params={"data_type": "benefit_estimates"}, timeout=5)
    resp.raise_for_status()
    return _flatten_query_rows(resp.json())


def fetch_ssa_snapshot() -> SSASnapshot:
    """Fetch and parse SSA benefit estimates into an SSASnapshot (best-effort)."""
    snap = SSASnapshot()
    try:
        rows = fetch_ssa_benefit_estimates()
    except requests.RequestException as e:
        snap.error = str(e)
        return snap
    snap.server_available = True
    for row in rows:
        try:
            snap.estimates.append(
                SSABenefitEstimate(
                    retirement_age=int(row["retirement_age"]),
                    claim_date=str(row.get("claim_date", "")),
                    benefit_type=str(row.get("benefit_type", "")),
                    monthly_amount=float(row["monthly_amount"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return snap


def match_fra_estimate(
    estimates: list[SSABenefitEstimate], fra_age: int
) -> SSABenefitEstimate | None:
    """Find the estimate at fra_age; fall back to the nearest retirement_age.

    Returns None if estimates is empty.
    """
    if not estimates:
        return None
    exact = next((e for e in estimates if e.retirement_age == fra_age), None)
    if exact is not None:
        return exact
    return min(estimates, key=lambda e: abs(e.retirement_age - fra_age))


class CorruptSSACacheError(CorruptDocumentError):
    """save_ssa_snapshot() found the saved SSA estimates unreadable and refused to write.

    Same convention as ``CorruptPortfolioCacheError``: the other owner's saved
    estimates would be lost if the unreadable document were replaced.
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"SSA cache at {path!r} is corrupt: {cause!r}")


def save_ssa_snapshot(snap: SSASnapshot, *, owner: str) -> None:
    """Save *snap* under *owner* ('you' or 'spouse') in the shared SSA document.

    Raises CorruptSSACacheError (without writing) if the saved document is
    unreadable or not a JSON object.
    """
    store = get_store()
    try:
        loaded = store.get(SSA)
    except CorruptStoreError as exc:
        raise CorruptSSACacheError(SSA, exc) from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise CorruptSSACacheError(
            SSA, TypeError(f"expected a JSON object, got {type(loaded).__name__}")
        )
    loaded[owner] = asdict(snap)
    store.put(SSA, loaded)


def load_ssa_snapshot(*, owner: str) -> SSASnapshot | None:
    """The saved SSA snapshot for *owner*, or None if unavailable."""
    try:
        data = get_store().get(SSA)
    except CorruptStoreError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        owner_data = data.get(owner)
        if owner_data is None:
            return None
        estimates = [
            SSABenefitEstimate(**known_fields(SSABenefitEstimate, e))
            for e in owner_data.get("estimates", [])
        ]
        return SSASnapshot(
            estimates=estimates,
            server_available=owner_data.get("server_available", False),
            error=owner_data.get("error"),
        )
    except (TypeError, ValueError, AttributeError):
        # Unknown keys are dropped above; a shape this build cannot read is
        # treated like a corrupt row, never an app-boot crash (audit-1008 F-56).
        return None
