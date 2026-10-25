"""Regression tests for audit-1008 wave W11 — cache-store robustness.

F-38  config/loader.py: with ROTH_PLANNER_DEFAULTS set, autosave wrote to
      .user_defaults.json, which load_defaults never read back; and the two
      readers (load_defaults / load_raw_user_defaults) disagreed on an empty
      env file.
F-44  CandidateStore.save only re-ran json.loads on the target, so a file that
      parses as JSON but has the wrong shape (load_corrupt=True) was silently
      overwritten with an empty store.
F-45  ChoiceMap.save wrote non-atomically with no corrupt-target guard, and
      load turned a corrupt file into an empty map that the next save wrote back.
F-47  A malformed .dividend_rates.json crashed forecast_portfolio.
F-48  load_exercise_schedule raised AttributeError on a wrong-shape cache
      despite its "never raises" contract.

All figures are synthetic.
"""

from __future__ import annotations

import errno
import json
from datetime import datetime
from pathlib import Path

import pytest

import config.loader as loader
from config.defaults import DEFAULTS
from engine.data_sources.candidate_store import CandidateStore, CorruptCandidateStoreError
from engine.data_sources.choices import ChoiceMap, CorruptChoiceMapError, TrustChoice
from engine.dividend_forecast import Position, forecast_portfolio
from engine.exercise_schedule_store import load_exercise_schedule
from engine.store.namespaces import EXERCISE_SCHEDULE
from models.exercise_schedule import ExerciseSchedule
from models.sourced import Provenance, Source

FIXED_DT = datetime(2026, 1, 1, 12, 0, 0)


# ---------------------------------------------------------------- F-38 ----


@pytest.fixture
def defaults_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Local .user_defaults.json at tmp_path; implicit local files honoured."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(loader, "_USER_DEFAULTS_PATH", tmp_path / ".user_defaults.json")
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
    return tmp_path


class TestF38UserDefaultsWithEnvFile:
    def test_autosaved_edit_survives_restart_with_env_json(
        self, defaults_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = defaults_dir / "my_defaults.json"
        env_file.write_text(json.dumps({"living_expenses": 50_000}))
        monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(env_file))
        assert loader.load_defaults()["living_expenses"] == 50_000

        loader.save_user_defaults({"living_expenses": 70_000})  # UI edit autosave

        # "restart": both readers must see the edit
        assert loader.load_defaults()["living_expenses"] == 70_000
        assert loader.load_raw_user_defaults()["living_expenses"] == 70_000

    def test_save_into_env_json_preserves_unmanaged_keys(
        self, defaults_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = defaults_dir / "my_defaults.json"
        env_file.write_text(
            json.dumps({"living_expenses": 50_000, "grant_strikes": {"2030": 12.5}})
        )
        monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(env_file))

        loader.save_user_defaults({"living_expenses": 70_000})

        assert loader.load_defaults()["grant_strikes"] == {"2030": 12.5}

    def test_empty_env_file_readers_agree_and_save_targets_local(
        self, defaults_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = defaults_dir / "my_defaults.json"
        env_file.write_text("{}")
        monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(env_file))
        local = defaults_dir / ".user_defaults.json"
        local.write_text(json.dumps({"living_expenses": 70_000, "grant_strikes": {"2030": 12.5}}))

        assert loader.load_defaults()["living_expenses"] == 70_000
        assert loader.load_raw_user_defaults()["living_expenses"] == 70_000

        # The save goes to the file that was actually read (the local one), so
        # its unmanaged keys stay visible after the next load.
        loader.save_user_defaults({"living_expenses": 71_000})
        assert loader.load_defaults()["living_expenses"] == 71_000
        assert loader.load_defaults()["grant_strikes"] == {"2030": 12.5}

    @pytest.mark.parametrize(
        ("env_content", "local_content"),
        [
            (None, None),
            (None, {"living_expenses": 70_000}),
            ({"living_expenses": 50_000}, None),
            ({"living_expenses": 50_000}, {"living_expenses": 70_000}),
            ({}, {"living_expenses": 70_000}),
            ({}, {}),
            (None, {}),
        ],
    )
    def test_readers_never_disagree(
        self,
        defaults_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        env_content: dict | None,
        local_content: dict | None,
    ) -> None:
        if env_content is not None:
            env_file = defaults_dir / "my_defaults.json"
            env_file.write_text(json.dumps(env_content))
            monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(env_file))
        if local_content is not None:
            (defaults_dir / ".user_defaults.json").write_text(json.dumps(local_content))

        assert loader.load_defaults() == {**DEFAULTS, **loader.load_raw_user_defaults()}


# ---------------------------------------------------------------- F-44 ----


def _seed_good_candidates(store: CandidateStore) -> None:
    store.record_candidate("your_ira", 100_000.0, Provenance(Source.PDF, FIXED_DT))
    store.record_candidate("your_ss_fra", 2_222.0, Provenance(Source.PDF, FIXED_DT))


@pytest.mark.parametrize(
    "bad_entry",
    [
        # a Source value this build does not know
        {"future_source": {"value": 42.0, "prov": {"source": "future_source", "recorded_at": "x"}}},
        # stale StockGrant fields from another build
        {"PDF": {"value": {"__stock_grants__": True, "items": [{"bogus_old_field": 1}]}}},
        # a bucket that is not an object
        [1, 2, 3],
    ],
)
def test_f44_wrong_shape_store_is_not_clobbered(tmp_path: Path, bad_entry: object) -> None:
    path = tmp_path / "candidates.json"
    good = CandidateStore()
    _seed_good_candidates(good)
    payload = good.to_json()
    payload["stock_price_now"] = bad_entry
    original = json.dumps(payload)
    path.write_text(original)

    loaded = CandidateStore.load(path)
    assert loaded.load_corrupt is True

    loaded.record_candidate("spouse_ira", 5.0, Provenance(Source.MANUAL, FIXED_DT))
    with pytest.raises(CorruptCandidateStoreError):
        loaded.save(path)
    assert path.read_text() == original


def test_f44_top_level_non_object_loads_as_corrupt_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "candidates.json"
    path.write_text("[1, 2, 3]")

    loaded = CandidateStore.load(path)

    assert loaded.load_corrupt is True
    with pytest.raises(CorruptCandidateStoreError):
        loaded.save(path)
    assert path.read_text() == "[1, 2, 3]"


# ---------------------------------------------------------------- F-45 ----


def _five_choices() -> ChoiceMap:
    cm = ChoiceMap()
    for key, src in [
        ("your_ira", Source.PDF),
        ("spouse_ira", Source.PDF),
        ("your_roth", Source.MANUAL),
        ("spouse_roth", Source.MANUAL),
        ("stock_price_now", Source.MANUAL),
    ]:
        cm.set_choice(key, src, FIXED_DT)
    return cm


class TestF45ChoiceMapDurability:
    def test_interrupted_save_leaves_previous_file_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / ".trust_choices.json"
        _five_choices().save(path)
        before = path.read_text()

        real_write_text = Path.write_text

        def _enospc_after_half(self: Path, data: str, *a: object, **kw: object) -> int:
            real_write_text(self, data[: len(data) // 2], *a, **kw)  # type: ignore[arg-type]
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(Path, "write_text", _enospc_after_half)
        updated = _five_choices()
        updated.clear("your_ira")
        with pytest.raises(OSError, match="No space left"):
            updated.save(path)
        monkeypatch.setattr(Path, "write_text", real_write_text)

        assert path.read_text() == before
        assert ChoiceMap.load(path).get("your_ira") == TrustChoice(Source.PDF, FIXED_DT)
        assert sorted(tmp_path.glob(f"{path.name}.tmp-*")) == []

    def test_save_refuses_to_overwrite_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / ".trust_choices.json"
        corrupt = '{"your_ira": {"source": "PDF", "locked_a'
        path.write_text(corrupt)

        loaded = ChoiceMap.load(path)
        assert loaded.load_corrupt is True
        assert loaded.get("your_ira") is None

        with pytest.raises(CorruptChoiceMapError):
            loaded.save(path)
        assert path.read_text() == corrupt

    def test_save_refuses_to_overwrite_wrong_shape_file(self, tmp_path: Path) -> None:
        path = tmp_path / ".trust_choices.json"
        wrong_shape = json.dumps({"your_ira": {"source": "NOT_A_SOURCE", "locked_at": "x"}})
        path.write_text(wrong_shape)

        assert ChoiceMap.load(path).load_corrupt is True
        with pytest.raises(CorruptChoiceMapError):
            ChoiceMap().save(path)
        assert path.read_text() == wrong_shape

    def test_missing_file_is_not_corrupt(self, tmp_path: Path) -> None:
        path = tmp_path / ".trust_choices.json"
        loaded = ChoiceMap.load(path)
        assert loaded.load_corrupt is False
        loaded.save(path)
        assert json.loads(path.read_text()) == {}


# ---------------------------------------------------------------- F-47 ----


def _vti() -> Position:
    return Position(ticker="VTI", shares=100.0, balance=25_000.0, ttm_dividends=350.0)


@pytest.mark.parametrize(
    "overrides_text",
    [
        json.dumps([{"VTI": {"annual_rate": 3.5}}]),  # top-level list
        json.dumps({"VTI": 3.5}),  # scalar per-ticker value
        json.dumps({"VTI": {"annual_rate": "n/a"}}),  # non-numeric rate
        json.dumps({"VTI": {"qualified_fraction": "n/a"}}),  # non-numeric fraction
        json.dumps({"VTI": "n/a"}),  # string per-ticker value
        json.dumps("just a string"),  # top-level scalar
    ],
)
def test_f47_malformed_overrides_fall_back_to_ttm(tmp_path: Path, overrides_text: str) -> None:
    ov_path = tmp_path / ".dividend_rates.json"
    ov_path.write_text(overrides_text)
    baseline = forecast_portfolio([_vti()], 25_000.0, overrides_path=tmp_path / "absent.json")

    result = forecast_portfolio([_vti()], 25_000.0, overrides_path=ov_path)

    assert result.yield_rate == pytest.approx(baseline.yield_rate)
    assert result.qualified_fraction == pytest.approx(baseline.qualified_fraction)


def test_f47_valid_entries_survive_next_to_a_bad_one(tmp_path: Path) -> None:
    ov_path = tmp_path / ".dividend_rates.json"
    ov_path.write_text(
        json.dumps({"VTI": {"annual_rate": 7.0, "qualified_fraction": 0.5}, "BND": "garbage"})
    )

    result = forecast_portfolio([_vti()], 25_000.0, overrides_path=ov_path)

    assert result.yield_rate == pytest.approx(700.0 / 25_000.0)
    assert result.qualified_fraction == pytest.approx(0.5)


# ---------------------------------------------------------------- F-48 ----


@pytest.mark.parametrize(
    "payload",
    [
        {"shares_by_grant_year": {"g1": [100]}},
        {"shares_by_grant_year": []},
        {"shares_by_grant_year": None},
        {"price_by_year": [1.0]},
        {"price_by_year": None},
    ],
)
def test_f48_wrong_shape_schedule_cache_returns_none(store, payload: dict) -> None:
    store.put(EXERCISE_SCHEDULE, {"version": ExerciseSchedule().to_dict()["version"], **payload})

    assert load_exercise_schedule() is None


def test_f48_from_dict_rejects_wrong_shape_with_value_error() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        ExerciseSchedule.from_dict({"shares_by_grant_year": {"g1": [100]}})
