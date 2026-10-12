"""Issue #597: auto-fill must size survivor years with the projection's SS.

_auto_fill_core kept its own copy of the widow(er) rules (decedent's already
reduced, claim-age-gated benefit; no PIA floor, no delayed credits, no COLA),
so it sized conversion room against a different survivor income than
run_scenario produces. It now calls the shared social_security_benefits.

All households are synthetic.
"""

from __future__ import annotations

import pytest

from engine.scenario_autofill import _auto_fill_core
from engine.scenario_compute import social_security_benefits
from models.household import Household, SurvivorScenario

BASE_YEAR = 2026
DEATH_YEAR = 2028


def _autofill_combined_ss_by_year(hh: Household) -> dict[int, float]:
    """The combined SS _auto_fill_core hands to its room function, per year."""
    seen: dict[int, float] = {}

    def _spy(fixed_gross, ded, base_magi, combined_ss, other_fixed, year, cpi, filing_status):
        seen[year] = combined_ss
        return 0.0

    _auto_fill_core(hh, None, _spy)
    return seen


def _projection_combined_ss(hh: Household, year: int) -> float:
    assert hh.survivor is not None
    idx = year - hh.base_year
    your_ss, spouse_ss = social_security_benefits(
        hh,
        hh.your_age + idx,
        hh.spouse_age + idx,
        survivor_active=True,
        who_dies=hh.survivor.who_dies,
        death_year=hh.survivor.death_year,
    )
    return your_ss + spouse_ss


def _assert_survivor_years_match(hh: Household) -> None:
    seen = _autofill_combined_ss_by_year(hh)
    survivor_years = [y for y in seen if y > DEATH_YEAR]
    assert len(survivor_years) >= 3
    for year in survivor_years:
        assert seen[year] == pytest.approx(_projection_combined_ss(hh, year), abs=1.0)


def test_decedent_who_claimed_early_gets_the_82_5_pct_floor() -> None:
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
    _assert_survivor_years_match(hh)


def test_decedent_who_died_before_claiming_passes_on_delayed_credits() -> None:
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
    _assert_survivor_years_match(hh)
