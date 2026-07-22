"""ExerciseSchedule — the sole source of truth for per-grant/per-year NQO
exercise decisions.

Content-based grant keys (``StockGrant.key()``) make the schedule
position-invariant under FinExtract list compaction/reordering, retiring the
bug class PR #369 patched (positional ``grants[year-base_year]`` indexing).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from models.grants import StockGrant, aggregate_by_key

_SCHEDULE_VERSION = 1


@dataclass
class ExerciseSchedule:
    """``grant.key()`` -> ``{year -> shares exercised}``, plus a per-year TXN
    price map (shared across grants).
    """

    shares_by_grant_year: dict[str, dict[int, int]] = field(default_factory=dict)
    price_by_year: dict[int, float] = field(default_factory=dict)

    # -- shares --------------------------------------------------------

    def shares(self, key: str, year: int) -> int:
        return self.shares_by_grant_year.get(key, {}).get(year, 0)

    def set_shares(self, key: str, year: int, n: int) -> None:
        if n <= 0:
            years = self.shares_by_grant_year.get(key)
            if years is not None:
                years.pop(year, None)
                if not years:
                    del self.shares_by_grant_year[key]
            return
        self.shares_by_grant_year.setdefault(key, {})[year] = n

    def total_exercised(self, key: str) -> int:
        return sum(self.shares_by_grant_year.get(key, {}).values())

    def remaining(self, grant: StockGrant) -> int:
        return grant.shares - self.total_exercised(grant.key())

    # -- price -----------------------------------------------------------

    def price(self, year: int, fallback: float | None = None) -> float | None:
        return self.price_by_year.get(year, fallback)

    def set_price(self, year: int, p: float) -> None:
        self.price_by_year[year] = p

    # -- engine entry point ---------------------------------------------

    def income_for(self, year: int, grants: list[StockGrant]) -> float:
        """Ordinary option income landing in ``year`` across all ``grants``.

        Defensive safety layer: ignores years past a grant's ``expiry_year``
        and clamps the CUMULATIVE exercised share count (across ALL years, not
        just this cell) to ``grant.shares``, so a malformed/stale cache cannot
        fabricate income by over-scheduling several years independently
        (audit-0721 C22). Colliding grants (same ``key()``, e.g. two
        empty-grant_id tranches sharing year+strike+expiry_year) are
        aggregated into one lot first so their shared schedule cell is only
        counted once, not once per grant object (audit-0721 C21). A missing
        price falls back to 0.0 (engine safety; the UI supplies its own
        fallback).
        """
        total = 0.0
        for grant in aggregate_by_key(grants):
            if year > grant.expiry_year:
                continue
            price = self.price(year, fallback=0.0)
            if price is None:
                price = 0.0
            key = grant.key()
            years_data = self.shares_by_grant_year.get(key, {})
            cumulative_before = min(
                sum(n for yr, n in years_data.items() if yr < year), grant.shares
            )
            cumulative_upto = min(cumulative_before + years_data.get(year, 0), grant.shares)
            exercised = cumulative_upto - cumulative_before
            total += grant.per_share_spread(price) * exercised
        return total

    # -- validation (warning layer) --------------------------------------

    def validate(self, grants: list[StockGrant], base_year: int) -> list[str]:
        """Human-readable warnings for a malformed/out-of-range schedule.

        Empty list == valid. This is the warning layer; ``income_for`` is the
        safety layer and never fabricates income even if these go unheeded.
        """
        messages: list[str] = []
        grants_by_key = {g.key(): g for g in grants}

        for key, years in self.shares_by_grant_year.items():
            grant = grants_by_key.get(key)
            total = sum(years.values())
            if grant is not None and total > grant.shares:
                messages.append(
                    f"grant {key}: scheduled {total} shares exceeds available {grant.shares}"
                )
            for yr, n in years.items():
                if n < 0:
                    messages.append(f"grant {key}, year {yr}: negative share count {n}")
                if yr < base_year:
                    messages.append(f"grant {key}, year {yr}: before base year {base_year}")
                if grant is not None and yr > grant.expiry_year:
                    messages.append(
                        f"grant {key}, year {yr}: after expiry year {grant.expiry_year}"
                    )

        for yr, p in self.price_by_year.items():
            if p < 0:
                messages.append(f"year {yr}: negative price {p}")

        return messages

    def is_empty(self) -> bool:
        return not any(years for years in self.shares_by_grant_year.values())

    def migrate_keys(self, grants: list[StockGrant]) -> None:
        """Rewrite stored grant keys from the legacy ``year:strike`` fallback to
        the current ``year:strike:expiry_year`` fallback, so schedules persisted
        before the expiry-year key enrichment keep matching their grants
        (audit-0720 H10). Only UNAMBIGUOUS remaps are applied: a legacy key is
        migrated iff exactly one grant maps to it. grant_id-based keys are never
        touched. Idempotent.
        """
        legacy_to_new: dict[str, str] = {}
        ambiguous: set[str] = set()
        for g in grants:
            if g.grant_id:
                continue  # grant_id keys never used the legacy fallback
            legacy = f"{g.year}:{g.strike:g}"
            new = g.key()
            if legacy in legacy_to_new and legacy_to_new[legacy] != new:
                ambiguous.add(legacy)
            legacy_to_new[legacy] = new
        for legacy in ambiguous:
            legacy_to_new.pop(legacy, None)
        for legacy, new in legacy_to_new.items():
            if legacy == new or legacy not in self.shares_by_grant_year:
                continue
            years = self.shares_by_grant_year.pop(legacy)
            dest = self.shares_by_grant_year.setdefault(new, {})
            for yr, n in years.items():
                dest[yr] = dest.get(yr, 0) + n

    # -- hold-to-expiration default ---------------------------------------

    @classmethod
    def default_at_expiry(
        cls,
        grants: list[StockGrant],
        base_year: int,
        price_now: float,
        price_for_year: Callable[[int], float] | None = None,
    ) -> ExerciseSchedule:
        """Default schedule: exercise each grant's full outstanding shares in
        its ``expiry_year`` (hold-to-expiration).

        Each grant's expiry-year price is ``price_for_year(expiry_year)`` when
        supplied (e.g. ``hh.projected_txn_price``, growing the price forward
        for future years), else the flat ``price_now`` -- matching the
        ``price_for_year`` convention in ``engine/exercise_optimizer.py``'s
        ``_build_candidate_schedule``, so the exercise page, optimizer, and
        scenario baseline all price future exercises identically.

        This is both the pre-fill the Option Exercise Planner shows before the
        user tunes anything and the projection default for households with no
        stored schedule. Grants already expired at ``base_year`` are skipped
        (nothing left to exercise). Grants that share an expiry year sum under
        that year. Grants that COLLIDE on ``key()`` (same year+strike+
        expiry_year, empty grant_id) are aggregated (shares summed) rather
        than last-write-wins overwritten (audit-0721 C21).
        """
        schedule = cls()
        for grant in aggregate_by_key(grants):
            if grant.expiry_year < base_year:
                continue
            price = price_for_year(grant.expiry_year) if price_for_year is not None else price_now
            schedule.set_shares(grant.key(), grant.expiry_year, grant.shares)
            schedule.set_price(grant.expiry_year, price)
        return schedule

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": _SCHEDULE_VERSION,
            "shares_by_grant_year": {
                key: dict(years) for key, years in self.shares_by_grant_year.items()
            },
            "price_by_year": dict(self.price_by_year),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExerciseSchedule:
        # JSON object keys are always strings — int-cast the year keys back.
        shares_by_grant_year = {
            key: {int(year): int(n) for year, n in years.items()}
            for key, years in d.get("shares_by_grant_year", {}).items()
        }
        price_by_year = {int(year): float(p) for year, p in d.get("price_by_year", {}).items()}
        return cls(shares_by_grant_year=shares_by_grant_year, price_by_year=price_by_year)
