"""CPI indexing for inflation-adjusted tax constants.

NIIT thresholds ($200K/$250K per IRC §1411(b)) are statutorily frozen
and NOT handled here.
"""

from __future__ import annotations

import math

BASE_YEAR: int = 2026
DEFAULT_CPI: float = 0.025


_EPS = 1e-9  # absorbs binary-float noise on exact multiples / exact halves


def _round_increase_down(base: float, scaled: float, step: float) -> float:
    """``base`` plus the increase ``scaled - base`` rounded DOWN to a multiple of ``step``.

    Statutory inflation adjustments round the INCREASE down to the next lowest
    multiple of the step: $50 for brackets, the standard deduction and LTCG
    breakpoints (IRC §1(f)(7)(A), §1(j)(5)(C), §63(c)(4)); $500 for the IRA
    contribution limit and $100 for its catch-up (§219(b)(5)(C)). Non-finite
    values (e.g. the open-ended top bracket ceiling float('inf')) are returned
    unchanged.
    """
    if not math.isfinite(scaled):
        return scaled
    return base + math.floor((scaled - base) / step + _EPS) * step


def _round_nearest(value: float, step: float) -> float:
    """Round to the nearest multiple of ``step``, halves up (float-noise tolerant)."""
    if not math.isfinite(value):
        return value
    return math.floor(value / step + 0.5 + _EPS) * step


def _scaled(base_value: float, year: int, cpi: float) -> float:
    return base_value * (1.0 + cpi) ** (year - BASE_YEAR)


def index_value(
    base_value: float, year: int, cpi: float = DEFAULT_CPI, round50: bool = False
) -> float:
    """Scale base_value forward from BASE_YEAR by compound CPI growth.

    When ``round50`` is True the CPI increase is rounded DOWN to the next
    lowest multiple of $50 (IRC §1(f)(7)(A), §1(j)(5)(C), §63(c)(4)). This is
    opt-in and must be enabled ONLY for the statutorily $50-rounded categories
    (ordinary brackets, standard/senior deductions, LTCG breakpoints).
    IRMAA/FPL/ACA thresholds use different statutory rounding and MUST keep the
    default (round50=False); see ``index_value_floor`` / ``index_value_nearest``
    for the $500/$100 and $1,000 categories. Base-year (and earlier) values
    are returned unrounded because the 2026 constants are already official.
    """
    if year <= BASE_YEAR:
        return base_value
    scaled = _scaled(base_value, year, cpi)
    return _round_increase_down(base_value, scaled, 50.0) if round50 else scaled


def index_value_floor(base_value: float, year: int, cpi: float, step: float) -> float:
    """Index ``base_value`` with the increase rounded DOWN to a multiple of ``step``."""
    if year <= BASE_YEAR:
        return base_value
    return _round_increase_down(base_value, _scaled(base_value, year, cpi), step)


def index_value_nearest(base_value: float, year: int, cpi: float, step: float) -> float:
    """Index ``base_value`` and round the result to the nearest ``step`` (halves up)."""
    if year <= BASE_YEAR:
        return base_value
    return _round_nearest(_scaled(base_value, year, cpi), step)


def qcd_limit_for_year(base_limit: float, year: int, cpi: float) -> float:
    """Projected per-person QCD cap, rounded to the nearest $1,000 (IRC §408(d)(8)(G))."""
    return index_value_nearest(base_limit, year, cpi, 1_000.0)


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
