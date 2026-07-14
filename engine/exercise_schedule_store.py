"""ExerciseSchedule persistence — ``.exercise_schedule_cache.json`` at project
root, via the hardened ``engine.secure_io`` PII read/write pattern (atomic,
``0o600``, ``O_NOFOLLOW``).

Mirrors ``engine/portfolio_sync/portfolio.py`` (``load_snapshot``/
``save_snapshot``) and ``engine/portfolio_sync/ytd.py``
(``load_ytd_snapshot``/``save_ytd_snapshot``) — the two *live* caches. Do not
imitate ``.tax_return_cache.json``; it is dead code.
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.secure_io import read_pii_json, write_pii_json
from models.exercise_schedule import _SCHEDULE_VERSION, ExerciseSchedule

_EXERCISE_SCHEDULE_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / ".exercise_schedule_cache.json"
)


def save_exercise_schedule(
    schedule: ExerciseSchedule, path: Path = _EXERCISE_SCHEDULE_CACHE_PATH
) -> None:
    """Save *schedule* to disk as JSON via the hardened PII write helper."""
    write_pii_json(path, schedule.to_dict())


def load_exercise_schedule(path: Path = _EXERCISE_SCHEDULE_CACHE_PATH) -> ExerciseSchedule | None:
    """Load cached ExerciseSchedule from disk, or ``None`` if unavailable.

    Returns ``None`` when the file is missing, contains malformed JSON, or
    carries an unrecognized/missing ``version`` — never raises. Callers rely
    on ``Household.effective_schedule()`` to fall back to
    ``default_from_legacy`` in all of those cases, so a bad/absent cache
    degrades gracefully to legacy behavior.
    """
    if not path.exists():
        return None
    try:
        data = read_pii_json(path)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != _SCHEDULE_VERSION:
        return None
    return ExerciseSchedule.from_dict(data)


def clear_exercise_schedule(path: Path = _EXERCISE_SCHEDULE_CACHE_PATH) -> None:
    """Delete the cached schedule file, if present.

    No-op (no error) when the file is already absent, so callers can invoke
    this unconditionally on a "Reset to default" action. After clearing,
    ``Household.effective_schedule()`` falls back to ``default_from_legacy``.
    """
    path.unlink(missing_ok=True)
