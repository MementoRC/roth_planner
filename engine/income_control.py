"""Income Control (issue #123, slice 1): post-process ONE run_scenario run into
per-year room under each line. NO tax math here -- numbers come from YearResult;
thresholds from engine.room_lines. NO Streamlit imports (engine purity).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.room_lines import LINE_KEYS, living_flags, room_after, room_lines_for_year
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from engine.this_year import first_rmd_year
from models.household import Household
from models.ytd_income import YTDSnapshot

_DEFAULT_END_AGE = 95  # run_scenario's default horizon (your age)


def window_bounds(hh: Household) -> tuple[int, int]:
    """(window_start, window_end): the base year through the later of the last
    option-grant expiry year and the first RMD year. With no grants the end is
    the first RMD year. end < start means an empty window."""
    start = hh.base_year
    last_expiry = max((g.expiry_year for g in hh.grants), default=None)
    rmd_year = first_rmd_year(hh)
    end = rmd_year if last_expiry is None else max(last_expiry, rmd_year)
    return start, end


def forced_exercise(hh: Household, year: int, option_income: float) -> float:
    """Option income in ``year`` from grants that EXPIRE in ``year`` (exercise is
    forced by the expiry). Same schedule and price resolver ``hh.option_income``
    uses, clamped to [0, option_income] so forced + discretionary == option_income."""
    expiring = [g for g in hh.grants if g.expiry_year == year]
    if not expiring:
        return 0.0
    raw = hh.effective_schedule().income_for(year, expiring, hh.projected_stock_price)
    return max(0.0, min(raw, option_income))


@dataclass(frozen=True)
class IncomeControlYear:
    year: int
    ages: tuple[int | None, int | None]  # (you, spouse); the deceased/absent person's slot is None
    social_security: float  # yr.combined_ss
    rmd: float  # yr.taxable_rmd + yr.spouse_taxable_rmd
    dividends_qualified: float
    dividends_ordinary: float
    interest: float  # 0.0 in slice 1 (no forecast field on YearResult)
    forced_exercise: float
    discretionary_exercise: float
    planned_conversion: float  # your + spouse conversion
    rooms: dict[str, float | None]
    binding_line: str | None
    crossed_lines: tuple[str, ...]
    year_result: YearResult
    # Forced IRA draws + inherited-IRA distributions (taxable, not RMDs).
    ira_draws: float = 0.0

    @property
    def fixed_income(self) -> float:
        return (
            self.social_security
            + self.rmd
            + self.ira_draws
            + self.dividends_qualified
            + self.dividends_ordinary
            + self.interest
        )

    @property
    def total_income(self) -> float:
        return (
            self.fixed_income
            + self.forced_exercise
            + self.discretionary_exercise
            + self.planned_conversion
        )


@dataclass(frozen=True)
class IncomeControlResult:
    years: tuple[IncomeControlYear, ...]
    window_start: int
    window_end: int
    plan_label: str


def _applicable(rooms: dict[str, float | None]) -> list[tuple[str, float]]:
    """(key, room) for every applicable (non-None) line, in LINE_KEYS order."""
    return [(key, room) for key in LINE_KEYS if (room := rooms.get(key)) is not None]


def tightest_line(rooms: dict[str, float | None]) -> str | None:
    """Applicable line with the smallest room >= 0; ties by LINE_KEYS order."""
    applicable = _applicable(rooms)
    candidates = [(room, LINE_KEYS.index(key), key) for key, room in applicable if room >= 0]
    return min(candidates)[2] if candidates else None


def crossed_lines(rooms: dict[str, float | None]) -> tuple[str, ...]:
    """Lines with negative room, most negative first; ties by LINE_KEYS order."""
    applicable = _applicable(rooms)
    crossed = [(room, LINE_KEYS.index(key), key) for key, room in applicable if room < 0]
    return tuple(key for _, _, key in sorted(crossed))


def _row(hh: Household, yr: YearResult) -> IncomeControlYear:
    you_alive, spouse_alive = living_flags(hh, yr)
    ages = (yr.your_age if you_alive else None, yr.spouse_age if spouse_alive else None)
    rooms = room_after(yr, room_lines_for_year(hh, yr))
    forced = forced_exercise(hh, yr.year, yr.option_income)
    return IncomeControlYear(
        year=yr.year,
        ages=ages,
        social_security=yr.combined_ss,
        rmd=yr.taxable_rmd + yr.spouse_taxable_rmd,
        dividends_qualified=yr.brokerage_qual_div,
        dividends_ordinary=yr.brokerage_ord_div,
        interest=0.0,
        forced_exercise=forced,
        discretionary_exercise=yr.option_income - forced,
        planned_conversion=yr.your_conversion + yr.spouse_conversion,
        rooms=rooms,
        binding_line=tightest_line(rooms),
        crossed_lines=crossed_lines(rooms),
        year_result=yr,
        ira_draws=(
            yr.forced_your_ira_draw
            + yr.forced_spouse_ira_draw
            + yr.your_inherited_distribution
            + yr.spouse_inherited_distribution
        ),
    )


def build_income_control(
    hh: Household,
    plan: ConversionPlan,
    *,
    plan_label: str,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> IncomeControlResult:
    """Run the scenario ONCE and turn the decision-window rows into room rows."""
    start, end = window_bounds(hh)
    if end < start:
        return IncomeControlResult((), start, end, plan_label)
    end_age = max(_DEFAULT_END_AGE, hh.your_age + (end - start))
    result = run_scenario(
        hh, plan, plan_label, end_age=end_age, ytd=ytd, net_inv_income=net_inv_income
    )
    by_year = {yr.year: yr for yr in result.years}
    rows = tuple(_row(hh, by_year[y]) for y in range(start, end + 1) if y in by_year)
    return IncomeControlResult(rows, start, end, plan_label)
