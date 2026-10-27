"""audit-1026 F-17: the RMD Squeeze era includes an older spouse's RMD years.

The page filtered on ``your_age >= your_rmd_start_age`` only, so spouse RMDs
that start earlier (older spouse) were dropped from totals, charts and tables.
"""

from __future__ import annotations

from engine.scenario import rmd_era_years, run_no_conversion
from models.household import Household


def _household(filing_status: str = "MFJ") -> Household:
    return Household(
        your_age=60,
        spouse_age=68,
        base_year=2026,
        your_rmd_start_age=75,
        spouse_rmd_start_age=73,
        your_ira=500_000.0,
        spouse_ira=800_000.0,
        filing_status=filing_status,
    )


def test_mfj_era_includes_spouse_only_rmd_years() -> None:
    hh = _household()
    run = run_no_conversion(hh, end_age=95)
    era = rmd_era_years(hh, run.years)
    spouse_only = [y for y in era if y.your_age < 75]
    assert spouse_only, "spouse RMD years before your own start age are dropped"
    assert all(y.spouse_rmd > 0 for y in spouse_only)


def test_era_total_equals_sum_of_all_rmd_years() -> None:
    hh = _household()
    run = run_no_conversion(hh, end_age=95)
    era = rmd_era_years(hh, run.years)
    true_total = sum(y.your_rmd + y.spouse_rmd for y in run.years)
    assert sum(y.your_rmd + y.spouse_rmd for y in era) == true_total
    assert true_total > 0


def test_single_filer_unchanged() -> None:
    hh = _household("Single")
    run = run_no_conversion(hh, end_age=95)
    era = rmd_era_years(hh, run.years)
    assert era == [y for y in run.years if y.your_age >= 75]
