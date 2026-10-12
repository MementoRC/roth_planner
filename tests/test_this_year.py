"""engine.this_year: Room This Year engine (redesign PR 4a). Synthetic data only."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from engine.exercise_optimizer import OptimizedPlan
from engine.scenario_types import ConversionPlan
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot

G1 = StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="g1")
G2 = StockGrant(year=2020, strike=150.0, shares=1000, expiry_year=2027, grant_id="g2")


def _hh(your_age: int = 60, spouse_age: int = 54, **kw) -> Household:
    kw.setdefault("grants", [G1, G2])
    kw.setdefault("your_ira", 1_000_000)
    kw.setdefault("spouse_ira", 1_000_000)
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        base_year=2026,
        stock_price_now=200.0,
        **kw,
    )


def _plan(
    label: str = "top-of-22",
    shares: dict[str, int] | None = None,
    you: float = 0.0,
    spouse: float = 0.0,
    bracket_target: int | None = None,
) -> OptimizedPlan:
    sched = ExerciseSchedule()
    for key, n in (shares or {}).items():
        sched.set_shares(key, 2026, n)
    sched.set_price(2026, 200.0)
    conv = ConversionPlan(
        your_conversions={2026: you},
        spouse_conversions={2026: spouse},
        bracket_target=bracket_target,
    )
    return OptimizedPlan(label, sched, conv, lifetime_all_in=0.0)


def _scenario_room(
    hh: Household, plan: OptimizedPlan, level: int, ytd: YTDSnapshot | None = None
) -> float:
    """Unclamped room to bracket ``level``'s top after ``plan``, on the
    scenario's pre-forced-draw basis -- computed independently of this_year."""
    from engine.scenario import run_scenario
    from engine.tax import indexed_bracket_ceiling

    h = copy.deepcopy(hh)
    h.exercise_schedule = plan.schedule
    y = run_scenario(h, plan.conversions, ytd=ytd).years[0]
    ordinary = y.combined_gross - y.forced_your_ira_draw - y.forced_spouse_ira_draw
    top = indexed_bracket_ceiling(
        level, year=2026, cpi=hh.cpi_assumption, filing_status=y.filing_status
    )
    return top - (ordinary - y.total_deductions)


def _scenario_base(hh: Household, plan: OptimizedPlan, ytd: YTDSnapshot | None = None):
    """The scenario's base-year YearResult with ``plan`` applied, computed
    independently of this_year."""
    from engine.scenario import run_scenario

    h = copy.deepcopy(hh)
    h.exercise_schedule = plan.schedule
    return run_scenario(h, plan.conversions, ytd=ytd).years[0]


def _magi_rooms(hh: Household, plan: OptimizedPlan, ytd: YTDSnapshot | None = None):
    """(IRMAA tier-1 room, NIIT room) after ``plan``, unclamped, on real MAGI
    (forced draws included) from the scenario's base year."""
    from engine.irmaa import irmaa_tier1_threshold
    from engine.niit import NIIT_THRESHOLD_MFJ, NIIT_THRESHOLD_SINGLE

    y = _scenario_base(hh, plan, ytd)
    niit = NIIT_THRESHOLD_SINGLE if y.filing_status == "Single" else NIIT_THRESHOLD_MFJ
    irmaa = irmaa_tier1_threshold(2026, y.filing_status, hh.cpi_assumption)
    return irmaa - y.magi, niit - y.niit_magi


def test_this_year_has_no_streamlit_import() -> None:
    src = Path(__file__).resolve().parent.parent / "engine" / "this_year.py"
    assert "streamlit" not in src.read_text()


class TestSummarizeThisYear:
    def test_conversions_remaining_per_person(self) -> None:
        from engine.this_year import summarize_this_year

        ytd = YTDSnapshot(
            tax_year=2026, ira_conversions_ytd=30_000.0, spouse_ira_conversions_ytd=50_000.0
        )
        split = summarize_this_year(
            _hh(), ytd, _plan(you=80_000.0, spouse=40_000.0), current_plan=ConversionPlan()
        )

        you, spouse = split.conversions
        assert (you.person, you.year_total, you.done, you.remaining) == (
            "you",
            80_000.0,
            30_000.0,
            50_000.0,
        )
        # Already did more than planned: year total is what was done, nothing left.
        assert (spouse.person, spouse.year_total, spouse.done, spouse.remaining) == (
            "spouse",
            50_000.0,
            50_000.0,
            0.0,
        )

    def test_filing_status_defaults_to_the_households(self, monkeypatch) -> None:
        import engine.this_year as ty

        seen: list[str] = []
        real = ty.compute_headroom

        def spy(hh, ytd, filing_status="MFJ", **kw):
            seen.append(filing_status)
            return real(hh, ytd, filing_status, **kw)

        monkeypatch.setattr(ty, "compute_headroom", spy)
        ty.summarize_this_year(
            _hh(filing_status="Single"), None, _plan(you=10_000.0), current_plan=ConversionPlan()
        )
        assert seen == ["Single"]

    def test_no_conversion_once_past_rmd_start_age(self) -> None:
        from engine.this_year import summarize_this_year

        split = summarize_this_year(
            _hh(your_age=76, spouse_age=74),
            None,
            _plan(you=80_000.0),
            current_plan=ConversionPlan(),
        )
        assert split.conversions[0].year_total == 0.0
        assert split.conversions[0].remaining == 0.0

    def test_conversion_above_the_ira_balance_uses_what_the_scenario_converts(self) -> None:
        # The optimizer's winning plan can ask for more than the IRA holds; the
        # summary must report what the scenario actually converts.
        from engine.this_year import summarize_this_year

        hh = _hh(your_ira=100_000)
        split = summarize_this_year(hh, None, _plan(you=300_000.0), current_plan=ConversionPlan())

        you = split.conversions[0]
        assert 0.0 < you.year_total <= 100_000.0
        assert you.remaining == you.year_total
        assert split.income_stack.roth == pytest.approx(you.year_total)

        # 12%/22% rooms are on the scenario basis (what is actually converted).
        assert split.room_after.to_22pct == pytest.approx(
            max(_scenario_room(hh, _plan(you=300_000.0), 2), 0.0)
        )

    def test_single_filer_has_no_spouse_row(self) -> None:
        from engine.this_year import summarize_this_year

        split = summarize_this_year(
            _hh(filing_status="Single"),
            None,
            _plan(you=10_000.0),
            filing_status="Single",
            current_plan=ConversionPlan(),
        )
        assert [c.person for c in split.conversions] == ["you"]

    def test_option_income_totals_and_remaining(self) -> None:
        from engine.this_year import summarize_this_year

        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=10_000.0)
        split = summarize_this_year(
            _hh(), ytd, _plan(shares={"g1": 300}), current_plan=ConversionPlan()
        )

        assert split.options_total == pytest.approx(30_000.0)  # 300 x $100 spread
        assert split.options_remaining == pytest.approx(20_000.0)
        assert split.option_floor_shortfall == 0.0
        (g,) = split.exercises
        assert (g.grant_key, g.year_total_shares) == ("g1", 300)
        assert g.year_total_income == pytest.approx(30_000.0)
        assert g.realized_income is None  # no per-grant data on this snapshot
        assert g.remaining_income is None

    def test_floor_shortfall_when_schedule_is_short_of_realized(self) -> None:
        # Realized option income the plan's schedule cannot cover is exposed,
        # not hidden; the year total still counts everything realized.
        from engine.this_year import summarize_this_year

        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=50_000.0)
        split = summarize_this_year(
            _hh(), ytd, _plan(shares={"g1": 100}), current_plan=ConversionPlan()
        )

        assert split.options_total == pytest.approx(50_000.0)
        assert split.options_remaining == 0.0
        assert split.option_floor_shortfall == pytest.approx(40_000.0)

    def test_per_grant_realized_when_known(self) -> None:
        from engine.this_year import summarize_this_year

        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=10_000.0)
        ytd._option_exercises_by_grant = {"g1": 10_000.0}  # type: ignore[attr-defined]
        (g,) = summarize_this_year(
            _hh(), ytd, _plan(shares={"g1": 300}), current_plan=ConversionPlan()
        ).exercises
        assert g.realized_income == 10_000.0
        assert g.remaining_income == pytest.approx(20_000.0)

    def test_room_after_bases(self) -> None:
        # Bracket rooms: the scenario basis, clamped at 0 (same as the reasons).
        # MAGI rooms: the scenario's real MAGI (forced draws included).
        from engine.this_year import summarize_this_year

        hh = _hh()
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=60_000.0, ira_conversions_ytd=10_000.0)
        plan = _plan(shares={"g1": 200}, you=40_000.0)
        split = summarize_this_year(hh, ytd, plan, current_plan=ConversionPlan())

        assert split.room_after.to_12pct == pytest.approx(
            max(_scenario_room(hh, plan, 1, ytd), 0.0)
        )
        assert split.room_after.to_22pct == pytest.approx(
            max(_scenario_room(hh, plan, 2, ytd), 0.0)
        )
        irmaa, niit = _magi_rooms(hh, plan, ytd)
        assert split.room_after.to_irmaa_t1 == pytest.approx(max(irmaa, 0.0))
        assert split.room_after.to_niit == pytest.approx(max(niit, 0.0))

    def test_magi_rooms_include_forecast_income_rmd_household(self) -> None:
        # ytd=None: the RMDs are forecast income headroom's YTD-only rooms miss.
        from engine.this_year import summarize_this_year

        hh = _hh(76, 74)
        plan = _plan("irmaa-safe", shares={"g1": 10})
        split = summarize_this_year(hh, None, plan, current_plan=ConversionPlan())
        irmaa, niit = _magi_rooms(hh, plan)
        assert _scenario_base(hh, plan).your_rmd > 0
        assert split.room_after.to_irmaa_t1 == pytest.approx(max(irmaa, 0.0))
        assert split.room_after.to_niit == pytest.approx(max(niit, 0.0))

    def test_spouse_row_follows_the_scenarios_filing_status(self) -> None:
        from engine.this_year import summarize_this_year

        split = summarize_this_year(
            _hh(filing_status="Single"), None, _plan(you=10_000.0), current_plan=ConversionPlan()
        )
        assert [c.person for c in split.conversions] == ["you"]

    def test_income_stack(self) -> None:
        from engine.this_year import summarize_this_year

        hh = _hh()
        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=60_000.0,
            ira_conversions_ytd=10_000.0,
            nqo_exercise_ytd=5_000.0,
        )
        plan = _plan(shares={"g1": 200}, you=40_000.0)
        split = summarize_this_year(hh, ytd, plan, current_plan=ConversionPlan())
        stack = split.income_stack

        assert stack.roth == pytest.approx(40_000.0)  # year total: 10K done + 30K to come
        assert stack.options == pytest.approx(20_000.0)
        # Taxable-income scale: the bar plus the room left reaches the bracket top.
        assert split.room_after.to_22pct > 0
        assert stack.locked + stack.roth + stack.options + split.room_after.to_22pct == (
            pytest.approx(stack.top_of_22)
        )
        assert stack.locked + stack.roth + stack.options == pytest.approx(
            stack.top_of_22 - _scenario_room(hh, plan, 2, ytd)
        )

    def test_income_stack_rmd_household_reconciles_with_tiles(self) -> None:
        from engine.tax import indexed_bracket_ceiling
        from engine.this_year import summarize_this_year

        hh = _hh(76, 74)
        split = summarize_this_year(
            hh, None, _plan("top-of-22", shares={"g1": 10}), current_plan=ConversionPlan()
        )
        stack = split.income_stack

        assert stack.top_of_12 == indexed_bracket_ceiling(
            1, year=2026, cpi=hh.cpi_assumption, filing_status="MFJ"
        )
        assert stack.top_of_22 == indexed_bracket_ceiling(
            2, year=2026, cpi=hh.cpi_assumption, filing_status="MFJ"
        )
        assert stack.options == pytest.approx(1_000.0)
        assert stack.locked > 0  # the RMDs live here
        assert split.room_after.to_22pct > 0
        assert stack.locked + stack.roth + stack.options + split.room_after.to_22pct == (
            pytest.approx(stack.top_of_22)
        )

    def test_ytd_none_still_produces_everything(self) -> None:
        from engine.this_year import summarize_this_year

        split = summarize_this_year(
            _hh(), None, _plan(shares={"g1": 100}, you=20_000.0), current_plan=ConversionPlan()
        )
        assert split.year == 2026
        assert split.conversions[0].done == 0.0
        assert split.options_remaining == pytest.approx(10_000.0)


class TestReasons:
    @staticmethod
    def _codes(split) -> set:
        return {r.code for r in split.reasons}

    # RMD-age household: required distributions are income the split did not
    # add. 10 shares = $1,000 of option income keeps something still to do
    # (conversions are zero past RMD start age).
    def test_income_the_split_did_not_add_is_already_passed_not_exceeds(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        hh = _hh(76, 74, your_ira=5_000_000, spouse_ira=5_000_000)
        plan = _plan("top-of-22", shares={"g1": 10})
        assert _scenario_room(hh, plan, 2) + 1_000.0 < 0  # RMDs alone cross 22%
        codes = self._codes(summarize_this_year(hh, None, plan, current_plan=ConversionPlan()))
        assert ReasonCode.CEILING_ALREADY_PASSED in codes
        assert ReasonCode.EXCEEDS_CEILING not in codes
        assert ReasonCode.FILLS_BRACKET not in codes

    def test_rmd_income_below_the_ceiling_is_not_blamed_on_the_split(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        hh = _hh(76, 74)
        plan = _plan("top-of-22", shares={"g1": 10})
        room = _scenario_room(hh, plan, 2)
        assert room > 1_000.0  # RMDs alone leave room
        split = summarize_this_year(hh, None, plan, current_plan=ConversionPlan())
        codes = self._codes(split)
        assert not codes & {
            ReasonCode.CEILING_ALREADY_PASSED,
            ReasonCode.EXCEEDS_CEILING,
            ReasonCode.FILLS_BRACKET,
        }
        assert split.room_after.to_22pct == pytest.approx(room)

    def test_irmaa_safe_already_passed_by_rmds_alone(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        hh = _hh(76, 74, your_ira=5_000_000, spouse_ira=5_000_000)
        plan = _plan("irmaa-safe", shares={"g1": 10})
        assert _magi_rooms(hh, plan)[0] + 1_000.0 < 0  # RMDs alone cross tier 1
        assert ReasonCode.CEILING_ALREADY_PASSED in self._codes(
            summarize_this_year(hh, None, plan, current_plan=ConversionPlan())
        )

    def test_irmaa_safe_not_passed_with_room_left(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        plan = _plan("irmaa-safe", shares={"g1": 10})
        codes = self._codes(
            summarize_this_year(_hh(76, 74), None, plan, current_plan=ConversionPlan())
        )
        assert ReasonCode.CEILING_ALREADY_PASSED not in codes

    def test_no_grant_expires_when_the_grant_is_already_fully_realized(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=5_000.0)
        ytd._option_exercises_by_grant = {"g2": 5_000.0}  # type: ignore[attr-defined]
        split = summarize_this_year(
            _hh(), ytd, _plan(shares={"g2": 100}), current_plan=ConversionPlan()
        )  # 100 x $50
        assert ReasonCode.GRANT_EXPIRES not in self._codes(split)

    def test_no_action_still_reports_a_passed_ceiling(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        hh = _hh(76, 74, your_ira=5_000_000, spouse_ira=5_000_000)
        codes = self._codes(
            summarize_this_year(hh, None, _plan("top-of-22"), current_plan=ConversionPlan())
        )
        assert ReasonCode.NO_ACTION in codes
        assert ReasonCode.CEILING_ALREADY_PASSED in codes

    def test_grant_expires_only_for_a_grant_expiring_this_year_or_next(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        with_g2 = summarize_this_year(
            _hh(), None, _plan(shares={"g2": 100}), current_plan=ConversionPlan()
        )
        (reason,) = [r for r in with_g2.reasons if r.code is ReasonCode.GRANT_EXPIRES]
        assert reason.data == {"grant_key": "g2", "expiry_year": 2027}

        only_g1 = summarize_this_year(
            _hh(), None, _plan(shares={"g1": 100}), current_plan=ConversionPlan()
        )
        assert ReasonCode.GRANT_EXPIRES not in self._codes(only_g1)

    @staticmethod
    def _room_to_top(level: int) -> float:
        """Room to bracket ``level``'s top for _hh() with no conversion, on the
        engine's pre-forced-draw basis (taxable income without the waterfall's
        living-expense IRA draws)."""
        from engine.scenario import run_scenario
        from engine.tax import indexed_bracket_ceiling

        hh = _hh()
        y = run_scenario(hh, ConversionPlan()).years[0]
        ordinary = y.combined_gross - y.forced_your_ira_draw - y.forced_spouse_ira_draw
        top = indexed_bracket_ceiling(level, year=2026, cpi=hh.cpi_assumption, filing_status="MFJ")
        return top - (ordinary - y.total_deductions)

    # top-of-22 / top-of-24: the scenario caps a bracket-fill conversion at its
    # bracket top (bracket_target 2/3), so asking for more converts exactly to
    # the top. top-of-12 cannot use that mechanism: in a waterfall year the
    # scenario caps targets other than 3 at room_22, so a 12% request is sized
    # exactly instead (deductions don't move with the conversion at 60/54).
    # The cap comes from the CURRENT plan's bracket_target (the base year is
    # projected as Apply writes it), so the current plan carries the target.
    @pytest.mark.parametrize(
        ("label", "target", "ask"),
        [("top-of-12", 1, None), ("top-of-22", 2, 900_000.0), ("top-of-24", 3, 900_000.0)],
    )
    def test_fills_bracket_when_the_split_reaches_the_top(self, label, target, ask) -> None:
        from engine.this_year import FILL_TOLERANCE, ReasonCode, summarize_this_year

        you = ask if ask is not None else self._room_to_top(1)
        split = summarize_this_year(
            _hh(),
            None,
            _plan(label, you=you, bracket_target=target),
            current_plan=ConversionPlan(bracket_target=target),
        )
        (reason,) = [r for r in split.reasons if r.code is ReasonCode.FILLS_BRACKET]
        assert reason.data["ceiling_label"] == label
        assert abs(reason.data["room_after"]) <= FILL_TOLERANCE
        assert ReasonCode.EXCEEDS_CEILING not in self._codes(split)
        if ask is None:
            # Pin the mechanism: exactly the requested amount converted.
            assert split.conversions[0].year_total == pytest.approx(you)

    def test_overshoot_from_options_is_exceeds_ceiling(self) -> None:
        # 5,000 shares x $100 spread = $500K of option income against an empty
        # year: room before the split is positive, after it is far negative.
        from engine.this_year import FILL_TOLERANCE, ReasonCode, summarize_this_year

        split = summarize_this_year(
            _hh(), None, _plan("top-of-22", shares={"g1": 5000}), current_plan=ConversionPlan()
        )
        codes = self._codes(split)
        assert ReasonCode.FILLS_BRACKET not in codes
        assert ReasonCode.CEILING_ALREADY_PASSED not in codes
        (r,) = [r for r in split.reasons if r.code is ReasonCode.EXCEEDS_CEILING]
        assert r.data["ceiling_label"] == "top-of-22"
        assert r.data["overshoot"] > FILL_TOLERANCE

    def test_twelve_percent_request_capped_at_22_is_exceeds_ceiling(self) -> None:
        # In a waterfall year the scenario caps a 12%-target conversion at the
        # 22% top, so it lands past the 12% ceiling: an overshoot, not a fill.
        from engine.this_year import ReasonCode, summarize_this_year

        split = summarize_this_year(
            _hh(),
            None,
            _plan("top-of-12", you=900_000.0, bracket_target=1),
            current_plan=ConversionPlan(),
        )
        codes = self._codes(split)
        assert ReasonCode.EXCEEDS_CEILING in codes
        assert ReasonCode.FILLS_BRACKET not in codes

    @pytest.mark.parametrize(
        ("label", "target"), [("top-of-12", 1), ("top-of-22", 2), ("top-of-24", 3)]
    )
    def test_no_fills_bracket_when_room_is_left(self, label, target) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        split = summarize_this_year(
            _hh(),
            None,
            _plan(label, you=5_000.0, bracket_target=target),
            current_plan=ConversionPlan(),
        )
        assert ReasonCode.FILLS_BRACKET not in self._codes(split)

    def test_no_fills_bracket_for_magi_ceilings(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        split = summarize_this_year(
            _hh(), None, _plan("irmaa-safe", you=900_000.0), current_plan=ConversionPlan()
        )
        assert ReasonCode.FILLS_BRACKET not in self._codes(split)

    def test_reason_data_is_read_only(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        split = summarize_this_year(_hh(), None, _plan(you=20_000.0), current_plan=ConversionPlan())
        (r,) = [r for r in split.reasons if r.code is ReasonCode.IRMAA_LOOKBACK]
        with pytest.raises(TypeError):
            r.data["counts"] = True  # type: ignore[index]

    def test_irmaa_lookback_counts_only_from_the_first_relevant_year(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        def lookback(hh: Household):
            split = summarize_this_year(
                hh, None, _plan(you=20_000.0), current_plan=ConversionPlan()
            )
            (r,) = [r for r in split.reasons if r.code is ReasonCode.IRMAA_LOOKBACK]
            return r.data

        young = lookback(_hh(60, 54))  # 65 in 2031 -> first relevant income year = 2029
        assert young == {"payment_year": 2028, "counts": False, "first_relevant_year": 2029}
        older = lookback(_hh(64, 60))  # 64 + 2 >= 65: 2026 income already counts
        assert older == {"payment_year": 2028, "counts": True, "first_relevant_year": 2026}

    def test_ceiling_already_passed(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        ytd = YTDSnapshot(tax_year=2026, wages_ytd=400_000.0)
        split = summarize_this_year(
            _hh(), ytd, _plan("top-of-22", you=20_000.0), current_plan=ConversionPlan()
        )
        assert ReasonCode.CEILING_ALREADY_PASSED in self._codes(split)
        assert ReasonCode.FILLS_BRACKET not in self._codes(split)

    def test_top_of_24_ceiling_already_passed(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        ytd = YTDSnapshot(tax_year=2026, wages_ytd=600_000.0)
        split = summarize_this_year(
            _hh(),
            ytd,
            _plan("top-of-24", you=20_000.0, bracket_target=3),
            current_plan=ConversionPlan(),
        )
        assert ReasonCode.CEILING_ALREADY_PASSED in self._codes(split)
        assert ReasonCode.FILLS_BRACKET not in self._codes(split)

    def test_aca_safe_passed_uses_the_aca_cliff(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        on_aca = _hh(your_aca_enrolled=True, spouse_aca_enrolled=True)
        over = YTDSnapshot(tax_year=2026, wages_ytd=250_000.0)  # past 400% FPL
        under = YTDSnapshot(tax_year=2026, wages_ytd=20_000.0)
        assert ReasonCode.CEILING_ALREADY_PASSED in self._codes(
            summarize_this_year(
                on_aca, over, _plan("aca-safe", you=5_000.0), current_plan=ConversionPlan()
            )
        )
        assert ReasonCode.CEILING_ALREADY_PASSED not in self._codes(
            summarize_this_year(
                on_aca, under, _plan("aca-safe", you=5_000.0), current_plan=ConversionPlan()
            )
        )
        # Nobody on ACA: the cliff room is meaningless, never "passed".
        off_aca = _hh(your_aca_enrolled=False, spouse_aca_enrolled=False)
        assert ReasonCode.CEILING_ALREADY_PASSED not in self._codes(
            summarize_this_year(
                off_aca, over, _plan("aca-safe", you=5_000.0), current_plan=ConversionPlan()
            )
        )

    def test_colliding_tranches_rows_and_total_agree(self) -> None:
        from engine.this_year import summarize_this_year

        a = StockGrant(year=2019, strike=100.0, shares=300, expiry_year=2030)
        b = StockGrant(year=2019, strike=100.0, shares=200, expiry_year=2030)
        hh = _hh(grants=[a, b])
        split = summarize_this_year(
            hh, None, _plan(shares={a.key(): 400}), current_plan=ConversionPlan()
        )
        assert split.options_total == pytest.approx(
            sum(g.year_total_income for g in split.exercises)
        )

    def test_no_action_when_nothing_remains(self) -> None:
        from engine.this_year import ReasonCode, summarize_this_year

        split = summarize_this_year(_hh(), None, _plan(), current_plan=ConversionPlan())
        assert ReasonCode.NO_ACTION in self._codes(split)
        assert ReasonCode.FILLS_BRACKET not in self._codes(split)


def _first_scenario_rmd_year(hh: Household) -> int:
    """First year the scenario itself books an RMD for anyone (no conversions),
    computed independently of this_year."""
    from engine.scenario import run_no_conversion

    result = run_no_conversion(copy.deepcopy(hh), end_age=95)
    return next(y.year for y in result.years if y.your_rmd + y.spouse_rmd > 0.0)


class TestMagiRooms:
    @pytest.mark.parametrize(
        ("status", "threshold"), [("MFJ", 250_000), ("Single", 200_000), ("HoH", 200_000)]
    )
    def test_niit_room_uses_the_filing_status_threshold(self, status, threshold) -> None:
        from types import SimpleNamespace

        from engine.this_year import _magi_rooms_after

        base = SimpleNamespace(filing_status=status, magi=150_000.0, niit_magi=150_000.0)
        _, niit_room = _magi_rooms_after(_hh(), base)  # type: ignore[arg-type]
        assert niit_room == pytest.approx(threshold - 150_000.0)


class TestFirstRmdYear:
    def test_you_first(self) -> None:
        from engine.this_year import first_rmd_year

        # Born 1954 / 1956 -> start age 73 for both: you 2027, spouse 2029.
        assert first_rmd_year(_hh(72, 70)) == 2027

    def test_spouse_first(self) -> None:
        from engine.this_year import first_rmd_year

        assert first_rmd_year(_hh(70, 72)) == 2027

    def test_deferral_moves_it_a_year(self) -> None:
        from engine.this_year import first_rmd_year

        assert first_rmd_year(_hh(72, 70, your_defer_first_rmd=True)) == 2028

    def test_already_past_is_this_year(self) -> None:
        from engine.this_year import first_rmd_year

        assert first_rmd_year(_hh(75, 74)) == 2026

    def test_single_ignores_spouse(self) -> None:
        from engine.this_year import first_rmd_year

        assert first_rmd_year(_hh(70, 72, filing_status="Single")) == 2029

    @pytest.mark.parametrize(
        ("ages", "flags"),
        [
            ((72, 70), {}),
            ((70, 72), {}),
            ((72, 70), {"your_defer_first_rmd": True}),
            ((70, 72), {"spouse_defer_first_rmd": True}),
            ((73, 60), {"your_defer_first_rmd": True}),
            ((66, 60), {}),
        ],
    )
    def test_matches_the_scenarios_own_rmd_start(self, ages, flags) -> None:
        from engine.this_year import first_rmd_year

        hh = _hh(*ages, **flags)
        assert first_rmd_year(hh) == _first_scenario_rmd_year(hh)


class TestRmdLookahead:
    @staticmethod
    def _setup(**hh_kw):
        from engine.this_year import summarize_this_year

        hh = _hh(70, 68, grants=[G1], **hh_kw)
        sched = ExerciseSchedule()
        sched.set_shares("g1", 2026, 100)
        sched.set_shares("g1", 2027, 400)
        hh.exercise_schedule = sched
        current = ConversionPlan(your_conversions={2026: 10_000.0, 2027: 10_000.0})
        split_plan = _plan(shares={"g1": 300}, you=60_000.0)
        split = summarize_this_year(hh, None, split_plan, current_plan=current)
        return hh, current, split, split_plan

    def test_three_cases_in_order_for_the_first_rmd_year(self) -> None:
        from engine.this_year import LookaheadCase, first_rmd_year, rmd_lookahead

        hh, current, split, split_plan = self._setup()
        look = rmd_lookahead(hh, None, current, split, split_plan)
        assert look.year == first_rmd_year(hh)
        assert [c.case for c in look.cases] == [
            LookaheadCase.NONE,
            LookaheadCase.CURRENT,
            LookaheadCase.SPLIT,
        ]
        for c in look.cases:
            assert c.year == look.year
            assert c.total_rmd == pytest.approx(c.your_rmd + c.spouse_rmd)

    def test_conversions_shrink_the_rmd(self) -> None:
        from engine.this_year import LookaheadCase, rmd_lookahead

        hh, current, split, split_plan = self._setup()
        look = rmd_lookahead(hh, None, current, split, split_plan)
        none_, cur, spl = (look.case(c) for c in LookaheadCase)
        assert none_.total_rmd > cur.total_rmd > spl.total_rmd

    def test_split_case_equals_a_manual_apply(self) -> None:
        from engine.irmaa import irmaa_tier
        from engine.scenario import run_scenario
        from engine.this_year import LookaheadCase, rmd_lookahead

        hh, current, split, split_plan = self._setup()
        look = rmd_lookahead(hh, None, current, split, split_plan)
        assert split.conversions[0].year_total == pytest.approx(60_000.0)

        manual = copy.deepcopy(hh)
        manual_sched = ExerciseSchedule()
        manual_sched.set_shares("g1", 2026, 300)  # split's year total
        manual_sched.set_shares("g1", 2027, 200)  # 400 - 200 extra taken (nearest later year)
        manual_sched.set_price(2026, 200.0)
        manual.exercise_schedule = manual_sched
        manual_plan = ConversionPlan(
            your_conversions={2026: 60_000.0, 2027: 10_000.0}, spouse_conversions={2026: 0.0}
        )
        result = run_scenario(manual, manual_plan, end_age=95)
        yr = next(y for y in result.years if y.year == look.year)

        got = look.case(LookaheadCase.SPLIT)
        assert got.your_rmd == pytest.approx(yr.your_rmd)
        assert got.spouse_rmd == pytest.approx(yr.spouse_rmd)
        assert got.taxable_income == pytest.approx(yr.taxable_income)
        assert got.marginal_bracket == pytest.approx(yr.marginal_bracket)
        assert got.irmaa_tier == irmaa_tier(
            yr.magi, yr.filing_status, year=look.year + 2, cpi=hh.cpi_assumption
        )

    @staticmethod
    def _split_base_year(hh, ytd, current, split, split_plan):
        """The SPLIT case's base-year YearResult, built by hand the way Apply
        writes it -- independent of rmd_lookahead's internals."""
        from engine.scenario import run_scenario
        from engine.this_year import apply_split_conversions, apply_split_schedule

        h = copy.deepcopy(hh)
        h.exercise_schedule = apply_split_schedule(hh, split_plan)
        plan = apply_split_conversions(current, split)
        return run_scenario(h, plan, end_age=95, ytd=ytd).years[0]

    def test_conversion_above_the_ira_balance_does_not_leak_into_split(self) -> None:
        # The raw winner asks for more than the IRA holds; the split case
        # converts what the scenario actually converts, never the raw ask.
        from engine.this_year import apply_split_conversions, summarize_this_year

        hh, current, _, _ = self._setup(your_ira=100_000)
        split_plan = _plan(shares={"g1": 300}, you=300_000.0)
        split = summarize_this_year(hh, None, split_plan, current_plan=current)
        plan = apply_split_conversions(current, split)
        assert plan.your_conversions[2026] == pytest.approx(split.conversions[0].year_total)
        assert plan.your_conversions[2026] <= 100_000.0
        assert plan.your_conversions[2027] == 10_000.0
        # And the SPLIT run really converts the written amount.
        base = self._split_base_year(hh, None, current, split, split_plan)
        assert base.your_conversion == pytest.approx(plan.your_conversions[2026])

    def test_top_of_24_winner_summary_matches_what_split_converts(self) -> None:
        # The winner sizes to the 24% top (bracket_target=3) but Apply writes
        # amounts into the CURRENT plan (no bracket_target): in a waterfall base
        # year the scenario then caps at the 22% room. The summary must show
        # what Apply will produce, not the winner's own cap.
        from engine.this_year import summarize_this_year

        hh, current, _, _ = self._setup()
        winner = _plan("top-of-24", shares={"g1": 300}, you=900_000.0, bracket_target=3)
        split = summarize_this_year(hh, None, winner, current_plan=current)
        base = self._split_base_year(hh, None, current, split, winner)
        assert base.your_conversion == pytest.approx(split.conversions[0].year_total)
        # The exercise half agrees too: the rebalanced SPLIT schedule carries
        # the same base-year option income the summary's stack shows.
        assert base.option_income == pytest.approx(split.income_stack.options)

    def test_split_with_conversions_already_done_is_not_double_netted(self) -> None:
        from engine.this_year import summarize_this_year

        ytd = YTDSnapshot(tax_year=2026, ira_conversions_ytd=20_000.0)
        hh, current, _, split_plan = self._setup()
        split = summarize_this_year(hh, ytd, split_plan, current_plan=current)
        you = split.conversions[0]
        assert you.done == 20_000.0
        base = self._split_base_year(hh, ytd, current, split, split_plan)
        # The scenario nets what was done once: it converts only what remains.
        assert base.your_conversion == pytest.approx(you.remaining)
        assert you.year_total == pytest.approx(you.done + you.remaining)

    def test_live_household_not_mutated(self) -> None:
        from engine.this_year import rmd_lookahead

        hh, current, split, split_plan = self._setup()
        before = copy.deepcopy(hh)
        current_before = copy.deepcopy(current)
        plan_before = copy.deepcopy(split_plan)
        rmd_lookahead(hh, None, current, split, split_plan)
        assert hh == before
        assert current == current_before
        assert split_plan == plan_before

    def test_apply_helpers_replace_only_this_year(self) -> None:
        from engine.this_year import apply_split_conversions, apply_split_schedule

        hh, current, split, split_plan = self._setup()
        plan = apply_split_conversions(current, split)
        assert plan.your_conversions == {2026: 60_000.0, 2027: 10_000.0}
        assert plan.spouse_conversions == {2026: 0.0}
        sched = apply_split_schedule(hh, split_plan)
        assert sched.shares_by_grant_year["g1"] == {2026: 300, 2027: 200}

    def test_apply_schedule_skips_grants_expired_before_this_year(self) -> None:
        from engine.this_year import apply_split_schedule

        old = StockGrant(year=2015, strike=50.0, shares=100, expiry_year=2025, grant_id="old")
        hh, _, _, split_plan = self._setup()
        hh.grants = [G1, old]
        hh.exercise_schedule.set_shares("old", 2025, 100)
        sched = apply_split_schedule(hh, split_plan)  # must not raise
        assert sched.shares_by_grant_year["old"] == {2025: 100}
        assert sched.shares_by_grant_year["g1"] == {2026: 300, 2027: 200}

    def test_single_household_split_has_no_spouse_conversion(self) -> None:
        from engine.this_year import apply_split_conversions, summarize_this_year

        hh, current, _, _ = self._setup(filing_status="Single")
        current.spouse_conversions[2026] = 5_000.0
        split = summarize_this_year(
            hh, None, _plan(shares={"g1": 300}, you=60_000.0), current_plan=ConversionPlan()
        )
        plan = apply_split_conversions(current, split)
        assert plan.spouse_conversions[2026] == 0.0
