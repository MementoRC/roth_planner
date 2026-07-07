"""CPI indexing for inflation-adjusted tax constants.

NIIT thresholds ($200K/$250K per IRC §1411(b)) are statutorily frozen
and NOT handled here.
"""

from __future__ import annotations

import math

BASE_YEAR: int = 2026
DEFAULT_CPI: float = 0.025


def _round_to_nearest_50(value: float) -> float:
    """Round a CPI-adjusted amount to the nearest multiple of $50 (half-up).

    Statutory inflation-adjusted amounts — ordinary-income bracket ceilings,
    the standard/additional (senior) deductions, and LTCG breakpoints — are
    rounded to the nearest $50 per IRC §1(f)(6); an exact $25 half-step rounds
    UP to the next $50. Non-finite values (e.g. the open-ended top bracket
    ceiling float('inf')) are returned unchanged.
    """
    if not math.isfinite(value):
        return value
    return math.floor(value / 50.0 + 0.5) * 50.0


def index_value(
    base_value: float, year: int, cpi: float = DEFAULT_CPI, round50: bool = False
) -> float:
    """Scale base_value forward from BASE_YEAR by compound CPI growth.

    When ``round50`` is True the CPI-scaled result is rounded to the nearest
    $50 (IRC §1(f)(6)). This is opt-in and must be enabled ONLY for the
    statutorily $50-rounded categories (ordinary brackets, standard/senior
    deductions, LTCG breakpoints). IRMAA/FPL/ACA/QCD thresholds and
    contribution/catch-up/phase-out limits use different statutory rounding
    and MUST keep the default (round50=False). Base-year (and earlier) values
    are returned unrounded because the 2026 constants are already official.
    """
    if year <= BASE_YEAR:
        return base_value
    scaled = base_value * (1.0 + cpi) ** (year - BASE_YEAR)
    return _round_to_nearest_50(scaled) if round50 else scaled


def index_tuple(
    base_tuple: tuple, year: int, cpi: float = DEFAULT_CPI, round50: bool = False
) -> tuple:
    """Apply index_value to every element of a tuple (see index_value re round50)."""
    return tuple(index_value(v, year, cpi, round50=round50) for v in base_tuple)


def index_bracket_list(
    brackets: list[tuple[float, float]],
    year: int,
    cpi: float = DEFAULT_CPI,
    round50: bool = False,
) -> list[tuple[float, float]]:
    """Brackets are List[Tuple[ceiling, rate]]; index (optionally $50-round) ceilings only."""
    if not brackets:
        return brackets
    return [(index_value(c, year, cpi, round50=round50), r) for c, r in brackets]
