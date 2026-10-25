"""Regression tests for audit-0809 #11 — corrupt committed-cache silent clobber.

engine/data_sources/orchestrator.py::resolve_for_app treats ANY
``load_committed() is None`` result as "no committed baseline exists yet" and
re-migrates a fresh baseline from the current session Household, which app.py
then unconditionally persists back over the saved baseline whenever
``AppResolveResult.committed_changed`` is true. ``load_committed`` must
therefore tell a missing baseline from a corrupt one. The practical
consequence otherwise: if the saved baseline is corrupt, the very next app
load silently replaces it with a freshly-migrated baseline built from whatever
is in the current session — permanently destroying the irreplaceable committed
provenance/values that were saved, with no backup and no warning surfaced to
the user.
"""

from __future__ import annotations

from datetime import datetime

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import CorruptCommittedCacheError, load_committed, save_committed
from engine.data_sources.orchestrator import resolve_for_app
from engine.store import SINGLE_KEY
from engine.store.namespaces import COMMITTED
from models.household import Household
from tests.conftest import CORRUPT_PAYLOAD

FIXED_DT = datetime(2026, 8, 22, 12, 0, 0)


def test_corrupt_committed_cache_is_not_silently_destroyed(store, corrupt_row) -> None:
    """audit-0809 #11: a corrupt saved baseline is silently replaced by a
    freshly-migrated baseline, destroying irreplaceable user data (committed
    provenance/values with no other source of truth) with no backup and no
    warning.
    """
    corrupt_row(COMMITTED)

    # Exercise the real app.py load -> resolve -> save sequence directly.
    session_hh = Household()
    cands = CandidateStore()
    choices = ChoiceMap()

    # Mirrors app.py's actual handling: a corrupt baseline makes
    # load_committed raise CorruptCommittedCacheError (audit-0809 #11 fix),
    # which the caller must catch and treat as "proceed with no baseline,
    # but suppress the save" rather than letting it propagate.
    try:
        committed_json = load_committed()
        corrupt = False
    except CorruptCommittedCacheError:
        committed_json = None
        corrupt = True
    app_res = resolve_for_app(
        session_hh, None, {}, cands, choices, committed_json, recorded_at=FIXED_DT
    )
    if app_res.committed_changed and not corrupt:
        save_committed(app_res.committed_json)

    saved = store.backend.get(COMMITTED, SINGLE_KEY).payload
    assert saved == CORRUPT_PAYLOAD, (
        "Corrupt saved baseline was silently clobbered by a freshly migrated "
        f"baseline instead of being preserved. Saved content after the sequence: {saved!r}"
    )


def test_load_committed_distinguishes_corrupt_from_missing(corrupt_row) -> None:
    """audit-0809 #11 root cause: ``load_committed`` must not return None for
    both, or callers cannot tell "nothing has ever been committed yet" apart
    from "something was committed and got corrupted".
    """
    assert load_committed() is None

    corrupt_row(COMMITTED)
    try:
        load_committed()
        raised = False
    except CorruptCommittedCacheError:
        raised = True

    assert raised, "load_committed() cannot distinguish a missing baseline from a corrupt one"


def test_save_committed_refuses_to_overwrite_existing_corrupt_row(store, corrupt_row) -> None:
    """audit-0809 #11 design follow-up: save_committed() must itself refuse to
    write over a saved baseline that fails to parse — this is the
    authoritative guard that protects every call site (Setup Confirm clicks
    included), not just app.py's migration path.
    """
    corrupt_row(COMMITTED)

    try:
        save_committed({"prior_year_magi": {"data": {"2024": 1}, "prov": {}}})
        raised = False
    except CorruptCommittedCacheError:
        raised = True

    assert raised, (
        "save_committed() must raise CorruptCommittedCacheError on an existing corrupt baseline"
    )
    saved = store.backend.get(COMMITTED, SINGLE_KEY).payload
    assert saved == CORRUPT_PAYLOAD, (
        "save_committed() must leave an existing corrupt row completely "
        f"unchanged when it refuses to write. Saved content: {saved!r}"
    )


def test_save_committed_writes_normally_when_nothing_saved(store) -> None:
    """No saved baseline means "no baseline to protect yet" — save_committed()
    must write normally, exactly as before this guard was added.
    """
    payload = {"prior_year_magi": {"data": {"2024": 42}, "prov": {}}}

    save_committed(payload)

    assert store.has(COMMITTED)
    assert load_committed() == payload


def test_save_committed_writes_normally_when_saved_baseline_valid(store) -> None:
    """An existing but parseable baseline must not trip the corrupt guard —
    save_committed() must overwrite it normally, as a legitimate re-save.
    """
    store.put(COMMITTED, {"prior_year_magi": {"data": {"2024": 1}, "prov": {}}})
    payload = {"prior_year_magi": {"data": {"2024": 99}, "prov": {}}}

    save_committed(payload)

    assert load_committed() == payload
