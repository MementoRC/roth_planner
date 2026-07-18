"""Tests for the exercise-page TXN live-quote + growth-rate controls (P3).

Covers three pure/testable seams extracted out of
``views/option_exercise.py.render`` per the project's "no logic buried in
render()" convention:

- ``handle_txn_quote_fetch`` — fetch + record-as-pending-candidate + session
  stash, with an injectable fetcher so no network call happens in tests.
- ``models.household.project_price`` — the reusable compounding helper that
  lets the page project from an overridable base (a fetched-but-unconfirmed
  quote) instead of only ``hh.txn_price_now``.
- The ``txn_price_growth_rate`` user-defaults round trip (save -> load ->
  ``Household.txn_price_growth.default_rate``).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from config.loader import load_defaults, save_user_defaults
from engine.data_sources.candidate_store import CandidateStore
from engine.market_quote import QuoteResult
from models.household import GrowthProfile, Household, project_price
from models.sourced import Source
from views.option_exercise import handle_txn_quote_fetch

FIXED_DT = datetime(2026, 7, 16, 12, 0, 0)


def _ok_result(price: float = 210.5) -> QuoteResult:
    return QuoteResult(
        ticker="TXN",
        price=price,
        currency="USD",
        fetched_at=FIXED_DT,
        detail="Yahoo Finance TXN",
        error=None,
    )


def _error_result(error: str = "HTTP 503") -> QuoteResult:
    return QuoteResult(
        ticker="TXN",
        price=None,
        currency=None,
        fetched_at=None,
        detail="Yahoo Finance TXN",
        error=error,
    )


class TestHandleTxnQuoteFetchOk:
    """A successful fetch records a pending candidate AND stashes the price."""

    def test_records_pending_market_quote_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        result = handle_txn_quote_fetch(
            store_path=store_path, fetcher=lambda: _ok_result(210.5)
        )

        assert result.ok
        store = CandidateStore.load(store_path)
        candidates = store.candidates_for("txn_price_now")
        assert len(candidates) == 1
        assert candidates[0].value == 210.5
        assert candidates[0].prov.source == Source.MARKET_QUOTE

    def test_stashes_price_in_session_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        handle_txn_quote_fetch(store_path=store_path, fetcher=lambda: _ok_result(199.25))

        assert st.session_state["_txn_quote_price"] == 199.25

    def test_returns_the_quote_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        result = handle_txn_quote_fetch(store_path=store_path, fetcher=lambda: _ok_result(150.0))

        assert result.price == 150.0
        assert result.ticker == "TXN"


class TestHandleTxnQuoteFetchNotOk:
    """A failed fetch must not crash, not record a candidate, not stash a price."""

    def test_no_candidate_recorded_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        result = handle_txn_quote_fetch(
            store_path=store_path, fetcher=lambda: _error_result("timeout")
        )

        assert not result.ok
        assert result.error == "timeout"
        assert not store_path.exists()

    def test_no_session_stash_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        handle_txn_quote_fetch(store_path=store_path, fetcher=lambda: _error_result())

        assert "_txn_quote_price" not in st.session_state

    def test_does_not_raise(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        # A stub fetcher standing in for a network failure -- never raises,
        # matching fetch_txn_quote's own contract.
        result = handle_txn_quote_fetch(
            store_path=store_path, fetcher=lambda: _error_result("connection refused")
        )
        assert result.price is None


class TestProjectPriceReusable:
    """models.household.project_price -- the shared compounding helper."""

    def test_matches_household_projected_txn_price(self) -> None:
        hh = Household(base_year=2026, txn_price_now=200.0)
        for year in (2026, 2027, 2030):
            assert project_price(
                hh.txn_price_now, hh.base_year, hh.txn_price_growth, year
            ) == pytest.approx(hh.projected_txn_price(year))

    def test_overridable_base_diverges_from_committed_price(self) -> None:
        """The whole point of extracting this: project from an alternate base
        (e.g. a freshly fetched quote) without touching hh.txn_price_now."""
        hh = Household(base_year=2026, txn_price_now=100.0)
        committed_projection = project_price(
            hh.txn_price_now, hh.base_year, hh.txn_price_growth, 2028
        )
        quote_projection = project_price(250.0, hh.base_year, hh.txn_price_growth, 2028)

        assert quote_projection == pytest.approx(250.0 * 1.07**2)
        assert quote_projection != pytest.approx(committed_projection)

    def test_at_base_year_returns_base_unchanged(self) -> None:
        assert project_price(123.45, 2026, GrowthProfile(), 2026) == 123.45

    def test_honors_custom_growth_rate(self) -> None:
        grown = GrowthProfile(default_rate=0.10)
        assert project_price(100.0, 2026, grown, 2029) == pytest.approx(100.0 * 1.10**3)


class TestTxnPriceGrowthRatePersistence:
    """txn_price_growth_rate: save_user_defaults -> load_defaults -> Household
    round trip, so the edited rate feeds both the page and the plan
    (scenario/optimizer) via the same single-sourced default."""

    def test_saved_rate_round_trips_through_load_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)

        save_user_defaults({"txn_price_growth_rate": 9.0})
        defaults = load_defaults()

        assert defaults["txn_price_growth_rate"] == 9.0

    def test_loaded_rate_feeds_household_growth_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)

        save_user_defaults({"txn_price_growth_rate": 12.5})
        defaults = load_defaults()
        rate = float(defaults.get("txn_price_growth_rate", 7.0)) / 100

        hh = Household(txn_price_growth=GrowthProfile(default_rate=rate))

        assert hh.txn_price_growth.default_rate == pytest.approx(0.125)

    def test_absent_rate_falls_back_to_seven_percent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.setenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", "1")

        defaults = load_defaults()
        rate = float(defaults.get("txn_price_growth_rate", 7.0)) / 100

        assert rate == pytest.approx(0.07)
