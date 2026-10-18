"""Tests for "Mark as confirmed": confirm a governed value without changing it."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime

import pytest

from engine.data_sources.committed import load_committed, migrate_committed, save_committed
from engine.data_sources.confirm import confirm_as_is
from engine.data_sources.seed_defaults import seed_default_fields
from models.grants import StockGrant
from models.household import Household
from models.sourced import Provenance, Source

_THEN = datetime(2026, 1, 1, 12, 0)
_NOW = datetime(2026, 10, 18, 9, 30)


def _prov(source: Source = Source.DEFAULT) -> dict:
    return Provenance(source, _THEN, "seed default").to_json()


def _committed() -> dict:
    grant = StockGrant(year=2021, strike=10.0, shares=100, expiry_year=2031)
    return {
        "your_ira": {**_prov(), "value": 1234.5},
        "prior_year_magi": {
            "data": {"2024": 100.0, "2025": 200.0},
            "prov": {"2024": _prov(), "2025": _prov()},
        },
        "grants": {
            "data": [dataclasses.asdict(grant), dataclasses.asdict(grant)],
            "prov": [_prov(), _prov()],
        },
    }


def _assert_manual_now(prov_json: dict) -> None:
    prov = Provenance.from_json(prov_json)
    assert prov.source == Source.MANUAL
    assert prov.recorded_at == _NOW
    assert prov.detail == "confirmed as-is"


def test_scalar_value_unchanged_provenance_manual() -> None:
    committed = _committed()
    out = confirm_as_is(committed, "your_ira", _NOW)
    assert out is committed
    assert out["your_ira"]["value"] == 1234.5
    _assert_manual_now(out["your_ira"])


def test_magi_only_target_year_restamped() -> None:
    out = confirm_as_is(_committed(), "prior_year_magi.2024", _NOW)
    assert out["prior_year_magi"]["data"] == {"2024": 100.0, "2025": 200.0}
    _assert_manual_now(out["prior_year_magi"]["prov"]["2024"])
    assert out["prior_year_magi"]["prov"]["2025"] == _prov()


def test_grants_every_item_restamped_data_unchanged() -> None:
    before = _committed()["grants"]["data"]
    out = confirm_as_is(_committed(), "grants", _NOW)
    assert out["grants"]["data"] == before
    assert len(out["grants"]["prov"]) == 2
    for p in out["grants"]["prov"]:
        _assert_manual_now(p)


@pytest.mark.parametrize("field_key", ["spouse_ira", "prior_year_magi.2030", "nonsense"])
def test_missing_or_unknown_field_raises(field_key: str) -> None:
    committed = _committed()
    with pytest.raises(ValueError, match="No committed value|Unknown"):
        confirm_as_is(committed, field_key, _NOW)
    assert committed == _committed()


def test_missing_grants_raises() -> None:
    with pytest.raises(ValueError, match="No committed value"):
        confirm_as_is({}, "grants", _NOW)


# --- view ---------------------------------------------------------------


def _seed_fresh_demo_baseline() -> None:
    from engine.data_sources.paths import COMMITTED_PATH

    hh = Household()
    committed = migrate_committed(hh, _THEN, default_fields=seed_default_fields(hh))
    save_committed(COMMITTED_PATH, committed)


def _badge_script() -> None:
    import views._shared as shared
    from engine.data_sources.committed import apply_committed, load_committed
    from engine.data_sources.paths import COMMITTED_PATH
    from models.household import Household

    hh = Household()
    committed = load_committed(COMMITTED_PATH)
    assert committed is not None
    apply_committed(hh, committed)
    shared.render_completeness_badge(hh)


def _pct(at) -> int:  # noqa: ANN001
    (caption,) = [c.value for c in at.caption if "Data completeness" in c.value]
    return int(caption.split("%")[0].rsplit(" ", 1)[1])


def _row(at, label: str) -> str:  # noqa: ANN001
    (expander,) = [e for e in at.expander if e.label == "Data sources"]
    (line,) = [ln for ln in expander.markdown[0].value.splitlines() if ln.startswith(f"| {label} ")]
    return line


def test_button_confirms_field_without_changing_value() -> None:
    from streamlit.testing.v1 import AppTest

    from engine.data_sources.paths import COMMITTED_PATH

    _seed_fresh_demo_baseline()
    value_before = json.loads(COMMITTED_PATH.read_text())["your_ira"]["value"]

    at = AppTest.from_function(_badge_script, default_timeout=60).run()
    assert not at.exception
    assert "unconfirmed | Seed default" in _row(at, "Your IRA balance")
    pct_before = _pct(at)

    (button,) = [b for b in at.button if b.key == "confirm_asis_your_ira"]
    assert button.label == "Mark Your IRA balance as confirmed"
    button.click().run()

    assert not at.exception
    assert "ok | Manual entry" in _row(at, "Your IRA balance")
    assert _pct(at) > pct_before
    assert not [b for b in at.button if b.key == "confirm_asis_your_ira"]

    persisted = load_committed(COMMITTED_PATH)
    assert persisted is not None
    assert persisted["your_ira"]["value"] == value_before
    assert Provenance.from_json(persisted["your_ira"]).source == Source.MANUAL
    assert persisted["your_ira"]["detail"] == "confirmed as-is"
