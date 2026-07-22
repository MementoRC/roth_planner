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

        Content-based (grant_id, else year+strike+expiry_year) so it survives
        FinExtract list compaction/reordering — see PR #369. expiry_year is
        included in the fallback so two empty-grant_id grants that share
        year+strike but differ in expiry_year don't collide (audit-0720
        H10) — shares are NOT part of the key since they mutate over a
        grant's life and the key must stay stable.
        """
        return self.grant_id or f"{self.year}:{self.strike:g}:{self.expiry_year}"


def aggregate_by_key(grants: list[StockGrant]) -> list[StockGrant]:
    """Collapse grants that collide on ``key()`` into one aggregated lot per
    key (shares summed), preserving the first-seen grant's year/strike/
    expiry_year/grant_id. Non-colliding grants pass through unchanged and
    keep their original relative order.

    ``key()`` is content-based (grant_id, else year:strike:expiry_year), so
    two genuinely distinct tranches can legitimately collide (empty
    grant_id, same year+strike+expiry_year -- audit-0721 C21/C12/C22). Call
    sites that track remaining shares or sum scheduled income per key must
    treat colliding grants as one lot; this is the single shared place that
    does so, so ``ExerciseSchedule.income_for``/``default_at_expiry`` and
    ``engine.exercise_optimizer`` all resolve collisions the same way.
    """
    order: list[str] = []
    totals: dict[str, int] = {}
    reps: dict[str, StockGrant] = {}
    for g in grants:
        k = g.key()
        if k not in totals:
            order.append(k)
            reps[k] = g
        totals[k] = totals.get(k, 0) + g.shares
    return [
        StockGrant(
            reps[k].year, reps[k].strike, totals[k], reps[k].expiry_year, reps[k].grant_id
        )
        for k in order
    ]
