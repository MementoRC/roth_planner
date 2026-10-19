"""audit-1018 W11: input validation and model hardening.

- F-26: Option Exercises page crashed when every grant expired before base year.
- F-33: Annual Living Expenses widget was the one Assumptions numeric unclamped.
- F-35: forecast qualified_fraction override was not clamped to [0, 1].
- F-44: SourcedDict/SourcedList immutability guard missed |=, sort, reverse, *=.
- F-45: StockGrant.key() ``:g`` collided distinct high strikes.
- F-46: NaN / bool values passed model deserializers.
- F-47: Roth Eligibility Trad-contribution inputs accepted negatives.

All figures are synthetic.
"""

from __future__ import annotations

import json
import math
from datetime import datetime

import pytest
from streamlit.testing.v1 import AppTest

from engine.dividend_breakdown import qualified_breakdown
from engine.dividend_forecast import Position, forecast_portfolio
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant, aggregate_by_key
from models.household import InheritedIRA
from models.sourced import Provenance, Source, SourcedDict, SourcedList
from models.ytd_income import manual_mark_from_dict

_AT = datetime(2026, 6, 1, 12, 0, 0)


def _prov(source: Source = Source.PDF) -> Provenance:
    return Provenance(source, _AT, "synthetic")


# --- F-26 -----------------------------------------------------------------


def _render_expired_oe() -> None:
    import streamlit as st

    from models.grants import StockGrant
    from models.household import Household
    from views.option_exercise import render

    st.session_state["_suppress_snapshot_autoload"] = True
    hh = Household(
        grants=[StockGrant(2014, 30.0, 500, 2024), StockGrant(2015, 35.0, 400, 2025)],
        base_year=2026,
    )
    render(hh)


def test_f26_all_expired_grants_show_message_not_crash(monkeypatch) -> None:
    import views.option_exercise as oe_module

    monkeypatch.setattr(oe_module, "save_exercise_schedule", lambda s: None)
    monkeypatch.setattr(oe_module, "clear_exercise_schedule", lambda: None)
    at = AppTest.from_function(_render_expired_oe)
    at.run()
    assert not at.exception
    assert any("expired" in i.value.lower() for i in at.info)


# --- F-33 -----------------------------------------------------------------


def _render_assumptions() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_assumptions_partial

    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("_pending_review", set())
    render_assumptions_partial(Household(), st)


@pytest.mark.parametrize(
    ("raw", "shown"),
    [(-5000, 0), (60000.5, 60000), (60000.0, 60000), (60000, 60000)],
)
def test_f33_living_expenses_widget_clamps_and_casts(raw, shown) -> None:
    at = AppTest.from_function(_render_assumptions)
    at.session_state["living_expenses"] = raw
    at.run()
    assert not at.exception
    widget = next(w for w in at.number_input if w.label == "Annual Living Expenses")
    assert widget.value == shown


# --- F-35 -----------------------------------------------------------------


def _forecast(tmp_path, overrides: dict, positions: list[Position]):
    path = tmp_path / ".dividend_rates.json"
    path.write_text(json.dumps(overrides))
    return forecast_portfolio(positions, 10_000.0, overrides_path=path, employer_ticker="")


def test_f35_out_of_range_override_is_clamped_and_matches_breakdown(tmp_path) -> None:
    fc = _forecast(
        tmp_path,
        {"AAA": {"qualified_fraction": 1.5}, "BBB": {"qualified_fraction": 0.0}},
        [Position("AAA", 100, 5_000.0, 100.0), Position("BBB", 100, 5_000.0, 100.0)],
    )
    assert fc.qualified_fraction == pytest.approx(0.5)
    assert fc.per_position["AAA"]["qualified"] == 1.0
    bd = qualified_breakdown(fc.per_position)
    assert bd.total_qualified_pct == pytest.approx(fc.qualified_fraction)


def test_f35_negative_override_clamps_to_zero(tmp_path) -> None:
    fc = _forecast(
        tmp_path,
        {"AAA": {"qualified_fraction": -0.5}, "BBB": {"qualified_fraction": 1.0}},
        [Position("AAA", 100, 5_000.0, 100.0), Position("BBB", 100, 5_000.0, 100.0)],
    )
    assert fc.qualified_fraction == pytest.approx(0.5)


# --- F-44 -----------------------------------------------------------------


def test_f44_sourced_dict_ior_raises() -> None:
    sd = SourcedDict({2024: 1.0}, {2024: _prov()})
    with pytest.raises(TypeError):
        sd |= {2025: 2.0}
    assert dict(sd) == {2024: 1.0}


def test_f44_sourced_list_sort_reverse_imul_raise() -> None:
    sl = SourcedList([3, 1, 2], [_prov(), _prov(Source.MANUAL), _prov()])
    with pytest.raises(TypeError):
        sl.sort()
    with pytest.raises(TypeError):
        sl.reverse()
    with pytest.raises(TypeError):
        sl *= 2
    assert list(sl) == [3, 1, 2]
    assert sorted(sl) == [1, 2, 3]  # non-mutating forms still work


# --- F-45 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [(10234.56, 10234.61), (123.4567, 123.4571), (1234567.0, 1234568.0)],
)
def test_f45_distinct_strikes_get_distinct_keys(a: float, b: float) -> None:
    ga, gb = StockGrant(2020, a, 100, 2030), StockGrant(2020, b, 200, 2030)
    assert ga.key() != gb.key()
    lots = aggregate_by_key([ga, gb])
    assert [lot.strike for lot in lots] == [a, b]


@pytest.mark.parametrize(
    ("strike", "expected"),
    [(104.12, "2020:104.12:2030"), (40.0, "2020:40:2030"), (169.0, "2020:169:2030")],
)
def test_f45_ordinary_strike_keys_stay_stable(strike: float, expected: str) -> None:
    assert StockGrant(2020, strike, 1, 2030).key() == expected


# --- F-46 -----------------------------------------------------------------


@pytest.mark.parametrize("bad", ["nan", "inf", float("nan")])
def test_f46_inherited_ira_rejects_non_finite_balance(bad) -> None:
    entry = {"balance": bad, "owner": "you", "inherited_year": 2024}
    assert InheritedIRA.from_dict(entry) is None


def test_f46_inherited_ira_rejects_bool_year() -> None:
    entry = {"balance": 1000.0, "owner": "you", "inherited_year": True}
    assert InheritedIRA.from_dict(entry) is None


def test_f46_schedule_rejects_nan_price() -> None:
    payload = json.loads('{"shares_by_grant_year": {}, "price_by_year": {"2027": NaN}}')
    with pytest.raises(ValueError, match="non-finite"):
        ExerciseSchedule.from_dict(payload)


def test_f46_manual_mark_rejects_nan() -> None:
    assert manual_mark_from_dict({"value": math.nan, "as_of": "2026-05-01"}) is None
    assert manual_mark_from_dict({"value": 5.0, "as_of": "2026-05-01"}) is not None


# --- F-47 -----------------------------------------------------------------


def _render_elig() -> None:
    from models.household import Household
    from views.roth_eligibility import render

    render(Household(your_age=55, spouse_age=54, base_year=2026, filing_status="MFJ"))


def test_f47_trad_contribution_inputs_reject_negatives() -> None:
    at = AppTest.from_function(_render_elig)
    at.run()
    assert not at.exception
    trad = [n for n in at.number_input if "Trad IRA contribution" in n.label]
    assert len(trad) == 2
    assert all(n.min == 0 for n in trad)
