"""Guard: the exercise-schedule store has no path parameter to point at a real file."""

import inspect

import pytest

import engine.exercise_schedule_store as store_mod


@pytest.mark.parametrize(
    "name", ["save_exercise_schedule", "load_exercise_schedule", "clear_exercise_schedule"]
)
def test_exercise_schedule_functions_take_no_path(name):
    assert "path" not in inspect.signature(getattr(store_mod, name)).parameters
