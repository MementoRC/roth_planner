"""SSA retirement-benefit-estimate fetch/parse/match/cache."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import requests  # type: ignore[import-untyped]

from engine.secure_io import read_pii_json, write_pii_json

from .client import _flatten_query_rows, _get
from .shapes import SSABenefitEstimate, SSASnapshot

_SSA_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".ssa_cache.json"


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


def match_fra_estimate(estimates: list[SSABenefitEstimate], fra_age: int) -> SSABenefitEstimate | None:
    """Find the estimate at fra_age; fall back to the nearest retirement_age.

    Returns None if estimates is empty.
    """
    if not estimates:
        return None
    exact = next((e for e in estimates if e.retirement_age == fra_age), None)
    if exact is not None:
        return exact
    return min(estimates, key=lambda e: abs(e.retirement_age - fra_age))


def save_ssa_snapshot(snap: SSASnapshot, *, owner: str) -> None:
    """Save *snap* under *owner* ('you' or 'spouse') in the shared SSA cache file."""
    existing: dict[str, Any] = {}
    if _SSA_CACHE_PATH.exists():
        try:
            existing = read_pii_json(_SSA_CACHE_PATH)
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing[owner] = asdict(snap)
    write_pii_json(_SSA_CACHE_PATH, existing)


def load_ssa_snapshot(*, owner: str) -> SSASnapshot | None:
    """Load the cached SSA snapshot for *owner*, or None if unavailable."""
    if not _SSA_CACHE_PATH.exists():
        return None
    try:
        data = read_pii_json(_SSA_CACHE_PATH)
    except (json.JSONDecodeError, OSError):
        return None
    owner_data = data.get(owner)
    if owner_data is None:
        return None
    estimates = [SSABenefitEstimate(**e) for e in owner_data.get("estimates", [])]
    return SSASnapshot(
        estimates=estimates,
        server_available=owner_data.get("server_available", False),
        error=owner_data.get("error"),
    )
