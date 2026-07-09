"""Tests for SSA benefit-estimate fetch, matching, and cache (engine/portfolio_sync/social_security.py)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from engine.portfolio_sync import client as client_module
from engine.portfolio_sync import social_security
from engine.portfolio_sync.shapes import SSABenefitEstimate, SSASnapshot
from engine.portfolio_sync.social_security import (
    fetch_ssa_benefit_estimates,
    fetch_ssa_snapshot,
    load_ssa_snapshot,
    match_fra_estimate,
    save_ssa_snapshot,
)


def _fake_response(json_data, status_code=200):
    return SimpleNamespace(
        status_code=status_code,
        headers={},
        json=lambda: json_data,
        raise_for_status=lambda: None,
    )


class TestFetchSsaBenefitEstimates:
    def test_flattens_single_institution_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {"retirement_age": 62, "claim_date": "2027-01", "benefit_type": "early", "monthly_amount": 1800.0},
            {"retirement_age": 67, "claim_date": "2032-01", "benefit_type": "full", "monthly_amount": 2600.0},
        ]
        monkeypatch.setattr(
            client_module.requests,
            "get",
            lambda *a, **kw: _fake_response({"rows": rows}),
        )
        result = fetch_ssa_benefit_estimates()
        assert result == rows


class TestFetchSsaSnapshot:
    def test_parses_rows_into_estimates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.portfolio_sync.social_security as ssa_module

        monkeypatch.setattr(
            ssa_module,
            "fetch_ssa_benefit_estimates",
            lambda: [
                {"retirement_age": 67, "claim_date": "2032-01", "benefit_type": "full", "monthly_amount": 2600.0},
            ],
        )
        snap = fetch_ssa_snapshot()
        assert snap.server_available is True
        assert snap.error is None
        assert snap.estimates == [
            SSABenefitEstimate(retirement_age=67, claim_date="2032-01", benefit_type="full", monthly_amount=2600.0)
        ]

    def test_sets_error_on_request_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.portfolio_sync.social_security as ssa_module

        def _raise():
            raise requests.RequestException("connection refused")

        monkeypatch.setattr(ssa_module, "fetch_ssa_benefit_estimates", _raise)
        snap = fetch_ssa_snapshot()
        assert snap.server_available is False
        assert snap.error == "connection refused"
        assert snap.estimates == []

    def test_skips_malformed_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.portfolio_sync.social_security as ssa_module

        monkeypatch.setattr(
            ssa_module,
            "fetch_ssa_benefit_estimates",
            lambda: [{"retirement_age": "not-a-number", "monthly_amount": 2600.0}],
        )
        snap = fetch_ssa_snapshot()
        assert snap.server_available is True
        assert snap.estimates == []


class TestMatchFraEstimate:
    def test_exact_match(self) -> None:
        estimates = [
            SSABenefitEstimate(62, "2027-01", "early", 1800.0),
            SSABenefitEstimate(67, "2032-01", "full", 2600.0),
            SSABenefitEstimate(70, "2035-01", "delayed", 3200.0),
        ]
        assert match_fra_estimate(estimates, 67) == estimates[1]

    def test_nearest_fallback_when_no_exact_match(self) -> None:
        estimates = [
            SSABenefitEstimate(62, "2027-01", "early", 1800.0),
            SSABenefitEstimate(70, "2035-01", "delayed", 3200.0),
        ]
        assert match_fra_estimate(estimates, 68) == estimates[1]  # |68-62|=6, |68-70|=2

    def test_empty_list_returns_none(self) -> None:
        assert match_fra_estimate([], 67) is None


class TestSsaCache:
    def test_round_trips_per_owner(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(social_security, "_SSA_CACHE_PATH", tmp_path / "ssa.json")
        you_snap = SSASnapshot(
            estimates=[SSABenefitEstimate(67, "2032-01", "full", 2600.0)],
            server_available=True,
        )
        spouse_snap = SSASnapshot(
            estimates=[SSABenefitEstimate(67, "2033-01", "full", 1900.0)],
            server_available=True,
        )
        save_ssa_snapshot(you_snap, owner="you")
        save_ssa_snapshot(spouse_snap, owner="spouse")

        loaded_you = load_ssa_snapshot(owner="you")
        loaded_spouse = load_ssa_snapshot(owner="spouse")
        assert loaded_you == you_snap
        assert loaded_spouse == spouse_snap

    def test_load_missing_file_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(social_security, "_SSA_CACHE_PATH", tmp_path / "missing.json")
        assert load_ssa_snapshot(owner="you") is None

    def test_load_missing_owner_key_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(social_security, "_SSA_CACHE_PATH", tmp_path / "ssa.json")
        save_ssa_snapshot(SSASnapshot(server_available=True), owner="you")
        assert load_ssa_snapshot(owner="spouse") is None

    def test_load_corrupt_file_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "ssa.json"
        path.write_text("{not valid json")
        monkeypatch.setattr(social_security, "_SSA_CACHE_PATH", path)
        assert load_ssa_snapshot(owner="you") is None
