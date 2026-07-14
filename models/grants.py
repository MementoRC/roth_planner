"""Equity-comp grant dataclasses, isolated to break the config↔household
import cycle (config.defaults needs StockGrant, household needs config.loader).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StockGrant:
    """Non-qualified stock option grant."""

    year: int  # grant year (e.g. 2019)
    strike: float  # strike price per share
    shares: int  # exercisable shares
    expiry_year: int  # expiration year
    grant_id: str = ""  # FinExtract grant identifier for per-grant attribution

    def spread(self, price: float) -> float:
        return max(price - self.strike, 0) * self.shares

    def per_share_spread(self, price: float) -> float:
        """Per-share intrinsic value at ``price`` (unclamped by share count)."""
        return max(price - self.strike, 0.0)

    def key(self) -> str:
        """Stable, position-independent identity for this grant.

        Content-based (grant_id, else year+strike) so it survives FinExtract
        list compaction/reordering — see PR #369.
        """
        return self.grant_id or f"{self.year}:{self.strike:g}"
