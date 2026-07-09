"""SSA retirement-benefit-estimate fetch/parse/match/cache."""

from __future__ import annotations

from typing import Any

import requests  # type: ignore[import-untyped]

from .client import _flatten_query_rows, _get
from .shapes import SSABenefitEstimate, SSASnapshot


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
