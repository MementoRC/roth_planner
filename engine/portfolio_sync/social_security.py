"""SSA retirement-benefit-estimate fetch/parse/match/cache."""

from __future__ import annotations

from typing import Any

import requests  # type: ignore[import-untyped]

from .client import _flatten_query_rows, _get


def fetch_ssa_benefit_estimates() -> list[dict[str, Any]]:
    """GET /query/social_security?data_type=benefit_estimates, flattened rows."""
    resp = _get("/query/social_security", params={"data_type": "benefit_estimates"}, timeout=5)
    resp.raise_for_status()
    return _flatten_query_rows(resp.json())
