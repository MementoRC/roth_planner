"""Roth conversion 5-year ledger (IRS Pub 590-B, "Additional Tax on Early
Distributions" and "Ordering Rules for Distributions").

Converted amounts that were taxable at conversion carry a 10% additional tax if
withdrawn before age 59 1/2 and within the 5-year period that starts on
January 1 of the conversion year (so a 2026 conversion's period is 2026-2030).
Each conversion has its own period, and a Roth distribution consumes
conversions first-in, first-out.

This module is PURE: no Streamlit, no ``engine.scenario`` import, no I/O. It
tracks conversion PRINCIPAL only; Roth growth is not tracked here, so a draw
that outruns the remaining principal (earnings) is not penalised by this rule.
"""

from __future__ import annotations

from dataclasses import dataclass

FIVE_YEAR_PERIOD = 5
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

    def exposed_part(self, draw: float) -> float:
        """The portion of a draw of ``draw`` dollars that falls in an open period."""
        return min(max(draw - self.free_first, 0.0), self.exposed)


class RothConversionLedger:
    """FIFO ledger of one owner's Roth principal, by conversion year."""

    def __init__(self, opening_balance: float = 0.0) -> None:
        self._tranches: list[list[float]] = []
        if opening_balance > 0:
            self._tranches.append([_NEVER_EXPOSED_YEAR, opening_balance])

    def copy(self) -> RothConversionLedger:
        clone = RothConversionLedger()
        clone._tranches = [list(t) for t in self._tranches]
        return clone

    def record_conversion(self, year: int, amount: float) -> None:
        if amount > 0:
            self._tranches.append([year, amount])

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
        return RothExposure(free_first=free_first, exposed=exposed)
