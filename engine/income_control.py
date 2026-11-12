"""Income Control (issue #123, slice 1): post-process ONE run_scenario run into
per-year room under each line. NO tax math here -- numbers come from YearResult;
thresholds from engine.room_lines. NO Streamlit imports (engine purity).
"""

from __future__ import annotations

from engine.this_year import first_rmd_year
from models.household import Household


def window_bounds(hh: Household) -> tuple[int, int]:
    """(window_start, window_end): the base year through the later of the last
    option-grant expiry year and the first RMD year. With no grants the end is
    the first RMD year. end < start means an empty window."""
    start = hh.base_year
    last_expiry = max((g.expiry_year for g in hh.grants), default=None)
    rmd_year = first_rmd_year(hh)
    end = rmd_year if last_expiry is None else max(last_expiry, rmd_year)
    return start, end
