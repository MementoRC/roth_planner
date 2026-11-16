"""Income Control slice 3 (#123, #130): the 0% LTCG room measure and the
advisory harvest sizing. Synthetic data only."""

from __future__ import annotations

import pytest

from engine.room_lines import (
    _HARVEST_ROOMS,
    HARVEST_BINDINGS,
    HarvestSizing,
    RoomLines,
    harvest_sizing,
    room_after,
)
from engine.scenario_types import YearResult
from models.ytd_income import YTDSnapshot

CPI = 0.0  # no indexing: every threshold below is the 2026 statutory value
LTCG_ZERO_MFJ = 98_900.0
# 0% LTCG room of _plain_yr: MFJ threshold less its 30,000 deductions (no SS, no actuals)
PLAIN_ROOM = LTCG_ZERO_MFJ - 30_000.0


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


def _size(
    yr: YearResult,
    lines: RoomLines,
    embedded: float | None = None,
    ytd: YTDSnapshot | None = None,
) -> HarvestSizing:
    return harvest_sizing(yr, lines, room_after(yr, lines, ytd), embedded, CPI, ytd)


def test_harvest_bindings_are_the_six_owner_labels_in_tie_order() -> None:
    assert HARVEST_BINDINGS == (
        "0% LTCG",
        "Social Security tax",
        "ACA cliff",
        "IRMAA tier 1",
        "NIIT",
        "embedded gain",
    )


def test_every_harvest_room_key_maps_to_a_harvest_binding_label() -> None:
    assert set(_HARVEST_ROOMS.values()) <= set(HARVEST_BINDINGS)


def test_harvest_sizing_binds_on_the_0pct_ltcg_room_and_basis_unknown_is_uncapped() -> None:
    # room = PLAIN_ROOM (98,900 - 30,000); no SS, so the 1:1 bound is exact
    assert _size(_plain_yr(), _lines()) == HarvestSizing(PLAIN_ROOM, "0% LTCG", PLAIN_ROOM, False)


def test_harvest_sizing_binds_on_the_embedded_gain() -> None:
    sizing = _size(_plain_yr(), _lines(), embedded=20_000.0)
    assert sizing == HarvestSizing(20_000.0, "embedded gain", PLAIN_ROOM, True)


def test_harvest_sizing_embedded_gain_above_the_room_does_not_bind() -> None:
    sizing = _size(_plain_yr(), _lines(), embedded=100_000.0)
    assert sizing == HarvestSizing(PLAIN_ROOM, "0% LTCG", PLAIN_ROOM, True)


def test_harvest_sizing_zero_embedded_gain_binds_and_reports_the_uncapped_room() -> None:
    sizing = _size(_plain_yr(), _lines(), embedded=0.0)
    assert sizing == HarvestSizing(0.0, "embedded gain", PLAIN_ROOM, True)


@pytest.mark.parametrize(
    ("lines", "amount", "binding"),
    [
        pytest.param(_lines(aca_cliff=90_000.0), 30_000.0, "ACA cliff", id="aca"),
        pytest.param(_lines(irmaa_t1=85_000.0), 25_000.0, "IRMAA tier 1", id="irmaa"),
        pytest.param(_lines(niit=80_000.0), 20_000.0, "NIIT", id="niit"),
    ],
)
def test_harvest_sizing_binds_on_each_cliff(lines: RoomLines, amount: float, binding: str) -> None:
    sizing = _size(_plain_yr(), lines)
    assert (sizing.amount, sizing.binding) == (amount, binding)


@pytest.mark.parametrize(
    ("lines", "embedded", "amount", "binding"),
    [
        # NIIT room = 128,900 - 60,000 MAGI = 68,900, tying the 0% LTCG room
        pytest.param(
            _lines(niit=LTCG_ZERO_MFJ + 30_000.0), None, PLAIN_ROOM, "0% LTCG", id="ltcg-niit"
        ),
        pytest.param(
            _lines(aca_cliff=85_000.0, irmaa_t1=85_000.0),
            None,
            25_000.0,
            "ACA cliff",
            id="aca-irmaa",
        ),
        pytest.param(
            _lines(irmaa_t1=80_000.0, niit=80_000.0),
            None,
            20_000.0,
            "IRMAA tier 1",
            id="irmaa-niit",
        ),
        pytest.param(_lines(niit=80_000.0), 20_000.0, 20_000.0, "NIIT", id="niit-embedded"),
        pytest.param(_lines(), PLAIN_ROOM, PLAIN_ROOM, "0% LTCG", id="ltcg-embedded"),
    ],
)
def test_harvest_sizing_ties_follow_harvest_bindings_order(
    lines: RoomLines, embedded: float | None, amount: float, binding: str
) -> None:
    sizing = _size(_plain_yr(), lines, embedded=embedded)
    assert (sizing.amount, sizing.binding) == (amount, binding)


@pytest.mark.parametrize(
    ("lines", "binding"),
    # MAGI 60,000 for both NIIT and ACA: niit line 50,000 gives room -10,000;
    # aca line 40,000 gives -20,000, the most negative, so it names the binding
    [
        pytest.param(_lines(niit=50_000.0), "NIIT", id="niit-crossed"),
        pytest.param(
            _lines(niit=50_000.0, aca_cliff=40_000.0), "ACA cliff", id="aca-most-negative"
        ),
    ],
)
def test_harvest_sizing_crossed_line_gives_zero_and_the_most_negative_room(
    lines: RoomLines, binding: str
) -> None:
    sizing = _size(_plain_yr(), lines)
    assert (sizing.amount, sizing.binding, sizing.room_uncapped) == (0.0, binding, 0.0)


def test_harvest_sizing_skips_inapplicable_lines() -> None:
    yr = _plain_yr(aca_magi=999_999.0, magi=999_999.0)  # ACA / IRMAA lines are None
    sizing = _size(yr, _lines())
    assert (sizing.amount, sizing.binding) == (PLAIN_ROOM, "0% LTCG")


def test_harvest_sizing_counts_base_year_actuals() -> None:
    ytd = _ytd(qualified_dividends_ytd=2_000.0, ltcg_ytd=5_000.0)
    # 98,900 less 30,000 deductions, 2,000 YTD dividends and 5,000 YTD LTCG
    assert (
        _size(_plain_yr(), _lines(), ytd=ytd).amount == LTCG_ZERO_MFJ - 30_000.0 - 2_000.0 - 5_000.0
    )


def _pref_below_unused_yr() -> YearResult:
    """Ordinary 10,000 under deductions 30,000; dividends 5,000 (below the unused 20,000)."""
    return _yr(
        combined_gross=10_000.0,
        total_deductions=30_000.0,
        deductions_before_senior_bonus=30_000.0,
        taxable_income=0.0,
        brokerage_qual_div=5_000.0,
        magi=15_000.0,
        magi_phaseout_basis=15_000.0,
        niit_magi=15_000.0,
        aca_magi=15_000.0,
    )


def test_harvest_sizing_pref_below_unused_deduction_goes_past_the_threshold() -> None:
    sizing = _size(_pref_below_unused_yr(), _lines())
    assert (sizing.amount, sizing.binding) == (LTCG_ZERO_MFJ + 15_000.0, "0% LTCG")
