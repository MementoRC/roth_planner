"""Regression test for audit-0722b: compute_phase must not misclassify a
single filer's post-RMD years as "squeeze".

Bug: compute_phase used ``sa == 0`` (via ``sa > 0 and sa < rmd_spouse``) as
the single-filer sentinel. The sole caller (engine/scenario.py) passes
``sa = hh.spouse_age + yr_idx``, which increments every projected year even
for a single filer (whose spouse_age is seeded at 0). So past the base
year sa > 0, and once the primary reaches RMD age the squeeze branch fired
incorrectly, returning "squeeze" instead of "rmd" for single filers.

Fix: gate the squeeze branch on ``hh.filing_status != "Single"`` instead of
the sa == 0 sentinel.
"""

from engine.scenario_compute import compute_phase
from models.household import Household


class TestComputePhaseSingleFilerRmdLabel:
    """Single filer past RMD age must label 'rmd' in every projected year."""

    def _single_household(self, **kwargs) -> Household:
        defaults = {
            "filing_status": "Single",
            "your_age": 72,
            "spouse_age": 0,
            "base_year": 2026,
            "your_ira": 1_700_000,
            "spouse_ira": 0,
        }
        defaults.update(kwargs)
        return Household(**defaults)

    def test_single_filer_rmd_year_labels_rmd_not_squeeze(self):
        hh = self._single_household()
        # Base year: your_age=72 < your_rmd_start_age (73) -> not yet RMD.
        # Project forward 2 years so your_age reaches 74 (past RMD start).
        yr_idx = 2
        year = hh.base_year + yr_idx
        ya = hh.your_age + yr_idx
        sa = hh.spouse_age + yr_idx  # 0 + 2 = 2, mirrors engine/scenario.py caller
        assert ya >= hh.your_rmd_start_age
        phase = compute_phase(ya, sa, year, hh)
        assert phase == "rmd", f"Expected 'rmd' for single filer past RMD age, got {phase!r}"

    def test_single_filer_base_year_still_labels_rmd(self):
        """Base year (sa == 0) already worked under the old sentinel; must not regress."""
        hh = self._single_household(your_age=74)
        phase = compute_phase(hh.your_age, hh.spouse_age, hh.base_year, hh)
        assert phase == "rmd", f"Expected 'rmd' in base year, got {phase!r}"

    def test_married_squeeze_still_labels_squeeze(self):
        """Married household with primary past RMD age but spouse not yet at
        spouse_rmd_start_age must still label 'squeeze' (no regression)."""
        hh = Household(
            filing_status="MFJ",
            your_age=73,
            spouse_age=67,
            base_year=2026,
            your_ira=1_700_000,
            spouse_ira=1_700_000,
        )
        phase = compute_phase(hh.your_age, hh.spouse_age, hh.base_year, hh)
        assert phase == "squeeze", f"Expected 'squeeze' for married pre-spouse-RMD, got {phase!r}"

    def test_married_both_past_rmd_labels_rmd(self):
        """Married household with both spouses past RMD age must label 'rmd' (no regression)."""
        hh = Household(
            filing_status="MFJ",
            your_age=75,
            spouse_age=75,
            base_year=2026,
            your_ira=1_700_000,
            spouse_ira=1_700_000,
        )
        phase = compute_phase(hh.your_age, hh.spouse_age, hh.base_year, hh)
        assert phase == "rmd", f"Expected 'rmd' for married both past RMD, got {phase!r}"
