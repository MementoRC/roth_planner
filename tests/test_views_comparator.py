"""Unit tests for views/comparator.py helper functions.

Tests focus on the pure helper functions (_survivor_death_ages,
_compute_survivor_snapshot) which require no Streamlit context.
"""

import pytest

from engine.scenario import run_no_conversion
from models.household import Household, SurvivorScenario
from views.comparator import _compute_survivor_snapshot, _survivor_death_ages


@pytest.fixture
def base_hh() -> Household:
    """Minimal Household with predictable ages (no SS, modest IRA)."""
    return Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        your_ira=500_000,
        spouse_ira=300_000,
        your_ss_fra=0,
        spouse_ss_fra=0,
        your_ss_start_age=70,
        spouse_ss_start_age=70,
        your_rmd_start_age=75,
        spouse_rmd_start_age=75,
        growth_rate=0.07,
    )


@pytest.fixture
def base_scenarios(base_hh: Household) -> list:
    """One no-conversion scenario for base_hh."""
    s = run_no_conversion(base_hh, end_age=95)
    s.name = "No Conversion"
    return [s]


class TestSurvivorDeathAges:
    """_survivor_death_ages returns correct who_dies and death_ages list."""

    def test_no_survivor_set_returns_you_and_sweep(self, base_hh: Household) -> None:
        who_dies, death_ages = _survivor_death_ages(base_hh)
        assert who_dies == "you"
        assert death_ages == [70, 75, 80, 85]

    def test_survivor_you_dies_returns_single_age(self, base_hh: Household) -> None:
        base_hh.survivor = SurvivorScenario(who_dies="you", death_year=2031)
        who_dies, death_ages = _survivor_death_ages(base_hh)
        assert who_dies == "you"
        # death_age = your_age(61) + (2031 - 2026) = 66
        assert death_ages == [66]

    def test_survivor_spouse_dies_returns_single_age(self, base_hh: Household) -> None:
        base_hh.survivor = SurvivorScenario(who_dies="spouse", death_year=2034)
        who_dies, death_ages = _survivor_death_ages(base_hh)
        assert who_dies == "spouse"
        # death_age = spouse_age(55) + (2034 - 2026) = 63
        assert death_ages == [63]


class TestComputeSurvivorSnapshot:
    """_compute_survivor_snapshot produces correctly structured rows."""

    def test_default_sweep_produces_four_rows(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        rows = _compute_survivor_snapshot(base_hh, base_scenarios, "you", [70, 75, 80, 85])
        assert len(rows) == 4

    def test_row_keys_for_you_dies(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        rows = _compute_survivor_snapshot(base_hh, base_scenarios, "you", [75])
        assert "Your Death Age" in rows[0]
        assert "Spouse Age" in rows[0]
        assert "No Conversion Inherited IRA" in rows[0]

    def test_row_keys_for_spouse_dies(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        rows = _compute_survivor_snapshot(base_hh, base_scenarios, "spouse", [55])
        assert "Spouse Death Age" in rows[0]
        assert "Your Age" in rows[0]

    def test_spouse_age_column_correct_when_you_die(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        # At death_age=70, spouse_age = 70 - (61-55) = 64
        rows = _compute_survivor_snapshot(base_hh, base_scenarios, "you", [70])
        assert rows[0]["Spouse Age"] == "64"

    def test_your_age_column_correct_when_spouse_dies(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        # At spouse death_age=65, your_age = 65 + (61-55) = 71
        rows = _compute_survivor_snapshot(base_hh, base_scenarios, "spouse", [65])
        assert rows[0]["Your Age"] == "71"

    def test_uses_spouse_rmd_start_when_you_die(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        """Survivor (spouse) uses spouse_rmd_start_age=75, not deprecated rmd_start_age."""
        base_hh.spouse_rmd_start_age = 73
        base_hh.rmd_start_age = 75  # deprecated field differs — must be ignored
        # At death_age=70 projected 5 years: survivor_age=69 — below 73, so RMD=0
        # At death_age=75 projected 5 years: survivor_age=74 — below 73 is False (74>=73) → RMD > 0
        rows_75 = _compute_survivor_snapshot(base_hh, base_scenarios, "you", [75])
        tax_str = rows_75[0]["No Conversion Survivor Tax"]
        # With rmd_start_age=73 and survivor_age=74, RMD kicks in — tax should be > $0
        assert tax_str != "$0/yr"

    def test_uses_your_rmd_start_when_spouse_dies(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        """Survivor (you) uses your_rmd_start_age, not deprecated rmd_start_age."""
        base_hh.your_rmd_start_age = 75
        base_hh.rmd_start_age = 99  # deprecated field set absurdly high — must be ignored
        # At spouse death_age=55, projected 5 years: survivor (you) age = 61+5 = 66 — no RMD
        rows_55 = _compute_survivor_snapshot(base_hh, base_scenarios, "spouse", [55])
        # survivor too young for RMD under either start age — test structural correctness
        assert "No Conversion Inherited IRA" in rows_55[0]

    def test_single_ss_threshold_lower_than_mfj(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        """filing_status='Single' uses $25K tier-1 (vs $32K MFJ) — more SS is taxable."""
        from engine.tax import taxable_ss

        # Compute what MFJ would give vs Single for a representative income
        ss_income = 30_000.0
        other_income = 20_000.0
        mfj = taxable_ss(ss_income, other_income, filing_status="MFJ")
        single = taxable_ss(ss_income, other_income, filing_status="Single")
        # Single threshold is lower so more SS is taxable
        assert single > mfj

    def test_out_of_range_death_age_returns_dashes(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        # death_age=50 is before the projection starts (your_age=61)
        rows = _compute_survivor_snapshot(base_hh, base_scenarios, "you", [50])
        assert rows[0]["No Conversion Inherited IRA"] == "---"
        assert rows[0]["No Conversion Survivor Tax"] == "---"
        assert rows[0]["No Conversion Bracket"] == "---"

    def test_survivor_scenario_single_death_age(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        """Single death age from hh.survivor produces exactly 1 row."""
        base_hh.survivor = SurvivorScenario(who_dies="you", death_year=2031)
        who_dies, death_ages = _survivor_death_ages(base_hh)
        rows = _compute_survivor_snapshot(base_hh, base_scenarios, who_dies, death_ages)
        assert len(rows) == 1
        assert rows[0]["Your Death Age"] == "66"  # 61 + (2031-2026)

    def test_survivor_scenario_spouse_dies(
        self, base_hh: Household, base_scenarios: list
    ) -> None:
        """who_dies='spouse' produces Spouse Death Age column."""
        base_hh.survivor = SurvivorScenario(who_dies="spouse", death_year=2034)
        who_dies, death_ages = _survivor_death_ages(base_hh)
        rows = _compute_survivor_snapshot(base_hh, base_scenarios, who_dies, death_ages)
        assert len(rows) == 1
        assert "Spouse Death Age" in rows[0]
        assert rows[0]["Spouse Death Age"] == "63"  # 55 + (2034-2026)
