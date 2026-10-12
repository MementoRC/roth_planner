"""Issue #593: the Comparator's survivor snapshot must price survivor Social
Security with the same widow(er) rules the scenario projection uses.

W3 (audit-1008 F-24/F-25, decision D4) taught the projection the widow(er)
rules: the decedent's actual benefit floored at 82.5% of PIA when they claimed
early, delayed credits earned up to death when they had not claimed yet, and a
reduction for the survivor's own claim age. compute_survivor_snapshot kept
max(death-year benefits) grown by COLA, so the snapshot and the projection
disagreed about the same survivor in the same year.

All households are synthetic.
"""

from __future__ import annotations

import pytest

import engine.scenario_compare as scenario_compare
from engine.scenario import run_no_conversion
from engine.scenario_compare import compute_survivor_snapshot, survivor_death_ages
from models.household import Household, SurvivorScenario

BASE_YEAR = 2026
DEATH_YEAR = 2028
SNAPSHOT_YEARS = 5  # compute_survivor_snapshot projects the survivor 5 years past the death


def _snapshot_survivor_ss(hh: Household, monkeypatch: pytest.MonkeyPatch) -> float:
    """The survivor SS the snapshot hands to survivor_year_tax."""
    seen: list[float] = []
    original = scenario_compare.survivor_year_tax

    def _spy(survivor_age, rmd, survivor_ss, **kwargs):
        seen.append(survivor_ss)
        return original(survivor_age, rmd, survivor_ss, **kwargs)

    monkeypatch.setattr(scenario_compare, "survivor_year_tax", _spy)
    who_dies, death_ages = survivor_death_ages(hh)
    compute_survivor_snapshot(hh, [run_no_conversion(hh, end_age=95)], who_dies, death_ages)
    assert len(seen) == 1
    return seen[0]


def _scenario_survivor_ss(hh: Household) -> float:
    """The projection's survivor SS in the snapshot's year."""
    result = run_no_conversion(hh, end_age=95)
    yr = next(y for y in result.years if y.year == DEATH_YEAR + SNAPSHOT_YEARS)
    return yr.your_ss + yr.spouse_ss


def test_decedent_who_claimed_early_gets_the_82_5_pct_floor(monkeypatch) -> None:
    # "you" claimed at 62 and dies at 70; the spouse is past FRA with a small
    # own benefit, so the widow(er) benefit is the larger of the decedent's
    # reduced benefit and 82.5% of their PIA.
    hh = Household(
        your_age=68,
        spouse_age=68,
        base_year=BASE_YEAR,
        your_ira=500_000,
        spouse_ira=200_000,
        your_ss_fra=3_000,
        your_ss_start_age=62,
        spouse_ss_fra=500,
        spouse_ss_start_age=67,
        survivor=SurvivorScenario(who_dies="you", death_year=DEATH_YEAR),
    )
    assert _snapshot_survivor_ss(hh, monkeypatch) == pytest.approx(
        _scenario_survivor_ss(hh), abs=1.0
    )


def test_decedent_who_had_not_claimed_passes_on_delayed_credits(monkeypatch) -> None:
    # The spouse planned to claim at 70 but dies at 69, before claiming: the
    # survivor gets the PIA plus the delayed credits earned up to death.
    hh = Household(
        your_age=67,
        spouse_age=67,
        base_year=BASE_YEAR,
        your_ira=500_000,
        spouse_ira=200_000,
        your_ss_fra=600,
        your_ss_start_age=67,
        spouse_ss_fra=3_000,
        spouse_ss_start_age=70,
        survivor=SurvivorScenario(who_dies="spouse", death_year=DEATH_YEAR),
    )
    assert _snapshot_survivor_ss(hh, monkeypatch) == pytest.approx(
        _scenario_survivor_ss(hh), abs=1.0
    )
