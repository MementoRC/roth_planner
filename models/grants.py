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

    def spread(self, price: float) -> float:
        return max(price - self.strike, 0) * self.shares
