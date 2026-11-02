"""audit-1101 W5: survivor RMD era, Asset Location past 75, YTD events by tax year.

F-07: in survivor years only the living spouse's age sets the RMD era.
F-17: a household already past 75/85 sees its current-year figures, not $0.
F-18: logged events dated outside the tax year do not count toward it.
"""

from __future__ import annotations

import pytest

from engine.asset_location import project_asset_location
from engine.scenario import first_rmd_era_age, in_rmd_era, rmd_era_years, run_no_conversion
from models.household import Household, SurvivorScenario
from models.ytd_income import IncomeEvent, sum_income_events
from views._format import fmt_dollars
from views.asset_location import STRATEGIES, milestone_ages, strategy_summary_rows


def _survivor_hh(who_dies: str) -> Household:
    if who_dies == "spouse":
        # You 60 (RMD 75), older spouse 68 (RMD 73) dies in 2027.
        return Household(
            your_age=60,
            spouse_age=68,
            base_year=2026,
            your_rmd_start_age=75,
            spouse_rmd_start_age=73,
            your_ira=500_000.0,
            spouse_ira=800_000.0,
            filing_status="MFJ",
            survivor=SurvivorScenario(who_dies="spouse", death_year=2027),
        )
    # You 68 (RMD 73) die in 2027; younger spouse 60 (RMD 75) survives.
    return Household(
        your_age=68,
        spouse_age=60,
        base_year=2026,
        your_rmd_start_age=73,
        spouse_rmd_start_age=75,
        your_ira=800_000.0,
        spouse_ira=500_000.0,
        filing_status="MFJ",
        survivor=SurvivorScenario(who_dies="you", death_year=2027),
    )


# --- F-07 -----------------------------------------------------------------


def test_f07_dead_spouse_age_does_not_start_your_rmd_era() -> None:
    hh = _survivor_hh("spouse")
    run = run_no_conversion(hh, end_age=95)
    # The dead spouse would have reached 73 when you are 65; your own RMDs start at 75.
    assert first_rmd_era_age(hh, run.years) == 75


def test_f07_dead_you_age_does_not_start_survivor_spouse_era() -> None:
    hh = _survivor_hh("you")
    run = run_no_conversion(hh, end_age=95)
    era = rmd_era_years(hh, run.years)
    # The surviving spouse reaches 75 in 2041 (your-age axis 83).
    assert first_rmd_era_age(hh, run.years) == 83
    assert all(y.your_rmd + y.spouse_rmd > 0 for y in era)


def test_f07_rmd_tax_total_covers_only_survivor_rmd_years() -> None:
    hh = _survivor_hh("spouse")
    run = run_no_conversion(hh, end_age=95)
    expected = sum(y.federal_tax_amt - y.conversion_tax for y in run.years if y.your_age >= 75)
    assert run.total_rmd_tax == pytest.approx(expected)


def test_f07_pre_death_years_still_count_older_spouse() -> None:
    hh = _survivor_hh("spouse")
    hh.survivor = SurvivorScenario(who_dies="spouse", death_year=2040)
    # 2031: you 65, spouse 73 and alive on an MFJ return.
    assert in_rmd_era(hh, 65, 73)


def test_f07_survivor_spouse_counts_regardless_of_base_filing_status() -> None:
    hh = _survivor_hh("you")
    # 2041: you would be 83, the surviving spouse is 75.
    assert in_rmd_era(hh, 83, 75)
    # 2031: you would be 73 but are dead; the spouse (65) is not yet at 75.
    assert not in_rmd_era(hh, 73, 65)


# --- F-17 -----------------------------------------------------------------


def _results(hh: Household) -> dict:
    return {s: project_asset_location(hh, {}, strategy=s) for s in STRATEGIES}


def test_f17_household_past_75_uses_current_year() -> None:
    hh = Household(your_age=78, spouse_age=70, base_year=2026, your_ira=600_000.0)
    assert milestone_ages(hh) == (78, 85)
    rows = strategy_summary_rows(hh, _results(hh))
    row = rows[0]
    assert "IRA at 78 (now)" in row
    assert row["IRA at 78 (now)"] != "$0"
    assert row["IRA Growth at 78 (now)"] != "0.0%"
    assert row["IRA at 85"] != "$0"


def test_f17_household_under_75_unchanged() -> None:
    hh = Household(your_age=61, spouse_age=55, base_year=2026, your_ira=600_000.0)
    assert milestone_ages(hh) == (75, 85)
    results = _results(hh)
    row = strategy_summary_rows(hh, results)[0]
    eq = results["equity_first"]
    assert set(row) == {
        "Strategy",
        "IRA at 75",
        "IRA at 85",
        "RMD at 75",
        "RMD at 85",
        "IRA Growth at 75",
    }
    assert row["Strategy"] == "Equity First"
    assert row["IRA at 75"] == fmt_dollars(eq.ira_at_75)


def test_f17_age_beyond_projection_shows_dash() -> None:
    hh = Household(your_age=97, spouse_age=90, base_year=2026, your_ira=100_000.0)
    row = strategy_summary_rows(hh, _results(hh))[0]
    assert row["IRA at 97 (now)"] == "—"


# --- F-18 -----------------------------------------------------------------


def test_f18_events_outside_tax_year_are_excluded() -> None:
    events = [
        IncomeEvent(date="2025-12-30", amount=40_000.0, kind="conversion", owner="you"),
        IncomeEvent(date="2026-03-01", amount=10_000.0, kind="conversion", owner="you"),
        IncomeEvent(date="2027-01-02", amount=5_000.0, kind="conversion", owner="you"),
        IncomeEvent(date="2025-11-01", amount=7_000.0, kind="distribution", owner="you"),
        IncomeEvent(date="2026-06-01", amount=2_000.0, kind="distribution", owner="spouse"),
    ]
    assert sum_income_events(events, kind="conversion", owner="you", tax_year=2026) == 10_000.0
    assert sum_income_events(events, kind="distribution", tax_year=2026) == 2_000.0


def test_f18_without_tax_year_sums_everything() -> None:
    events = [
        IncomeEvent(date="2025-12-30", amount=40_000.0, kind="conversion"),
        IncomeEvent(date="2026-03-01", amount=10_000.0, kind="conversion"),
    ]
    assert sum_income_events(events, kind="conversion") == 50_000.0


def test_f18_undated_event_still_counts() -> None:
    events = [IncomeEvent(date="", amount=3_000.0, kind="conversion")]
    assert sum_income_events(events, kind="conversion", tax_year=2026) == 3_000.0
