"""Regression test for audit-0720 finding M5.

_base_projection must thread ``ytd`` into run_no_conversion so the base-year
ordinary-income ceiling reflects realized YTD wages.
"""

from __future__ import annotations

from engine.exercise_optimizer import optimize_exercises
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot


class TestM5YtdWagesReduceBaseYearCeilingRoom:
    def test_ytd_wages_leave_zero_ceiling_room_for_base_year_exercise(self) -> None:
        grant = StockGrant(year=2020, strike=100.0, shares=1000, expiry_year=2030, grant_id="g1")
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            grants=[grant],
            txn_price_now=200.0,
        )
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=300_000.0)

        result = optimize_exercises(hh, ytd=ytd)

        # top-of-12's 2026 ordinary-income ceiling (~$133K: bracket top +
        # deductions) is dwarfed by $300K of already-realized YTD wages, so
        # the ytd-aware base_ordinary must leave zero room for base-year
        # exercise under that strategy. Pre-fix, base_ordinary ignored ytd
        # entirely (0.0), leaving ~$133K of (nonexistent) room and dumping
        # all 1000 shares into 2026.
        top12 = next(c for c in result.candidates if c.ceiling_label == "top-of-12")
        assert top12.schedule.shares("g1", 2026) == 0
