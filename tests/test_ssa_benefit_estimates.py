"""Tests for SSA benefit-estimate fetch, matching, and cache (engine/portfolio_sync/social_security.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from engine.portfolio_sync import client as client_module
from engine.portfolio_sync.shapes import SSABenefitEstimate
from engine.portfolio_sync.social_security import fetch_ssa_benefit_estimates, fetch_ssa_snapshot


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
