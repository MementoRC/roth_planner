"""Regression tests: compute_headroom must thread filing_status to all consumers."""

from engine.headroom import compute_headroom
from models.household import Household
from models.ytd_income import YTDSnapshot


def _mfj_single(hh, ytd):
    return (
        compute_headroom(hh, ytd, filing_status="MFJ"),
        compute_headroom(hh, ytd, filing_status="Single"),
    )


def test_headroom_threads_filing_status_to_irmaa_tier() -> None:
    # R2-G: irmaa_tier must be filing-status-aware. $150K MAGI sits below the MFJ
    # IRMAA tier-1 threshold but above the (lower) Single thresholds.
    hh = Household()
    hh.your_age = 66
    hh.spouse_age = 66
    hh.your_ss_start_age = 70  # no SS at 66 -> locked_magi == wages
    hh.spouse_ss_start_age = 70
    ytd = YTDSnapshot(tax_year=2026, wages_ytd=150_000.0)
    hr_mfj, hr_single = _mfj_single(hh, ytd)
    assert hr_mfj.irmaa_tier_current == 0
    assert hr_single.irmaa_tier_current > hr_mfj.irmaa_tier_current


def test_headroom_threads_filing_status_to_senior_bonus() -> None:
    # R2-F: OBBBA senior bonus phases out from $75K (Single) vs $150K (MFJ). At $90K
    # MAGI with both spouses 65+, the Single deduction is smaller -> less 12%-bracket room.
    hh = Household()
    hh.your_age = 66
    hh.spouse_age = 66
    hh.your_ss_start_age = 70
    hh.spouse_ss_start_age = 70
    ytd = YTDSnapshot(tax_year=2026, wages_ytd=90_000.0)
    hr_mfj, hr_single = _mfj_single(hh, ytd)
    assert hr_mfj.room_to_12pct > 0  # guard: room is not clamped to zero
    assert hr_single.room_to_12pct < hr_mfj.room_to_12pct


def test_headroom_threads_filing_status_to_taxable_ss() -> None:
    # R1 #4: taxable_ss must use Single provisional-income thresholds when Single.
    hh = Household()
    hh.your_ss_start_age = hh.your_age  # force SS to flow (your_age >= start age)
    ytd = YTDSnapshot(tax_year=2026, wages_ytd=35_000.0)
    hr_mfj, hr_single = _mfj_single(hh, ytd)
    # Guard: Social Security is actually flowing (else the threshold difference is moot).
    assert hr_mfj.locked_magi > ytd.magi_ytd
    # Single thresholds ($25K/$34K) are lower than MFJ ($32K/$44K) -> more taxable SS
    # -> higher ordinary gross -> less room under the 22% bracket top.
    assert hr_single.room_to_22pct < hr_mfj.room_to_22pct
