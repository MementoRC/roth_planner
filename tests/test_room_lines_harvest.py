"""Income Control slice 3 (#123, #130): the 0% LTCG room measure and the
advisory harvest sizing. Synthetic data only."""

from __future__ import annotations

import dataclasses

import pytest

from engine import room_lines as rl
from engine.income_control import build_income_control
from engine.room_lines import (
    _HARVEST_ROOMS,
    HARVEST_BINDINGS,
    HarvestSizing,
    RoomLines,
    harvest_sizing,
    room_after,
    room_lines_for_year,
)
from engine.scenario import _pref_stack_tax, run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from engine.tax import (
    LTCG_RATES_MFJ,
    LTCG_RATES_SINGLE,
    LTCG_THRESHOLDS_MFJ,
    LTCG_THRESHOLDS_SINGLE,
    federal_tax,
    federal_tax_single,
)
from engine.tax_indexing import index_tuple
from engine.yr_reprice import reprice
from models.household import Household, SurvivorScenario
from models.ytd_income import YTDSnapshot
from tests.test_income_control_harvest import _brok_hh

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
    # realized_gains carries turnover AND forced-draw gains
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


def _ss_yr(**kw: float | int | str) -> YearResult:
    """MFJ 2030, Social Security 40,000 with 20,000 of other ordinary income:
    provisional income 40,000 (50% band), taxable SS 4,000; ordinary 24,000
    under deductions 30,000 (taxable 0, unused deduction 6,000)."""
    base: dict[str, float | int | str] = {
        "combined_ss": 40_000.0,
        "taxable_ss_amt": 4_000.0,
        "ss_provisional_base": 20_000.0,
        "combined_gross": 24_000.0,
        "total_deductions": 30_000.0,
        "deductions_before_senior_bonus": 30_000.0,
        "taxable_income": 0.0,
        "magi": 24_000.0,
        "magi_phaseout_basis": 24_000.0,
        "niit_magi": 24_000.0,
        "aca_magi": 60_000.0,
    }
    base.update(kw)
    return _yr(**base)


# Past 4,000 of harvest, taxable SS rises 0.85 per dollar; it uses up the unused
# 6,000 of deduction (taxable SS 4,000 -> 10,000) at H = 4,000 + 4,000 / 0.85,
# where ordinary tax starts.
SS_TAX_LIMIT = 4_000.0 + 4_000.0 / 0.85
# With 80,000 of deductions, taxable SS caps at 34,000 and ordinary income
# (20,000 + 34,000) never exceeds them: no ordinary tax. The 26,000 still unused
# shelters harvest first, so the 0% band ends at H = 98,900 + 26,000.
SS_LTCG_LIMIT = LTCG_ZERO_MFJ + 26_000.0


def test_harvest_sizing_binds_on_social_security_tax() -> None:
    sizing = _size(_ss_yr(), _lines())
    assert sizing.binding == "Social Security tax"
    assert SS_TAX_LIMIT - 1.0 <= sizing.amount <= SS_TAX_LIMIT + 0.1
    assert room_after(_ss_yr(), _lines())["ltcg_zero"] == LTCG_ZERO_MFJ + 6_000.0  # 1:1 bound


def test_harvest_sizing_finds_the_0pct_limit_when_social_security_eats_the_deduction() -> None:
    yr = _ss_yr(total_deductions=80_000.0, deductions_before_senior_bonus=80_000.0)
    sizing = _size(yr, _lines())
    assert sizing.binding == "0% LTCG"
    assert SS_LTCG_LIMIT - 1.0 <= sizing.amount <= SS_LTCG_LIMIT + 0.05
    assert room_after(yr, _lines())["ltcg_zero"] == LTCG_ZERO_MFJ + 56_000.0  # 1:1 bound


def test_harvest_violations_report_each_constraint_in_binding_order() -> None:
    lines = _lines(aca_cliff=90_000.0, irmaa_t1=85_000.0, niit=80_000.0)
    case = rl._harvest_case(_plain_yr(), lines, CPI, None)
    assert rl._violations(case, 0.0) == []
    assert rl._violations(case, 21_000.0) == ["NIIT"]
    assert rl._violations(case, 26_000.0) == ["IRMAA tier 1", "NIIT"]
    assert rl._violations(case, 31_000.0) == ["ACA cliff", "IRMAA tier 1", "NIIT"]
    assert rl._violations(case, 70_000.0) == ["0% LTCG", "ACA cliff", "IRMAA tier 1", "NIIT"]
    ss_case = rl._harvest_case(_ss_yr(), _lines(), CPI, None)
    assert rl._violations(ss_case, 9_000.0) == ["Social Security tax"]


def test_harvest_sizing_returns_a_feasible_upper_bound_without_bisecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[float] = []
    real = rl._bisect

    def counting(case: object, upper: float) -> tuple[float, float]:
        calls.append(upper)
        return real(case, upper)  # type: ignore[arg-type]

    monkeypatch.setattr(rl, "_bisect", counting)
    _size(_plain_yr(), _lines())
    assert calls == []
    _size(_ss_yr(), _lines())
    assert calls == [LTCG_ZERO_MFJ + 6_000.0]


def test_harvest_bisection_stops_within_a_dollar_or_forty_halvings() -> None:
    case = rl._harvest_case(_ss_yr(), _lines(), CPI, None)
    lo, hi = rl._bisect(case, LTCG_ZERO_MFJ + 6_000.0)
    assert hi - lo <= 1.0
    assert rl._violations(case, lo) == []
    assert rl._violations(case, hi) == ["Social Security tax"]


def test_harvest_prices_with_the_lines_filing_status_when_the_year_has_none() -> None:
    blank = _size(_ss_yr(filing_status=""), _lines())
    assert blank == _size(_ss_yr(filing_status="MFJ"), _lines())
    assert blank.binding == "Social Security tax"


def test_harvest_binds_on_0pct_ltcg_when_ordinary_and_pref_tax_rise_together() -> None:
    # Ordinary tax starts at H = SS_TAX_LIMIT (the unused deduction is gone). The
    # existing 90,194.2 of dividends puts the 0% band's end a few cents earlier.
    yr = _ss_yr(brokerage_qual_div=90_194.2)
    lines = _lines()
    case = rl._harvest_case(yr, lines, CPI, None)
    sizing = _size(yr, lines)
    assert sizing.binding == "0% LTCG"
    past = sizing.amount + 2.0
    rp = rl.reprice(case.yr, 0.0, past, CPI)
    gross = yr.combined_gross - yr.taxable_ss_amt + rp.taxable_ss
    pref_tax = rl._pref_stack_tax(
        rp.taxable_income,
        max(rp.total_deductions - gross, 0.0),
        case.pref_existing,
        past,
        case.thresholds,
        case.rates,
    )
    ordinary_extra = (
        rl._ordinary_tax(lines.filing_status, rp.taxable_income, yr.year, CPI)
        - case.ordinary_tax_at_zero
    )
    assert pref_tax > 0.0
    assert ordinary_extra > 0.0
    assert rl._tax_violation(case, past)[0] == "0% LTCG"


def _senior_yr() -> YearResult:
    """MFJ 2027, both 66, no Social Security: ordinary 148,000 just under the
    150,000 OBBBA phase-out start; 60,000 other deductions + 12,000 senior bonus
    -> ordinary taxable income 76,000 (12% bracket)."""
    return _yr(
        year=2027,
        your_age=66,
        spouse_age=66,
        senior_bonus_ya_eff=66,
        senior_bonus_sa_eff=66,
        combined_gross=148_000.0,
        deductions_before_senior_bonus=60_000.0,
        total_deductions=72_000.0,
        taxable_income=76_000.0,
        magi=148_000.0,
        magi_phaseout_basis=148_000.0,
        niit_magi=148_000.0,
        aca_magi=148_000.0,
    )


def _survivor_case() -> tuple[YearResult, RoomLines]:
    """Single survivor year 2030 (spouse died 2028), survivor 62: SS 30,000 with
    10,000 other income (provisional 25,000, taxable SS 0), deductions 60,000."""
    hh = Household(
        base_year=2026,
        your_age=58,
        spouse_age=56,
        filing_status="MFJ",
        cpi_assumption=CPI,
        survivor=SurvivorScenario("spouse", 2028),
        your_aca_enrolled=False,
        spouse_aca_enrolled=False,
    )
    yr = _yr(
        your_age=62,
        spouse_age=60,
        filing_status="Single",
        combined_ss=30_000.0,
        taxable_ss_amt=0.0,
        ss_provisional_base=10_000.0,
        combined_gross=10_000.0,
        total_deductions=60_000.0,
        deductions_before_senior_bonus=60_000.0,
        taxable_income=0.0,
        magi=10_000.0,
        magi_phaseout_basis=10_000.0,
        niit_magi=10_000.0,
        aca_magi=40_000.0,
    )
    return yr, room_lines_for_year(hh, yr)


# Senior bonus: MAGI hits 150,000 at H = 2,000; past it each dollar removes 0.12
# of deduction (0.06 per person), so ordinary tax starts there.
SENIOR_LIMIT = 2_000.0
# Survivor: taxable SS caps at 25,500 (85%); ordinary 35,500 stays under 60,000,
# leaving 24,500 unused, so the Single 0% band (49,450) ends at H = 73,950.
SURVIVOR_LIMIT = 49_450.0 + 24_500.0


def _pref(yr: YearResult, ytd: YTDSnapshot | None) -> float:
    actual = 0.0 if ytd is None else ytd.qualified_dividends_ytd + ytd.preferential_capital_gain_ytd
    return yr.brokerage_qual_div + yr.realized_gains + actual


def _tax_at(yr: YearResult, lines: RoomLines, pref: float, h: float, cpi: float = CPI) -> float:
    if yr.filing_status != lines.filing_status:
        yr = dataclasses.replace(yr, filing_status=lines.filing_status)
    rp = reprice(yr, 0.0, h, cpi)
    gross = yr.combined_gross - yr.taxable_ss_amt + rp.taxable_ss
    unused = max(rp.total_deductions - gross, 0.0)
    single = lines.filing_status == "Single"
    base = LTCG_THRESHOLDS_SINGLE if single else LTCG_THRESHOLDS_MFJ
    thresholds = index_tuple(base, yr.year, cpi, round50=True)
    rates = LTCG_RATES_SINGLE if single else LTCG_RATES_MFJ
    bracket_tax = federal_tax_single if single else federal_tax
    ordinary = bracket_tax(rp.taxable_income, year=yr.year, cpi=cpi)
    return ordinary + _pref_stack_tax(rp.taxable_income, unused, pref, h, thresholds, rates)


def _cliff_rooms_at(yr: YearResult, lines: RoomLines, h: float, cpi: float = CPI) -> list[float]:
    dtss = reprice(yr, 0.0, h, cpi).taxable_ss - yr.taxable_ss_amt
    rooms = [lines.niit - (yr.niit_magi + dtss + h)]
    if lines.aca_cliff is not None:
        rooms.append(lines.aca_cliff - (yr.aca_magi + h))
    if lines.irmaa_t1 is not None:
        rooms.append(lines.irmaa_t1 - (yr.magi + dtss + h))
    return rooms


def _feasible(yr: YearResult, lines: RoomLines, pref: float, h: float, cpi: float = CPI) -> bool:
    extra = _tax_at(yr, lines, pref, h, cpi) - _tax_at(yr, lines, pref, 0.0, cpi)
    return extra <= 0.005 and min(_cliff_rooms_at(yr, lines, h, cpi)) >= 0.0


_CROSS_CHECK = [
    pytest.param(_plain_yr(), _lines(), None, None, False, id="plain-mfj"),
    pytest.param(_plain_yr(), _lines(), 20_000.0, None, False, id="plain-mfj-embedded-cap"),
    pytest.param(
        _plain_yr(),
        _lines(aca_cliff=90_000.0, irmaa_t1=85_000.0),
        None,
        None,
        False,
        id="plain-mfj-irmaa",
    ),
    pytest.param(
        _plain_yr(),
        _lines(),
        None,
        _ytd(qualified_dividends_ytd=2_000.0, ltcg_ytd=5_000.0),
        False,
        id="base-year-actuals",
    ),
    pytest.param(_pref_below_unused_yr(), _lines(), None, None, False, id="pref-below-unused"),
    pytest.param(_ss_yr(), _lines(), None, None, True, id="taxable-ss-partial-band"),
    pytest.param(
        _ss_yr(total_deductions=80_000.0, deductions_before_senior_bonus=80_000.0),
        _lines(),
        None,
        None,
        True,
        id="taxable-ss-eats-unused-deduction",
    ),
    pytest.param(_senior_yr(), _lines(year=2027), None, None, True, id="senior-bonus-phase-out"),
    pytest.param(*_survivor_case(), None, None, True, id="single-survivor"),
]


@pytest.mark.parametrize(("yr", "lines", "embedded", "ytd", "below_upper"), _CROSS_CHECK)
def test_harvest_cross_check_amount_is_exact(
    yr: YearResult,
    lines: RoomLines,
    embedded: float | None,
    ytd: YTDSnapshot | None,
    below_upper: bool,
) -> None:
    rooms = room_after(yr, lines, ytd)
    sizing = harvest_sizing(yr, lines, rooms, embedded, CPI, ytd)
    pref = _pref(yr, ytd)
    assert _feasible(yr, lines, pref, sizing.amount), sizing
    if sizing.binding == "embedded gain":
        assert sizing.amount == embedded
    else:
        assert not _feasible(yr, lines, pref, sizing.amount + 1.0 + 1.0), sizing
    if below_upper:
        keys = ("ltcg_zero", "aca_cliff", "irmaa_t1", "niit")
        upper = min(room for key in keys if (room := rooms[key]) is not None)
        assert sizing.amount < upper - 1.0, (sizing, upper)


def test_harvest_senior_bonus_phase_out_binds_as_social_security_tax() -> None:
    sizing = _size(_senior_yr(), _lines(year=2027))
    assert sizing.binding == "Social Security tax"
    assert SENIOR_LIMIT - 1.0 <= sizing.amount <= SENIOR_LIMIT + 0.5
    assert room_after(_senior_yr(), _lines(year=2027))["ltcg_zero"] == 22_900.0  # 1:1 bound


def test_harvest_single_survivor_uses_the_single_thresholds() -> None:
    yr, lines = _survivor_case()
    assert (lines.filing_status, lines.ltcg_zero, lines.irmaa_t1, lines.aca_cliff) == (
        "Single",
        49_450.0,
        None,
        None,
    )
    sizing = _size(yr, lines)
    assert sizing.binding == "0% LTCG"
    # MFJ thresholds would give 98,900 + 24,500
    assert SURVIVOR_LIMIT - 1.0 <= sizing.amount <= SURVIVOR_LIMIT + 0.05


def _assert_rows_exact(hh: Household, ytd: YTDSnapshot | None) -> int:
    """Run the real scenario and prove every row's harvest against the oracle."""
    checked = 0
    result = build_income_control(hh, ConversionPlan(), plan_label="x", ytd=ytd)
    for index, row in enumerate(result.years):
        sizing = row.harvest
        assert sizing is not None
        yr = row.year_result
        if index == 0:
            assert yr.year == hh.base_year  # the snapshot belongs to the base year
        lines = room_lines_for_year(hh, yr)
        pref = _pref(yr, ytd if index == 0 else None)
        cpi = hh.cpi_assumption
        crossed = min(_cliff_rooms_at(yr, lines, 0.0, cpi)) < 0.0
        if crossed:
            # a cliff is already behind the plan: the harvest is 0 by design
            assert sizing.amount == 0.0, (yr.year, sizing)
        else:
            assert _feasible(yr, lines, pref, sizing.amount, cpi), (yr.year, sizing)
        if sizing.binding != "embedded gain":
            assert not _feasible(yr, lines, pref, sizing.amount + 2.0, cpi), (yr.year, sizing)
        checked += 1
    return checked


def test_harvest_cross_check_real_scenario_base_year_with_ytd_actuals() -> None:
    ytd = YTDSnapshot(
        tax_year=2026,
        snapshot_date="2026-06-30",
        qualified_dividends_ytd=2_000.0,
        ltcg_ytd=5_000.0,
    )
    assert _assert_rows_exact(_brok_hh(), ytd) > 1


def test_harvest_cross_check_real_scenario_single_survivor_years() -> None:
    hh = _brok_hh(survivor=SurvivorScenario("spouse", 2028))
    result = build_income_control(hh, ConversionPlan(), plan_label="x")
    assert any(row.year_result.filing_status == "Single" for row in result.years)
    assert _assert_rows_exact(hh, None) > 1


def _base_year_federal_tax(hh: Household, ltcg_ytd: float) -> float:
    """Base-year federal income tax (ordinary + preferential, NIIT excluded) when
    ``ltcg_ytd`` of long-term gain is already realized: the scenario's own pricing."""
    ytd = YTDSnapshot(tax_year=hh.base_year, snapshot_date="2026-06-30", ltcg_ytd=ltcg_ytd)
    yr = run_scenario(hh, ConversionPlan(), "x", end_age=hh.your_age + 2, ytd=ytd).years[0]
    assert yr.year == hh.base_year
    return yr.federal_tax_amt + yr.brokerage_gain_tax


def _e2e_sizing(hh: Household) -> HarvestSizing:
    ytd = YTDSnapshot(tax_year=hh.base_year, snapshot_date="2026-06-30")
    sizing = build_income_control(hh, ConversionPlan(), plan_label="x", ytd=ytd).years[0].harvest
    assert sizing is not None
    return sizing


def _assert_harvest_is_tax_free_end_to_end(hh: Household, binding: str) -> None:
    sizing = _e2e_sizing(hh)
    assert sizing.binding == binding, sizing
    assert sizing.amount > 1_000.0, sizing
    baseline = _base_year_federal_tax(hh, 0.0)
    assert _base_year_federal_tax(hh, sizing.amount) - baseline <= 0.01, sizing
    assert _base_year_federal_tax(hh, sizing.amount + 2.0) - baseline > 0.01, sizing


def test_harvest_amount_is_tax_free_end_to_end_through_the_scenario_0pct_ltcg() -> None:
    """The realized gain enters run_scenario as the base-year ``ltcg_ytd`` actual."""
    hh = _brok_hh(brokerage_start_basis=None)
    assert hh.cpi_assumption > 0.0  # thresholds are indexed
    _assert_harvest_is_tax_free_end_to_end(hh, "0% LTCG")


def test_harvest_amount_is_tax_free_end_to_end_through_the_scenario_social_security() -> None:
    hh = _brok_hh(
        brokerage_start_basis=None,
        your_age=70,
        spouse_age=68,
        your_ira=0.0,
        spouse_ira=0.0,
        your_ss_start_age=70,
        spouse_ss_start_age=68,
        your_ss_fra=3_000.0,
        spouse_ss_fra=2_500.0,
    )
    _assert_harvest_is_tax_free_end_to_end(hh, "Social Security tax")
