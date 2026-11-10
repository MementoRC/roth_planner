"""Roth conversion 5-year ledger (IRS Pub 590-B, "Additional Tax on Early
Distributions" and "Ordering Rules for Distributions").

Converted amounts that were taxable at conversion carry a 10% additional tax if
withdrawn before age 59 1/2 and within the 5-year period that starts on
January 1 of the conversion year (so a 2026 conversion's period is 2026-2030).
Each conversion has its own period, and a Roth distribution consumes
conversions first-in, first-out.

Distributions are ordered (Pub 590-B): regular contributions, then conversions,
then earnings. The ledger tracks PRINCIPAL (contributions plus conversions), so
the part of a draw that outruns the remaining principal is EARNINGS. Earnings
are income and carry the 10% additional tax unless the distribution is
qualified: after the 5-year period that starts January 1 of the first
contribution or conversion year, and at age 59 1/2 or later.

This module is PURE: no Streamlit, no ``engine.scenario`` import, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FIVE_YEAR_PERIOD = 5
QUALIFIED_AGE = 59.5
# Year assigned to money that is never exposed (the opening Roth balance).
_NEVER_EXPOSED_YEAR = -(10**6)


@dataclass(frozen=True)
class RothExposure:
    """How a Roth draw is split by the 5-year rule, in FIFO order.

    The first ``free_first`` dollars drawn are penalty-free (opening balance and
    conversions whose period has ended); the next ``exposed`` dollars are
    conversions still inside their period.
    """

    free_first: float = 0.0
    exposed: float = 0.0
    # All remaining principal; None = not tracked (no earnings are reported).
    principal: float | None = field(default=None, compare=False)

    def exposed_part(self, draw: float) -> float:
        """The portion of a draw of ``draw`` dollars that falls in an open period."""
        return min(max(draw - self.free_first, 0.0), self.exposed)

    def earnings_part(self, draw: float) -> float:
        """The portion of a draw that outruns all remaining principal."""
        if self.principal is None:
            return 0.0
        return max(draw - self.principal, 0.0)


class RothConversionLedger:
    """FIFO ledger of one owner's Roth principal, by conversion year.

    ``first_year`` is the owner's first Roth contribution year when known; it
    overrides the first recorded conversion year for the qualified-distribution
    5-year period.
    """

    def __init__(self, opening_balance: float = 0.0, first_year: int | None = None) -> None:
        self._tranches: list[list[float]] = []
        self._first_year = first_year
        if opening_balance > 0:
            self._tranches.append([_NEVER_EXPOSED_YEAR, opening_balance])

    def copy(self) -> RothConversionLedger:
        clone = RothConversionLedger(first_year=self._first_year)
        clone._tranches = [list(t) for t in self._tranches]
        return clone

    def record_conversion(self, year: int, amount: float) -> None:
        if amount > 0:
            self._tranches.append([year, amount])
            if self._first_year is None:
                self._first_year = year

    def earnings_qualified(self, year: int, age: float) -> bool:
        """Whether a draw in ``year`` at ``age`` is a qualified distribution.

        STRICT: with no known first year (no override and no conversion yet) the
        5-year period is NOT met, so earnings stay non-qualified until the user
        enters a first Roth year.
        """
        if age < QUALIFIED_AGE or self._first_year is None:
            return False
        return year >= self._first_year + FIVE_YEAR_PERIOD

    def record_draw(self, amount: float) -> None:
        """Consume ``amount`` of principal oldest-first."""
        remaining = amount
        for tranche in self._tranches:
            if remaining <= 0:
                break
            take = min(remaining, tranche[1])
            tranche[1] -= take
            remaining -= take
        self._tranches = [t for t in self._tranches if t[1] > 0]

    def exposure(self, year: int) -> RothExposure:
        """The split of a draw taken in ``year`` (tranches are oldest-first)."""
        free_first = 0.0
        exposed = 0.0
        for tranche_year, remaining in self._tranches:
            if year >= tranche_year + FIVE_YEAR_PERIOD:
                free_first += remaining
            else:
                exposed += remaining
        return RothExposure(free_first=free_first, exposed=exposed, principal=free_first + exposed)
