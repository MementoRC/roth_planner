"""Income Control slice 3 (#123, #130): the 0% LTCG room measure and the
advisory harvest sizing. Synthetic data only."""

from __future__ import annotations

from engine.room_lines import RoomLines, room_after
from engine.scenario_types import YearResult
from models.ytd_income import YTDSnapshot

CPI = 0.0  # no indexing: every threshold below is the 2026 statutory value
LTCG_ZERO_MFJ = 98_900.0


def _yr(**kw: float | int | str) -> YearResult:
    base: dict[str, float | int | str] = {
        "year": 2030,
        "your_age": 66,
        "spouse_age": 64,
        "phase": "clean",
        "filing_status": "MFJ",
    }
    base.update(kw)
    return YearResult(**base)  # type: ignore[arg-type]


def _plain_yr(**kw: float | int | str) -> YearResult:
    """MFJ 2030 (past the OBBBA sunset: no senior bonus), no Social Security:
    ordinary income 60,000, deductions 30,000, ordinary taxable income 30,000."""
    base: dict[str, float | int | str] = {
        "combined_gross": 60_000.0,
        "total_deductions": 30_000.0,
        "deductions_before_senior_bonus": 30_000.0,
        "taxable_income": 30_000.0,
        "magi": 60_000.0,
        "magi_phaseout_basis": 60_000.0,
        "niit_magi": 60_000.0,
        "aca_magi": 60_000.0,
    }
    base.update(kw)
    return _yr(**base)


def _lines(**kw: float | int | str | None) -> RoomLines:
    base: dict[str, float | int | str | None] = {
        "year": 2030,
        "filing_status": "MFJ",
        "top_12": 100_800.0,
        "top_22": 211_400.0,
        "top_24": 403_550.0,
        "ltcg_zero": LTCG_ZERO_MFJ,
        "irmaa_t1": None,
        "aca_cliff": None,
        "niit": 250_000.0,
    }
    base.update(kw)
    return RoomLines(**base)  # type: ignore[arg-type]


def _ytd(**kw: float) -> YTDSnapshot:
    return YTDSnapshot(tax_year=2030, snapshot_date="2030-06-30", **kw)


def _except_ltcg(rooms: dict[str, float | None]) -> dict[str, float | None]:
    return {key: room for key, room in rooms.items() if key != "ltcg_zero"}


def test_room_after_ltcg_zero_counts_this_years_realized_gains() -> None:
    # realized_gains carries turnover AND forced-draw gains (Task 2)
    rooms = room_after(_plain_yr(realized_gains=8_000.0), _lines())
    assert rooms["ltcg_zero"] == LTCG_ZERO_MFJ - (30_000.0 + 8_000.0)


def test_room_after_ltcg_zero_subtracts_base_year_ytd_dividends_and_long_term_gain() -> None:
    yr = _plain_yr()
    with_actuals = room_after(yr, _lines(), _ytd(qualified_dividends_ytd=2_000.0, ltcg_ytd=5_000.0))
    without = room_after(yr, _lines())
    assert without["ltcg_zero"] - with_actuals["ltcg_zero"] == 7_000.0
    assert _except_ltcg(with_actuals) == _except_ltcg(without)


def test_room_after_a_net_capital_loss_does_not_widen_the_room() -> None:
    yr = _plain_yr()
    assert room_after(yr, _lines(), _ytd(ltcg_ytd=-8_000.0)) == room_after(yr, _lines())


def test_room_after_unused_deduction_widens_the_ltcg_zero_room() -> None:
    # ordinary 20,000 under deductions 30,000: the unused 10,000 shelters the dividends
    yr = _yr(
        combined_gross=20_000.0,
        total_deductions=30_000.0,
        taxable_income=0.0,
        brokerage_qual_div=10_000.0,
    )
    assert room_after(yr, _lines())["ltcg_zero"] == LTCG_ZERO_MFJ  # slice 1 gave 88,900


def test_room_after_room_exceeds_the_threshold_when_pref_is_below_the_unused_deduction() -> None:
    yr = _yr(
        combined_gross=10_000.0,
        total_deductions=30_000.0,
        taxable_income=0.0,
        brokerage_qual_div=5_000.0,
    )
    # measure = 0 + 5,000 - 20,000 = -15,000 (no floor)
    assert room_after(yr, _lines())["ltcg_zero"] == LTCG_ZERO_MFJ + 15_000.0
