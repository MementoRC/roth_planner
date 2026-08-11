"""RED-first tests: ACA benchmark premium must be derived (age-rated + indexed),
not a flat stale constant.

Covers three defects in the pre-fix code (models/household.py:276,
engine/aca.py:48-context): the household-level benchmark was a flat $21,600/yr
with no age progression and no CPI indexing across all 7 production consumers.
"""

import pytest

from engine.aca import (
    SLCSP_AGE40_MONTHLY,
    aca_age_factor,
    derive_couple_benchmark_annual,
    resolve_couple_benchmark_annual,
)
from models.household import Household


def test_derive_couple_benchmark_matches_anchor() -> None:
    """61+55 MFJ couple at BASE_YEAR with cpi=0.0 (no indexing applied).

    Anchor: 625*(2.810/1.278)*12 + 625*(2.230/1.278)*12 = 16490.61 + 13086.86
    = 29577.47.
    """
    result = derive_couple_benchmark_annual(
        your_age=61, spouse_age=55, filing_status="MFJ", year=2026, cpi=0.0
    )
    per_you = SLCSP_AGE40_MONTHLY * (aca_age_factor(61) / aca_age_factor(40)) * 12
    per_spouse = SLCSP_AGE40_MONTHLY * (aca_age_factor(55) / aca_age_factor(40)) * 12
    assert per_you == pytest.approx(16_490.61, abs=0.01)
    assert per_spouse == pytest.approx(13_086.86, abs=0.01)
    assert result == pytest.approx(29_577.47, abs=0.01)


def test_derive_couple_benchmark_rises_as_couple_ages() -> None:
    """Same couple's benchmark must rise as both age from 61/55 to 64/58 (HHS
    age-rating curve rises with age -- the level was previously held flat)."""
    younger = derive_couple_benchmark_annual(
        your_age=61, spouse_age=55, filing_status="MFJ", year=2026, cpi=0.0
    )
    older = derive_couple_benchmark_annual(
        your_age=64, spouse_age=58, filing_status="MFJ", year=2026, cpi=0.0
    )
    assert older > younger


def test_derive_couple_benchmark_rises_with_year_under_cpi() -> None:
    """Nonzero cpi must inflate the benchmark forward from BASE_YEAR -- the
    pre-fix constant never received `year` at any of its 7 call sites."""
    base = derive_couple_benchmark_annual(
        your_age=61, spouse_age=55, filing_status="MFJ", year=2026, cpi=0.025
    )
    later = derive_couple_benchmark_annual(
        your_age=61, spouse_age=55, filing_status="MFJ", year=2030, cpi=0.025
    )
    assert later > base
    assert later == pytest.approx(base * (1.025**4))


def test_resolve_explicit_override_used_verbatim() -> None:
    """A household-supplied float override must be returned as-is -- no
    age-rating, no indexing -- even where derivation would differ wildly."""
    override = 50_000.0
    resolved = resolve_couple_benchmark_annual(
        override, your_age=90, spouse_age=90, filing_status="MFJ", year=2040, cpi=0.10
    )
    assert resolved == override


def test_resolve_zero_override_honored_as_zero() -> None:
    """0.0 is a deliberate override (no ACA premium exposure modeled), distinct
    from None ("derive"). Must NOT be treated as falsy/"derive"."""
    resolved = resolve_couple_benchmark_annual(
        0.0, your_age=61, spouse_age=55, filing_status="MFJ", year=2026, cpi=0.0
    )
    assert resolved == 0.0


def test_resolve_none_derives() -> None:
    """None means 'derive' -- resolves to the same value derive_couple_benchmark_annual
    would produce directly."""
    resolved = resolve_couple_benchmark_annual(
        None, your_age=61, spouse_age=55, filing_status="MFJ", year=2026, cpi=0.0
    )
    expected = derive_couple_benchmark_annual(
        your_age=61, spouse_age=55, filing_status="MFJ", year=2026, cpi=0.0
    )
    assert resolved == pytest.approx(expected)


def test_household_benchmark_field_defaults_to_none() -> None:
    """Household.aca_benchmark_premium_annual defaults to None ('derive'), not
    the old flat 21_600.0 constant."""
    hh = Household()
    assert hh.aca_benchmark_premium_annual is None
