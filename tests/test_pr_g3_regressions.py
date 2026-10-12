"""Regression test for deep-review 2026-06-18 PR-G3 (survivor OBBBA bonus)."""

from engine.scenario import run_no_conversion
from engine.scenario_compare import compute_survivor_snapshot, survivor_death_ages
from models.household import Household, SurvivorScenario


def _survivor_tax(base_year: int) -> float:
    """Survivor annual tax for a spouse-dies-at-base scenario; proj year = base_year + 5."""
    hh = Household(
        your_age=76,
        spouse_age=76,
        base_year=base_year,
        your_ira=200_000,
        spouse_ira=1_000_000,
        your_ss_fra=0,
        spouse_ss_fra=0,
        your_ss_start_age=70,
        spouse_ss_start_age=70,
        growth_rate=0.05,
        survivor=SurvivorScenario(who_dies="spouse", death_year=base_year),
    )
    s = run_no_conversion(hh, end_age=95)
    s.name = "NoConv"
    who, ages = survivor_death_ages(hh)
    rows = compute_survivor_snapshot(hh, [s], who, ages)
    raw = rows[0]["NoConv Survivor Tax"]
    return float(raw.replace("$", "").replace(",", "").replace("/yr", ""))


class TestSurvivorObbbaBonus:
    def test_survivor_snapshot_applies_obbba_bonus_in_window(self):
        """compare-sweetspot-4: the survivor snapshot must apply the OBBBA senior bonus
        when the projection year falls in 2026-2028.

        base_year 2022 -> projection 2027 (OBBBA active) vs base_year 2025 -> 2030
        (sunset). Ages and IRA are identical, so the only difference is the $6k bonus,
        which lowers the survivor's taxable income and therefore the tax.
        """
        tax_in_window = _survivor_tax(2022)
        tax_out_window = _survivor_tax(2025)
        assert tax_out_window > 0  # a positive taxable base exists to be reduced
        assert tax_in_window < tax_out_window
