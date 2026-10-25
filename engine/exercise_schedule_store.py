"""ExerciseSchedule persistence in the document store (namespace ``exercise_schedule``)."""

from __future__ import annotations

from engine.store import CorruptStoreError, get_store
from engine.store.namespaces import EXERCISE_SCHEDULE
from models.exercise_schedule import _SCHEDULE_VERSION, ExerciseSchedule


def save_exercise_schedule(schedule: ExerciseSchedule) -> None:
    """Save *schedule* to the document store."""
    get_store().put(EXERCISE_SCHEDULE, schedule.to_dict())


def load_exercise_schedule() -> ExerciseSchedule | None:
    """The saved ExerciseSchedule, or ``None`` if unavailable.

    Returns ``None`` when nothing is saved, the row is unreadable, or it
    carries an unrecognized/missing ``version`` -- never raises. Callers rely
    on ``Household.effective_schedule()`` to fall back to ``default_at_expiry``
    in all of those cases.
    """
    try:
        data = get_store().get(EXERCISE_SCHEDULE)
    except CorruptStoreError:
        return None
    if not isinstance(data, dict) or data.get("version") != _SCHEDULE_VERSION:
        return None
    try:
        return ExerciseSchedule.from_dict(data)
    except (ValueError, TypeError, KeyError):
        return None


def clear_exercise_schedule() -> None:
    """Delete the saved schedule, if any ("Reset to default").

    After clearing, ``Household.effective_schedule()`` falls back to
    ``default_at_expiry``. In demo mode the null store drops the delete.
    """
    get_store().delete(EXERCISE_SCHEDULE)
