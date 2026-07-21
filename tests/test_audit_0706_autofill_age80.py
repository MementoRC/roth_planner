"""Regression test for audit-0706: age-80 hard break truncates spouse squeeze-tail.

Bug: `if ya > 80: break` in engine/scenario_autofill.py fires before the
loop finishes processing all squeeze-tail years for large age-gap households
where _squeeze_tail > 6.

Worked example (from audit report):
  your_age=61, spouse_age=50, both rmd_start_age=75
  _your_window = 75-61 = 14, _spouse_window = 75-50 = 25
  _squeeze_tail = max(25-14, 6) = 11, range(14+11=25)
  At yr_idx=20 ya=81 → break fires, but sa=70 (< spouse_rmd_start_age=75)
  → spouse conversions in years 20-24 (sa=70..74) are silently dropped.

Expected fix: remove `if ya > 80: break` (the range already terminates
at the correct boundary).
"""

from __future__ import annotations

from dataclasses import replace

from engine.scenario_autofill import auto_fill_12
from models.household import Household


def _age_gap_household() -> Household:
    """61/50 couple, both rmd_start=75.

    _your_window = 14, _spouse_window = 25, _squeeze_tail = max(11, 6) = 11.
    Loop range = 25.  Bug fires at yr_idx=20 (ya=81), dropping spouse
    conversions for sa=70..74 (5 eligible years).
    """
    return replace(
        Household(),
        your_age=61,  # base_year 2026 - 61 = 1965 → rmd_start_age = 75
        your_ira=1_700_000.0,
        spouse_age=50,  # base_year 2026 - 50 = 1976 → rmd_start_age = 75
        spouse_ira=1_700_000.0,
        your_rmd_start_age=75,
        spouse_rmd_start_age=75,
        # Keep SS/brokerage minimal so conversion room is clearly available
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
    )


class TestAge80BreakSqueezeTail:
    """age-80 hard break must not truncate spouse squeeze-tail conversions."""

    def test_spouse_conversions_present_in_tail_years(self) -> None:
        """Spouse conversions must appear in squeeze-tail years where sa < 75.

        With the bug the loop breaks at yr_idx=20 (ya=81).  Spouse ages 70-74
        (yr_idx 20-24) are all eligible but produce zero conversions.
        Without the bug, at least one of those tail years should have a
        non-zero spouse conversion (IRA has $1.7M, bracket room is available).
        """
        hh = _age_gap_household()
        assert hh.your_rmd_start_age == 75, "setup: your rmd_start must be 75"
        assert hh.spouse_rmd_start_age == 75, "setup: spouse rmd_start must be 75"

        plan = auto_fill_12(hh)

        # yr_idx=20 → ya=81, sa=70.  yr_idx=24 → ya=85, sa=74.
        # Any of these years should have a non-zero spouse conversion.
        tail_years = [hh.base_year + i for i in range(20, 25)]
        tail_conversions = {
            yr: plan.spouse_conversions.get(yr, 0.0) for yr in tail_years
        }

        # With the bug ALL tail conversions are 0.0 (loop broke before them).
        assert any(v > 0.0 for v in tail_conversions.values()), (
            f"BUG: ya>80 break silently dropped spouse squeeze-tail conversions.\n"
            f"  tail_years (sa=70..74): {tail_years}\n"
            f"  spouse_conversions in tail: {tail_conversions}\n"
            f"  all spouse_conversions: {dict(plan.spouse_conversions)}"
        )

    def test_total_planned_conversions_not_truncated(self) -> None:
        """Total planned conversions must reflect 25 loop iterations (not 21).

        A truncated plan misses ~4-5 years of conversions.  The un-bugged plan
        must have a non-zero spouse_conversion total beyond year base+19.
        """
        hh = _age_gap_household()
        plan = auto_fill_12(hh)

        # The last year in the range is base_year + 24.  Confirm it exists in
        # the plan (or has a non-zero value somewhere in that band).
        years_with_spouse_conv = sorted(
            yr for yr, v in plan.spouse_conversions.items() if v > 0.0
        )

        # Must have spouse conversions beyond yr_idx=19 (base_year+19)
        assert any(yr > hh.base_year + 19 for yr in years_with_spouse_conv), (
            f"BUG: spouse conversions stop at or before base_year+19.\n"
            f"  years_with_spouse_conv: {years_with_spouse_conv}"
        )
