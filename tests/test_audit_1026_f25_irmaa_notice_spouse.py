"""Audit-1026 F-25: IRMAA not-yet notice must name the spouse who sets the year."""

from __future__ import annotations

from engine.headroom import compute_headroom
from models.household import Household
from models.ytd_income import YTDSnapshot
from views.ytd_income._partials._analysis import irmaa_not_yet_notice


def _notice(your_age: int, spouse_age: int, filing_status: str = "MFJ") -> tuple[str, int]:
    hh = Household(
        your_age=your_age, spouse_age=spouse_age, base_year=2026, filing_status=filing_status
    )
    headroom = compute_headroom(hh, YTDSnapshot(tax_year=2026))
    return irmaa_not_yet_notice(hh, headroom), headroom.irmaa_first_relevant_year


def test_older_spouse_drives_year_notice_names_spouse() -> None:
    text, year = _notice(your_age=50, spouse_age=55)
    assert year == 2026 + (63 - 55)
    assert "Your spouse is 55" in text
    assert "spouse age 63" in text
    assert str(year) in text
    assert "You are 50" not in text


def test_you_drive_year_notice_names_you() -> None:
    text, year = _notice(your_age=55, spouse_age=50)
    assert "You are 55" in text
    assert f"(age {55 + year - 2026})" in text
    assert "spouse" not in text


def test_single_filer_names_you() -> None:
    text, _ = _notice(your_age=50, spouse_age=60, filing_status="Single")
    assert "You are 50" in text
