"""CPI indexing for inflation-adjusted tax constants.

NIIT thresholds ($200K/$250K per IRC §1411(b)) are statutorily frozen
and NOT handled here.
"""

from __future__ import annotations

BASE_YEAR: int = 2026
DEFAULT_CPI: float = 0.025


def index_value(base_value: float, year: int, cpi: float = DEFAULT_CPI) -> float:
    """Scale base_value forward from BASE_YEAR by compound CPI growth."""
    if year <= BASE_YEAR:
        return base_value
    return base_value * (1.0 + cpi) ** (year - BASE_YEAR)


def index_tuple(base_tuple: tuple, year: int, cpi: float = DEFAULT_CPI) -> tuple:
    """Apply index_value to every element of a tuple."""
    return tuple(index_value(v, year, cpi) for v in base_tuple)


def index_bracket_list(
    brackets: list[tuple[float, float]], year: int, cpi: float = DEFAULT_CPI
) -> list[tuple[float, float]]:
    """Brackets are List[Tuple[ceiling, rate]]; index ceilings only."""
    if not brackets:
        return brackets
    return [(index_value(c, year, cpi), r) for c, r in brackets]
