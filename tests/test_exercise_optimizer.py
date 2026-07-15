"""Tests for engine.exercise_optimizer: result dataclasses + engine-purity guard."""

from __future__ import annotations

from pathlib import Path

from engine.exercise_optimizer import OptimizedPlan, OptimizerResult
from engine.scenario_types import ConversionPlan
from models.exercise_schedule import ExerciseSchedule


def _make_schedule() -> ExerciseSchedule:
    sched = ExerciseSchedule()
    sched.set_shares("grant-1", 2030, 100)
    sched.set_price(2030, 150.0)
    return sched


def _make_plan() -> ConversionPlan:
    return ConversionPlan(your_conversions={2030: 50_000.0})


def test_optimized_plan_round_trips_fields() -> None:
    sched = _make_schedule()
    plan = _make_plan()

    optimized = OptimizedPlan(
        ceiling_label="top-of-22",
        schedule=sched,
        conversions=plan,
        lifetime_all_in=123.0,
        over_ceiling_years=[2030],
    )

    assert optimized.ceiling_label == "top-of-22"
    assert optimized.schedule is sched
    assert optimized.conversions is plan
    assert optimized.lifetime_all_in == 123.0
    assert optimized.over_ceiling_years == [2030]


def test_optimizer_result_round_trips_fields() -> None:
    sched = _make_schedule()
    plan = _make_plan()
    optimized = OptimizedPlan(
        ceiling_label="top-of-22",
        schedule=sched,
        conversions=plan,
        lifetime_all_in=123.0,
        over_ceiling_years=[2030],
    )

    result = OptimizerResult(best=optimized, candidates=[optimized], baseline_cost=200.0)

    assert result.best is optimized
    assert result.candidates == [optimized]
    assert result.baseline_cost == 200.0


def test_exercise_optimizer_has_no_streamlit_import() -> None:
    src = (Path(__file__).resolve().parent.parent / "engine/exercise_optimizer.py").read_text()
    assert "streamlit" not in src
