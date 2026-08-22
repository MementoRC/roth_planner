"""Regression tests for audit-0809 #11 — corrupt committed-cache silent clobber.

engine/data_sources/orchestrator.py::resolve_for_app treats ANY
``load_committed(COMMITTED_PATH) is None`` result as "no committed baseline
exists yet" and re-migrates a fresh baseline from the current session
Household, which app.py then unconditionally persists back over
``.committed_household.json`` (app.py ~330-365) whenever
``AppResolveResult.committed_changed`` is true. ``load_committed`` returns
``None`` for BOTH a genuinely missing file (OSError) and a corrupt/truncated
one (json.JSONDecodeError) — a single ``except (OSError, json.JSONDecodeError,
...)`` clause in engine/data_sources/committed.py collapses both cases. The
practical consequence: if ``.committed_household.json`` is truncated mid-write
(e.g. the process is killed during ``save_committed``), the very next app load
silently replaces it with a freshly-migrated baseline built from whatever is
in the current session — permanently destroying the irreplaceable committed
provenance/values that were on disk, with no backup and no warning surfaced
to the user.
"""

from __future__ import annotations

from datetime import datetime

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import CorruptCommittedCacheError, load_committed, save_committed
from engine.data_sources.orchestrator import resolve_for_app
from models.household import Household

FIXED_DT = datetime(2026, 8, 22, 12, 0, 0)

# Syntactically invalid JSON, as would be left behind by a save_committed()
# call that was interrupted (process killed / disk full) partway through
# writing. Contains a sentinel value that must survive the app.py 330->365
# load -> resolve -> save sequence if the corrupt file is NOT clobbered.
_CORRUPT_COMMITTED_TEXT = (
    '{"prior_year_magi": {"data": {"2024": 987654}, '
    '"prov": {"2024": {"source": "SENTINEL_COMMITTED_MAGI", "recorded_at": "2026-0'
)


def test_corrupt_committed_cache_is_not_silently_destroyed() -> None:
    """audit-0809 #11: a corrupt .committed_household.json is silently
    replaced by a freshly-migrated baseline, destroying irreplaceable user
    data (committed provenance/values with no other source of truth) with
    no backup and no warning.
    """
    from engine.data_sources.paths import COMMITTED_PATH

    COMMITTED_PATH.write_text(_CORRUPT_COMMITTED_TEXT)

    # Exercise the real app.py :330->:365 sequence directly.
    session_hh = Household()
    store = CandidateStore()
    choices = ChoiceMap()

    # Mirrors app.py's actual :330->:365 handling: a corrupt file makes
    # load_committed raise CorruptCommittedCacheError (audit-0809 #11 fix),
    # which the caller must catch and treat as "proceed with no baseline,
    # but suppress the save" rather than letting it propagate.
    try:
        committed_json = load_committed(COMMITTED_PATH)
        corrupt = False
    except CorruptCommittedCacheError:
        committed_json = None
        corrupt = True
    app_res = resolve_for_app(session_hh, None, {}, store, choices, committed_json, recorded_at=FIXED_DT)
    if app_res.committed_changed and not corrupt:
        save_committed(COMMITTED_PATH, app_res.committed_json)

    on_disk = COMMITTED_PATH.read_text()
    assert "SENTINEL_COMMITTED_MAGI" in on_disk, (
        "Corrupt .committed_household.json was silently clobbered by a freshly "
        "migrated baseline instead of being preserved/backed up. On-disk content "
        f"after the sequence: {on_disk!r}"
    )


def test_load_committed_distinguishes_corrupt_from_missing(tmp_path) -> None:
    """audit-0809 #11 root cause: engine/data_sources/committed.py's
    ``load_committed`` catches OSError (genuinely missing/unreadable file)
    and json.JSONDecodeError (corrupt file) in the SAME except clause and
    returns None for both, so callers cannot tell "nothing has ever been
    committed yet" apart from "something was committed and got corrupted".
    """
    missing_path = tmp_path / "missing_committed.json"
    corrupt_path = tmp_path / "corrupt_committed.json"
    corrupt_path.write_text(_CORRUPT_COMMITTED_TEXT)

    def _outcome(path):
        try:
            return ("returned", load_committed(path))
        except Exception as exc:  # noqa: BLE001 - deliberately capturing any exception type
            return ("raised", type(exc))

    missing_outcome = _outcome(missing_path)
    corrupt_outcome = _outcome(corrupt_path)

    assert missing_outcome != corrupt_outcome, (
        "load_committed() cannot distinguish a missing cache file from a corrupt "
        f"one: missing -> {missing_outcome!r}, corrupt -> {corrupt_outcome!r}"
    )


def test_save_committed_refuses_to_overwrite_existing_corrupt_file(tmp_path) -> None:
    """audit-0809 #11 design follow-up: save_committed() must itself refuse to
    write over a target that exists but fails to parse — this is the
    authoritative guard that protects every call site (Setup Confirm clicks
    included), not just app.py's migration path.
    """
    path = tmp_path / "corrupt_committed.json"
    path.write_text(_CORRUPT_COMMITTED_TEXT)

    try:
        save_committed(path, {"prior_year_magi": {"data": {"2024": 1}, "prov": {}}})
        raised = False
    except CorruptCommittedCacheError:
        raised = True

    assert raised, "save_committed() must raise CorruptCommittedCacheError on an existing corrupt target"
    on_disk = path.read_text()
    assert on_disk == _CORRUPT_COMMITTED_TEXT, (
        "save_committed() must leave an existing corrupt file's bytes completely "
        f"unchanged when it refuses to write. On-disk content: {on_disk!r}"
    )


def test_save_committed_writes_normally_when_target_missing(tmp_path) -> None:
    """A missing target means "no baseline to protect yet" — save_committed()
    must write normally, exactly as before this guard was added.
    """
    path = tmp_path / "missing_committed.json"
    payload = {"prior_year_magi": {"data": {"2024": 42}, "prov": {}}}

    save_committed(path, payload)

    assert path.exists()
    assert load_committed(path) == payload


def test_save_committed_writes_normally_when_target_valid(tmp_path) -> None:
    """An existing but parseable target must not trip the corrupt-file guard —
    save_committed() must overwrite it normally, as a legitimate re-save.
    """
    path = tmp_path / "valid_committed.json"
    path.write_text('{"prior_year_magi": {"data": {"2024": 1}, "prov": {}}}')
    payload = {"prior_year_magi": {"data": {"2024": 99}, "prov": {}}}

    save_committed(path, payload)

    assert load_committed(path) == payload
