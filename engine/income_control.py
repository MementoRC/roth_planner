"""Income Control (issue #123, slices 1-2): post-process ONE run_scenario run into
per-year room under each line, plus the labelled income stack (done + remaining
per line, slice 2). NO tax math here -- numbers come from YearResult;
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
# Half-cent tolerance: a float difference below it is noise. Mirrors
# tests/acceptance/dsl.py (the engine can't import test code).
_CENT = 0.005


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
class StackLine:
    """One line of the income stack: ``done`` (actual so far) + ``remaining`` (forecast).

    ``key`` is the stable identifier, ``label`` the display text. ``plan`` is the
    plan's FULL-YEAR amount, used only for the above-plan note; None means there is
    nothing to compare against (actual-only lines, no ConversionPlan). On forecast-only
    lines plan equals ``remaining``, so ``above_plan`` is 0.
    """

    key: str
    label: str
    done: float
    remaining: float
    plan: float | None = None  # plan's full-year amount; None for actual-only lines
    magi_only: bool = False  # long-term capital gains / qualified dividends

    @property
    def full_year(self) -> float:
        """Done so far plus the remaining forecast."""
        return self.done + self.remaining

    @property
    def above_plan(self) -> float:
        """How far the full year exceeds plan; never negative, 0 without a plan."""
        if self.plan is None:
            return 0.0
        excess = self.full_year - self.plan
        return excess if excess >= _CENT else 0.0


def _full_year_of(lines: tuple[StackLine, ...], key: str, default: float) -> float:
    return next((line.full_year for line in lines if line.key == key), default)


@dataclass(frozen=True)
class IncomeControlYear:
    year: int
    ages: tuple[int | None, int | None]  # (you, spouse); the deceased/absent person's slot is None
    social_security: float  # yr.combined_ss
    rmd: float  # yr.taxable_rmd + yr.spouse_taxable_rmd
    dividends_qualified: float
    dividends_ordinary: float
    interest: float  # the interest line's full year (0.0 without a snapshot)
    forced_exercise: float
    discretionary_exercise: float
    planned_conversion: float  # your + spouse conversion
    rooms: dict[str, float | None]
    binding_line: str | None
    crossed_lines: tuple[str, ...]
    year_result: YearResult
    # Forced IRA draws + inherited-IRA distributions (taxable, not RMDs).
    ira_draws: float = 0.0
    # Slice 2: the labelled stack (done + remaining per line) and whether this
    # row carries this-year actuals (base year with the toggle on and a snapshot).
    stack_lines: tuple[StackLine, ...] = ()
    has_actuals: bool = False

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
        """Sum of the line full-years; rows built without lines (hand-built in view
        tests) fall back to the slice 1 formula."""
        if self.stack_lines:
            return sum(line.full_year for line in self.stack_lines)
        return (
            self.fixed_income
            + self.forced_exercise
            + self.discretionary_exercise
            + self.planned_conversion
        )

    @property
    def magi_only_total(self) -> float:
        """Of the total, what reaches MAGI but not the ordinary brackets."""
        return sum(line.full_year for line in self.stack_lines if line.magi_only)

    @property
    def stack_fixed_income(self) -> float:
        """Everything in the total that is neither exercise nor conversion (chart base layer)."""
        return (
            self.total_income
            - self.full_year_of("forced_exercise", self.forced_exercise)
            - self.full_year_of("discretionary_exercise", self.discretionary_exercise)
            - self.full_year_of("conversion", self.planned_conversion)
        )

    def full_year_of(self, key: str, default: float) -> float:
        """Full-year amount of line ``key``, or ``default`` when the row has no such line."""
        return _full_year_of(self.stack_lines, key, default)


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


def _forecast(key: str, label: str, value: float, *, magi_only: bool = False) -> StackLine:
    """A pure-forecast line: nothing done yet, plan == the forecast."""
    return StackLine(key, label, 0.0, value, value, magi_only)


def _clamped_rmd(rmd: float, qcd: float) -> float:
    """scenario_compute.compute_rmds' taxable form: max(rmd - min(qcd, rmd), 0)."""
    return max(rmd - min(qcd, rmd), 0.0)


def _rmd_line(yr: YearResult, ytd: YTDSnapshot | None) -> StackLine:
    """RMD line: pooled YTD IRA distributions done, reduced taxable RMD remaining."""
    remaining = yr.taxable_rmd + yr.spouse_taxable_rmd
    if ytd is None:
        return _forecast("rmd", "Required minimum distributions", remaining)
    plan = _clamped_rmd(yr.your_rmd, yr.qcd) + _clamped_rmd(yr.spouse_rmd, yr.spouse_qcd)
    return StackLine(
        "rmd", "Required minimum distributions", ytd.ira_distributions_ytd, remaining, plan
    )


def _dividend_lines(yr: YearResult, ytd: YTDSnapshot | None) -> list[StackLine]:
    """Base-year forecast dividends are 0 with a snapshot, so these are done-only there."""
    ordinary = 0.0 if ytd is None else ytd.ordinary_dividends_ytd
    qualified = 0.0 if ytd is None else ytd.qualified_dividends_ytd
    return [
        StackLine("dividends_ordinary", "Ordinary dividends", ordinary, yr.brokerage_ord_div),
        StackLine(
            "dividends_qualified",
            "Qualified dividends",
            qualified,
            yr.brokerage_qual_div,
            magi_only=True,
        ),
    ]


def _capital_gain_lines(ytd: YTDSnapshot | None) -> list[StackLine]:
    """Actual-only: the engine's own §1211-capped ordinary / preferential split."""
    if ytd is None:
        return []
    return [
        StackLine(
            "capital_gain_ordinary",
            "Capital gains (ordinary part)",
            ytd.ordinary_capital_gain_ytd,
            0.0,
        ),
        StackLine(
            "capital_gain_long_term",
            "Capital gains (long-term part)",
            ytd.preferential_capital_gain_ytd,
            0.0,
            magi_only=True,
        ),
    ]


def _actual_income_lines(ytd: YTDSnapshot | None) -> list[StackLine]:
    """Actual-only ordinary lines (and the negative above-the-line adjustments)."""
    if ytd is None:
        return []
    return [
        StackLine("wages", "Wages", ytd.wages_ytd, 0.0),
        StackLine("nec", "1099-NEC", ytd.nec_income_ytd, 0.0),
        StackLine("interest", "Interest", ytd.interest_ytd, 0.0),
        StackLine("crypto_income", "Crypto income", ytd.crypto_income_ytd, 0.0),
        StackLine(
            "adjustments",
            "Adjustments (HSA, deductible IRA)",
            -ytd.above_the_line_adjustments_ytd,
            0.0,
        ),
    ]


def _option_lines(yr: YearResult, forced_plan: float, ytd: YTDSnapshot | None) -> list[StackLine]:
    """Forced (grant-expiry) exercises fill first; the rest is discretionary.

    DISPLAY rule only: just the total nqo_exercise_ytd is persisted, not the per-grant
    split. done + max(plan - done, 0) == max(plan, done), the engine's bounded option income.
    """
    disc_plan = yr.option_income - forced_plan
    nqo = 0.0 if ytd is None else max(ytd.nqo_exercise_ytd, 0.0)
    forced_done = min(nqo, forced_plan)
    disc_done = nqo - forced_done
    return [
        StackLine(
            "forced_exercise",
            "Forced exercise (grant expires)",
            forced_done,
            max(forced_plan - forced_done, 0.0),
            forced_plan,
        ),
        StackLine(
            "discretionary_exercise",
            "Discretionary exercise",
            disc_done,
            max(disc_plan - disc_done, 0.0),
            disc_plan,
        ),
    ]


def _planned_conversion(hh: Household, yr: YearResult, plan: ConversionPlan) -> float:
    """The plan's conversion for the year, zeroed at each person's RMD start age
    (mirrors scenario_compute.compute_conversions)."""
    yours = 0.0 if yr.your_age >= hh.your_rmd_start_age else plan.your_conversions.get(yr.year, 0.0)
    spouse = (
        0.0
        if yr.spouse_age >= hh.spouse_rmd_start_age
        else plan.spouse_conversions.get(yr.year, 0.0)
    )
    return yours + spouse


def _conversion_line(
    hh: Household, yr: YearResult, plan: ConversionPlan | None, ytd: YTDSnapshot | None
) -> StackLine:
    """Conversion line: YTD conversions done, engine remainder, and the plan's amount."""
    remaining = yr.your_conversion + yr.spouse_conversion  # already net of YTD conversions
    if ytd is None:
        return _forecast("conversion", "Planned conversion", remaining)
    done = ytd.ira_conversions_ytd + ytd.spouse_ira_conversions_ytd
    planned = None if plan is None else _planned_conversion(hh, yr, plan)
    return StackLine("conversion", "Planned conversion", done, remaining, planned)


def _ira_draws(yr: YearResult) -> float:
    """Forced IRA draws plus inherited-IRA distributions (taxable, not RMDs)."""
    return (
        yr.forced_your_ira_draw
        + yr.forced_spouse_ira_draw
        + yr.your_inherited_distribution
        + yr.spouse_inherited_distribution
    )


def _stack_lines(
    hh: Household,
    yr: YearResult,
    forced_plan: float,
    plan: ConversionPlan | None,
    ytd: YTDSnapshot | None,
) -> tuple[StackLine, ...]:
    """The labelled income stack for one row; ``forced_plan`` is the grant-expiry option income."""
    lines = [
        _forecast("social_security", "Social Security", yr.combined_ss),
        _rmd_line(yr, ytd),
        _forecast("ira_draws", "Forced / inherited IRA draws", _ira_draws(yr)),
        _forecast(
            "extra_withdrawals",
            "Extra IRA withdrawals",
            yr.extra_withdrawal + yr.spouse_extra_withdrawal,
        ),
        _forecast("roth_earnings", "Roth-earnings income", yr.roth_earnings_income),
        *_actual_income_lines(ytd),
        *_dividend_lines(yr, ytd),
        *_capital_gain_lines(ytd),
        *_option_lines(yr, forced_plan, ytd),
        _conversion_line(hh, yr, plan, ytd),
    ]
    return tuple(lines)


def _row(
    hh: Household,
    yr: YearResult,
    plan: ConversionPlan | None = None,
    ytd: YTDSnapshot | None = None,
) -> IncomeControlYear:
    you_alive, spouse_alive = living_flags(hh, yr)
    ages = (yr.your_age if you_alive else None, yr.spouse_age if spouse_alive else None)
    rooms = room_after(yr, room_lines_for_year(hh, yr))
    forced = forced_exercise(hh, yr.year, yr.option_income)
    lines = _stack_lines(hh, yr, forced, plan, ytd)

    def full(key: str, default: float) -> float:
        return _full_year_of(lines, key, default)

    return IncomeControlYear(
        year=yr.year,
        ages=ages,
        social_security=yr.combined_ss,
        rmd=yr.taxable_rmd + yr.spouse_taxable_rmd,
        # Legacy fields follow the lines so a base-year row never contradicts its snapshot.
        dividends_qualified=full("dividends_qualified", yr.brokerage_qual_div),
        dividends_ordinary=full("dividends_ordinary", yr.brokerage_ord_div),
        interest=full("interest", 0.0),
        forced_exercise=forced,
        discretionary_exercise=yr.option_income - forced,
        planned_conversion=yr.your_conversion + yr.spouse_conversion,
        rooms=rooms,
        binding_line=tightest_line(rooms),
        crossed_lines=crossed_lines(rooms),
        year_result=yr,
        ira_draws=_ira_draws(yr),
        stack_lines=lines,
        has_actuals=ytd is not None,
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
    rows = tuple(
        _row(hh, by_year[y], plan, ytd if y == start else None)
        for y in range(start, end + 1)
        if y in by_year
    )
    return IncomeControlResult(rows, start, end, plan_label)
