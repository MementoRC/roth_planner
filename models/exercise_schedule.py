"""ExerciseSchedule — the sole source of truth for per-grant/per-year NQO
exercise decisions.

Content-based grant keys (``StockGrant.key()``) make the schedule
position-invariant under FinExtract list compaction/reordering, retiring the
bug class PR #369 patched (positional ``grants[year-base_year]`` indexing).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from models.grants import StockGrant, aggregate_by_key

_SCHEDULE_VERSION = 1

# A saved price within this many dollars of the projection is not an override.
PRICE_OVERRIDE_TOLERANCE = 0.005


def explicit_price_overrides(
    prices: dict[int, float], projected: Callable[[int], float]
) -> dict[int, float]:
    """The prices worth persisting: those that diverge from ``projected(year)``.

    Untouched "assumed" prices must never freeze into fake overrides that
    shadow a later live-quote fetch (the "stuck at old price" bug). Shared by
    the manual Save path and the optimizer's Apply so both write the same shape.
    """
    return {
        year: price
        for year, price in prices.items()
        if abs(price - projected(year)) > PRICE_OVERRIDE_TOLERANCE
    }


def _require_mapping(value: Any, name: str) -> dict:
    """Return *value* if it is a dict, else raise ValueError naming *name*."""
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object, got {type(value).__name__}")
    return value


@dataclass
class ExerciseSchedule:
    """``grant.key()`` -> ``{year -> shares exercised}``, plus a per-year stock
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
        """Unscheduled shares of ``grant``. Pass an aggregated lot (see
        ``aggregate_by_key``): the schedule is keyed per lot, so a single
        colliding tranche would be measured against the whole lot's total."""
        return grant.shares - self.total_exercised(grant.key())

    # -- price -----------------------------------------------------------

    def price(self, year: int, fallback: float | None = None) -> float | None:
        return self.price_by_year.get(year, fallback)

    def set_price(self, year: int, p: float) -> None:
        self.price_by_year[year] = p

    # -- engine entry point ---------------------------------------------

    def income_for(
        self,
        year: int,
        grants: list[StockGrant],
        price_resolver: Callable[[int], float] | None = None,
    ) -> float:
        """Ordinary option income landing in ``year`` across all ``grants``.

        Defensive safety layer: ignores years past a grant's ``expiry_year``
        and clamps the CUMULATIVE exercised share count (across ALL years, not
        just this cell) to ``grant.shares``, so a malformed/stale cache cannot
        fabricate income by over-scheduling several years independently
        (audit-0721 C22). Colliding grants (same ``key()``, e.g. two
        empty-grant_id tranches sharing year+strike+expiry_year) are
        aggregated into one lot first so their shared schedule cell is only
        counted once, not once per grant object (audit-0721 C21).

        A missing per-year price is resolved via ``price_resolver(year)`` when
        supplied (e.g. ``hh.projected_stock_price``) -- this is what lets an
        untouched "assumed" schedule (saved with an intentionally empty
        ``price_by_year``, see the save-side drop-filter in
        ``views/option_exercise.py``) still price its exercises correctly
        instead of silently going to zero (audit-0722b). Falls back to 0.0
        only when no resolver is given (engine safety for direct/bare-schedule
        callers; the UI supplies its own fallback independently).
        """
        total = 0.0
        for grant in aggregate_by_key(grants):
            if year > grant.expiry_year:
                continue
            price = self.price(year)
            if price is None:
                price = price_resolver(year) if price_resolver is not None else 0.0
            key = grant.key()
            years_data = self.shares_by_grant_year.get(key, {})
            cumulative_before = min(
                sum(n for yr, n in years_data.items() if yr < year), grant.shares
            )
            cumulative_upto = min(cumulative_before + years_data.get(year, 0), grant.shares)
            # audit-0802 F8: clamp the lower bound too -- a negative share cell
            # (malformed/stale cache) must not fabricate negative option income.
            exercised = max(cumulative_upto - cumulative_before, 0)
            total += grant.per_share_spread(price) * exercised
        return total

    # -- validation (warning layer) --------------------------------------

    def validate(self, grants: list[StockGrant], base_year: int) -> list[str]:
        """Human-readable warnings for a malformed/out-of-range schedule.

        Empty list == valid. This is the warning layer; ``income_for`` is the
        safety layer and never fabricates income even if these go unheeded.
        """
        messages: list[str] = []
        # Colliding grants (same key()) are one lot, as in income_for and the
        # grid; a raw dict would keep only the last tranche (audit-1008 F-75).
        grants_by_key = {g.key(): g for g in aggregate_by_key(grants)}

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
        supplied (e.g. ``hh.projected_stock_price``, growing the price forward
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
        # audit-0802 F8: clamp negative share counts to 0 on deserialization so
        # a malformed/stale cache cannot resurrect a negative share cell.
        # audit-1008 F-48: a wrong-shape payload raises ValueError (which
        # load_exercise_schedule treats as "no cache") instead of AttributeError.
        raw_shares = _require_mapping(d.get("shares_by_grant_year", {}), "shares_by_grant_year")
        shares_by_grant_year = {
            key: {
                int(year): max(int(n), 0)
                for year, n in _require_mapping(years, f"shares_by_grant_year[{key!r}]").items()
            }
            for key, years in raw_shares.items()
        }
        raw_prices = _require_mapping(d.get("price_by_year", {}), "price_by_year")
        price_by_year = {int(year): float(p) for year, p in raw_prices.items()}
        return cls(shares_by_grant_year=shares_by_grant_year, price_by_year=price_by_year)


def rebalance_schedule(
    schedule: ExerciseSchedule, grant: StockGrant, year: int, new_shares: int
) -> ExerciseSchedule:
    """Return a copy of ``schedule`` with ``grant``'s ``year`` cell set to
    ``new_shares``, keeping the grant's total intact (redesign PR 4a, rule A):

    - more shares this year are taken from the NEAREST later years first;
    - fewer shares this year are added to ``year + 1`` when that is still on
      or before ``grant.expiry_year``, else they stay unplanned (remaining);
    - the grant total never exceeds ``grant.shares`` and no cell goes negative.

    Requests above what the grant has left are clamped; callers should compare
    ``out.shares(grant.key(), year)`` with the request. Raises ``ValueError``
    for a ``year`` past ``grant.expiry_year``.

    Shared by the Room This Year look-ahead and its Apply button so both write
    the same schedule. ``schedule`` itself is never mutated.
    """
    if year > grant.expiry_year:
        raise ValueError(f"year {year} is past grant expiry {grant.expiry_year}")
    out = copy.deepcopy(schedule)
    key = grant.key()
    new = max(new_shares, 0)
    delta = new - out.shares(key, year)
    out.set_shares(key, year, new)

    if delta > 0:
        to_take = delta
        later = sorted(y for y in out.shares_by_grant_year.get(key, {}) if y > year)
        for y in later:
            if to_take <= 0:
                break
            cut = min(out.shares(key, y), to_take)
            out.set_shares(key, y, out.shares(key, y) - cut)
            to_take -= cut
        excess = out.total_exercised(key) - grant.shares
        if excess > 0:
            out.set_shares(key, year, out.shares(key, year) - excess)
    elif delta < 0 and year + 1 <= grant.expiry_year:
        out.set_shares(key, year + 1, out.shares(key, year + 1) - delta)

    return out
