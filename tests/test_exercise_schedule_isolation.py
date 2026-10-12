"""Guard: exercise-schedule store defaults must be redirected to tmp by conftest."""

import inspect

import pytest

import engine.exercise_schedule_store as store_mod


@pytest.mark.parametrize(
    "name", ["save_exercise_schedule", "load_exercise_schedule", "clear_exercise_schedule"]
)
def test_exercise_schedule_default_path_is_redirected(name, tmp_path, clean_command_center_caches):
    default = inspect.signature(getattr(store_mod, name)).parameters["path"].default
    assert str(default).startswith(str(tmp_path))
