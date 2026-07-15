"""Pure-Python exercise auto-optimizer: solve for the ExerciseSchedule that
minimizes modeled lifetime all-in cost. NO Streamlit imports (engine purity)."""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.scenario_types import ConversionPlan
from models.exercise_schedule import ExerciseSchedule


@dataclass
class OptimizedPlan:
    ceiling_label: str
    schedule: ExerciseSchedule
    conversions: ConversionPlan
    lifetime_all_in: float
    over_ceiling_years: list[int] = field(default_factory=list)


@dataclass
class OptimizerResult:
    best: OptimizedPlan
    candidates: list[OptimizedPlan]
    baseline_cost: float
