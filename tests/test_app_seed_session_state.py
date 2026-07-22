"""Behavioral tests for app.py's ``_seed_session_state``.

app.py is a Streamlit script with heavy import-time side effects (sidebar
page routing, disk-cache hydration) so it cannot be imported directly in
tests without triggering all of that. Instead we extract just the
``_seed_session_state`` function's source text from app.py and ``exec`` it
into an isolated namespace backed by a small dict/attribute session_state
harness — the same "patch the symbol at the module binding it's imported
into" pattern used by the MagicMock session_state harnesses in
tests/test_views_ytd_income.py, adapted here because app.py has no module
object we can safely import.

Audit 2026-07-13: _seed_session_state's ``session_keys`` map covered only
~10 of the 27 persisted scalar fields; filing_status and growth_rate (among
15 others) were unconditionally hardcoded to MFJ / 7.0, discarding the
user's persisted values on every fresh session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.irmaa import BASE_PART_B
from engine.upload_merge import SCALAR_KEYS

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


class _FakeSessionState:
    """Minimal dict + attribute stand-in for st.session_state."""

    def __init__(self) -> None:
        object.__setattr__(self, "_d", {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)

    def setdefault(self, key: str, default: Any = None) -> Any:
        return self._d.setdefault(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._d

    def __getitem__(self, key: str) -> Any:
        return self._d[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._d[key] = value

    def __getattr__(self, key: str) -> Any:
        try:
            return self._d[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key: str, value: Any) -> None:
        self._d[key] = value


class _FakeSt:
    def __init__(self) -> None:
        self.session_state = _FakeSessionState()


def _run_seed_session_state(defaults: dict) -> _FakeSessionState:
    """Extract and execute app.py's _seed_session_state against *defaults*.

    Returns the resulting fake session_state for assertions.
    """
    text = APP_PATH.read_text()
    start = text.index("def _seed_session_state()")
    end = text.index("\n\n\n# Shared state: household parameters")
    source = text[start:end]
    fake_st = _FakeSt()
    namespace: dict[str, Any] = {
        "st": fake_st,
        "load_defaults": lambda: defaults,
        "BASE_PART_B": BASE_PART_B,
        "SCALAR_KEYS": SCALAR_KEYS,
    }
    exec(compile(source, "<_seed_session_state>", "exec"), namespace)
    namespace["_seed_session_state"]()
    return fake_st.session_state


class TestSeedSessionStateRestoresPersistedScalars:
    """audit 2026-07-13: seeding must restore ALL persisted scalars, not ~10."""

    def test_persisted_filing_status_and_growth_rate_are_restored(self) -> None:
        persisted = {"filing_status": "Single", "growth_rate": 5.5}
        state = _run_seed_session_state(persisted)
        assert state.get("filing_status") == "Single"
        assert state.get("growth_rate") == 5.5

    def test_persisted_cpi_assumption_is_also_restored(self) -> None:
        """A third previously-missing scalar, to guard against a narrow fix."""
        persisted = {"cpi_assumption": 0.031}
        state = _run_seed_session_state(persisted)
        assert state.get("cpi_assumption") == 0.031

    def test_all_scalar_keys_are_restored_from_persisted_defaults(self) -> None:
        """Every canonical SCALAR_KEYS entry must round-trip through seeding."""
        persisted = {k: f"__persisted_{k}__" for k in SCALAR_KEYS}
        state = _run_seed_session_state(persisted)
        for k in SCALAR_KEYS:
            sess_key = "txn_price" if k == "stock_price_now" else k
            assert state.get(sess_key) == f"__persisted_{k}__", f"{k} was not seeded from persisted defaults"


class TestSeedSessionStateFirstRunDefaults:
    """No persisted value present → sensible hardcoded defaults still apply."""

    def test_first_run_scalars_fall_back_to_hardcoded_defaults(self) -> None:
        state = _run_seed_session_state({})
        assert state.get("filing_status") == "MFJ"
        assert state.get("growth_rate") == 7.0
        assert state.get("cpi_assumption") == 0.025
        assert state.get("your_aca") is False
        assert state.get("spouse_aca") is False
        assert state.get("your_ss_start_age") == 70
        assert state.get("spouse_rmd_start_age") == 75


class TestSeedSessionStateRestoresComplexPersistedKeys:
    """audit 2026-07-22: the three complex (non-scalar) persisted keys —
    survivor, inherited_iras, account_type_overrides — are NOT in SCALAR_KEYS,
    so they must be seeded explicitly from persisted defaults. Pre-fix they were
    hardcoded (survivor=None, inherited_iras=[], account_type_overrides
    unseeded), silently dropping a saved survivor scenario, inherited IRAs, and
    manual account-type overrides on every fresh session / server restart."""

    def test_persisted_survivor_is_restored(self) -> None:
        persisted = {"survivor": {"who_dies": "you", "death_year": 2035}}
        state = _run_seed_session_state(persisted)
        assert state.get("survivor") == {"who_dies": "you", "death_year": 2035}

    def test_persisted_inherited_iras_are_restored(self) -> None:
        iras = [{"balance": 500_000.0, "inherited_year": 2024, "owner": "you"}]
        state = _run_seed_session_state({"inherited_iras": iras})
        assert state.get("inherited_iras") == iras

    def test_persisted_account_type_overrides_are_restored(self) -> None:
        overrides = {"Z1234": {"type": "trad_ira", "owner": "spouse"}}
        state = _run_seed_session_state({"account_type_overrides": overrides})
        assert state.get("account_type_overrides") == overrides


class TestSeedSessionStateComplexKeysFirstRun:
    """No persisted value present → empty demo defaults preserved
    (survivor None, inherited_iras [], account_type_overrides {})."""

    def test_first_run_complex_keys_default_empty(self) -> None:
        state = _run_seed_session_state({})
        assert state.get("survivor") is None
        assert state.get("inherited_iras") == []
        assert state.get("account_type_overrides") == {}
