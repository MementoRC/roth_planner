"""Tests for the exercise-page ACME live-quote + growth-rate controls (P3).

Covers three pure/testable seams extracted out of
``views/option_exercise.py.render`` per the project's "no logic buried in
render()" convention:

- ``handle_stock_quote_fetch`` — fetch + record-as-pending-candidate only (no
  session stash), with an injectable fetcher so no network call happens in tests.
- ``models.household.project_price`` — the reusable compounding helper that
  projects from an overridable base price.
- The ``stock_price_growth_rate`` user-defaults round trip (save -> load ->
  ``Household.stock_price_growth.default_rate``).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from config.loader import load_defaults, save_user_defaults
from engine.data_sources.candidate_store import CandidateStore
from engine.exercise_schedule_store import load_exercise_schedule
from engine.market_quote import QuoteResult
from models.household import GrowthProfile, Household, project_price
from models.sourced import Source
from views.option_exercise import handle_stock_quote_fetch

FIXED_DT = datetime(2026, 7, 16, 12, 0, 0)


def _ok_result(price: float = 210.5) -> QuoteResult:
    return QuoteResult(
        ticker="ACME",
        price=price,
        currency="USD",
        fetched_at=FIXED_DT,
        detail="Yahoo Finance ACME",
        error=None,
    )


def _error_result(error: str = "HTTP 503") -> QuoteResult:
    return QuoteResult(
        ticker="ACME",
        price=None,
        currency=None,
        fetched_at=None,
        detail="Yahoo Finance ACME",
        error=error,
    )


class TestHandleStockQuoteFetchOk:
    """A successful fetch records a pending candidate and nothing else."""

    def test_records_pending_market_quote_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        result = handle_stock_quote_fetch(
            store_path=store_path, fetcher=lambda _t: _ok_result(210.5)
        )

        assert result.ok
        store = CandidateStore.load(store_path)
        candidates = store.candidates_for("stock_price_now")
        assert len(candidates) == 1
        assert candidates[0].value == 210.5
        assert candidates[0].prov.source == Source.MARKET_QUOTE

    def test_does_not_stash_price_in_session_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        handle_stock_quote_fetch(store_path=store_path, fetcher=lambda _t: _ok_result(199.25))

        assert "_stock_quote_price" not in st.session_state

    def test_returns_the_quote_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        result = handle_stock_quote_fetch(
            store_path=store_path, fetcher=lambda _t: _ok_result(150.0)
        )

        assert result.price == 150.0
        assert result.ticker == "ACME"


class TestHandleStockQuoteFetchNotOk:
    """A failed fetch must not crash, not record a candidate, not stash a price."""

    def test_no_candidate_recorded_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        result = handle_stock_quote_fetch(
            store_path=store_path, fetcher=lambda _t: _error_result("timeout")
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

        handle_stock_quote_fetch(store_path=store_path, fetcher=lambda _t: _error_result())

        assert "_stock_quote_price" not in st.session_state

    def test_does_not_raise(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import streamlit as st

        monkeypatch.setattr(st, "session_state", {})
        store_path = tmp_path / "candidate_store.json"

        # A stub fetcher standing in for a network failure -- never raises,
        # matching fetch_stock_quote's own contract.
        result = handle_stock_quote_fetch(
            store_path=store_path, fetcher=lambda _t: _error_result("connection refused")
        )
        assert result.price is None


class TestProjectPriceReusable:
    """models.household.project_price -- the shared compounding helper."""

    def test_matches_household_projected_stock_price(self) -> None:
        hh = Household(base_year=2026, stock_price_now=200.0)
        for year in (2026, 2027, 2030):
            assert project_price(
                hh.stock_price_now, hh.base_year, hh.stock_price_growth, year
            ) == pytest.approx(hh.projected_stock_price(year))

    def test_overridable_base_diverges_from_committed_price(self) -> None:
        """The whole point of extracting this: project from an alternate base
        (e.g. a freshly fetched quote) without touching hh.stock_price_now."""
        hh = Household(base_year=2026, stock_price_now=100.0)
        committed_projection = project_price(
            hh.stock_price_now, hh.base_year, hh.stock_price_growth, 2028
        )
        quote_projection = project_price(250.0, hh.base_year, hh.stock_price_growth, 2028)

        assert quote_projection == pytest.approx(250.0 * 1.07**2)
        assert quote_projection != pytest.approx(committed_projection)

    def test_at_base_year_returns_base_unchanged(self) -> None:
        assert project_price(123.45, 2026, GrowthProfile(), 2026) == 123.45

    def test_honors_custom_growth_rate(self) -> None:
        grown = GrowthProfile(default_rate=0.10)
        assert project_price(100.0, 2026, grown, 2029) == pytest.approx(100.0 * 1.10**3)


class TestStockPriceGrowthRatePersistence:
    """stock_price_growth_rate: save_user_defaults -> load_defaults -> Household
    round trip, so the edited rate feeds both the page and the plan
    (scenario/optimizer) via the same single-sourced default."""

    def test_saved_rate_round_trips_through_load_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)

        save_user_defaults({"stock_price_growth_rate": 9.0})
        defaults = load_defaults()

        assert defaults["stock_price_growth_rate"] == 9.0

    def test_loaded_rate_feeds_household_growth_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)

        save_user_defaults({"stock_price_growth_rate": 12.5})
        defaults = load_defaults()
        rate = float(defaults.get("stock_price_growth_rate", 7.0)) / 100

        hh = Household(stock_price_growth=GrowthProfile(default_rate=rate))

        assert hh.stock_price_growth.default_rate == pytest.approx(0.125)

    def test_absent_rate_falls_back_to_seven_percent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.setenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", "1")

        defaults = load_defaults()
        rate = float(defaults.get("stock_price_growth_rate", 7.0)) / 100

        assert rate == pytest.approx(0.07)


def _render_household_with_one_grant() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.option_exercise import render

    hh = Household(
        your_age=60,
        spouse_age=54,
        base_year=2026,
        stock_price_now=192.0,
        grants=[StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2028, grant_id="g1")],
    )
    render(hh)


def _render_household_with_explicit_price_override() -> None:
    from models.exercise_schedule import ExerciseSchedule
    from models.grants import StockGrant
    from models.household import Household
    from views.option_exercise import render

    hh = Household(
        your_age=60,
        spouse_age=54,
        base_year=2026,
        stock_price_now=192.0,
        grants=[StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2028, grant_id="g1")],
    )
    # A real saved schedule (non-empty shares) that ALSO carries an explicit
    # price override for the base year -- must survive a later live fetch.
    hh.exercise_schedule = ExerciseSchedule(
        shares_by_grant_year={"g1": {2028: 300}},
        price_by_year={2026: 500.0},
    )
    render(hh)


def _stub_fetch_284(ticker: str = "ACME") -> QuoteResult:
    return QuoteResult(
        ticker="ACME",
        price=284.02,
        currency="USD",
        fetched_at=FIXED_DT,
        detail="stub Yahoo Finance ACME",
        error=None,
    )


class TestExercisePagePriceCellsUseConfirmedPrice:
    """Drives the real page through AppTest: a fetched quote is only a pending
    candidate, so the price cells keep projecting from the confirmed
    ``hh.stock_price_now`` and explicit saved overrides are untouched."""

    def test_price_cells_stay_on_confirmed_price_after_fetch_click(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import views.option_exercise as oe_module

        # Stand in for the network fetcher + candidate store path, but exercise
        # the REAL handle_stock_quote_fetch (session-state stash + candidate
        # record) so this is a true end-to-end reproduction of the bug.
        store_path = tmp_path / "candidate_store.json"
        original_handler = oe_module.handle_stock_quote_fetch

        def _patched_handler() -> QuoteResult:
            return original_handler(store_path=store_path, fetcher=_stub_fetch_284)

        monkeypatch.setattr(oe_module, "handle_stock_quote_fetch", _patched_handler)

        at = AppTest.from_function(_render_household_with_one_grant)
        at.run()
        assert not at.exception

        base_before = at.number_input(key="oe_price_2026").value
        assert base_before == pytest.approx(192.0)

        next(b for b in at.button if b.label.endswith("quote (Yahoo)")).click()
        at.run()
        assert not at.exception

        base_after = at.number_input(key="oe_price_2026").value
        future_after = at.number_input(key="oe_price_2028").value

        # The fetch is only a pending candidate; the page keeps the confirmed price.
        assert base_after == pytest.approx(192.0)
        assert future_after == pytest.approx(192.0 * 1.07**2)

    def test_explicit_saved_override_survives_a_fresh_fetch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A year the user explicitly saved a price for must NOT be clobbered
        by a subsequent live-quote fetch."""
        import views.option_exercise as oe_module

        store_path = tmp_path / "candidate_store.json"
        original_handler = oe_module.handle_stock_quote_fetch

        def _patched_handler() -> QuoteResult:
            return original_handler(store_path=store_path, fetcher=_stub_fetch_284)

        monkeypatch.setattr(oe_module, "handle_stock_quote_fetch", _patched_handler)

        at = AppTest.from_function(_render_household_with_explicit_price_override)
        at.run()
        assert not at.exception

        assert at.number_input(key="oe_price_2026").value == pytest.approx(500.0)

        next(b for b in at.button if b.label.endswith("quote (Yahoo)")).click()
        at.run()
        assert not at.exception

        # explicit override at 2026 untouched by the fetch ...
        assert at.number_input(key="oe_price_2026").value == pytest.approx(500.0)
        # ... and an assumed (non-overridden) year keeps the committed basis.
        assert at.number_input(key="oe_price_2028").value == pytest.approx(192.0 * 1.07**2)


def _render_household_loading_schedule() -> None:
    # NOTE: AppTest.from_function execs only this function's own source, so
    # the module-level ``Path`` import is unavailable at annotation-eval time
    # -- deliberately unannotated (private test helper) for that reason.
    from engine.exercise_schedule_store import load_exercise_schedule
    from models.grants import StockGrant
    from models.household import Household
    from views.option_exercise import render

    hh = Household(
        your_age=60,
        spouse_age=54,
        base_year=2026,
        stock_price_now=192.0,
        grants=[StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2028, grant_id="g1")],
    )
    hh.exercise_schedule = load_exercise_schedule()
    render(hh)


class TestSaveOnlyPersistsDivergentPrices:
    """ "Save schedule" must only freeze a year's price as an explicit override
    when the widget value diverges from the projected assumption -- untouched
    "assumed" cells must not be persisted (the root cause of the "stuck at old
    price" bug: a stale flat price shadowing a later live-quote fetch)."""

    def test_all_cells_at_projected_default_persist_no_overrides(self) -> None:
        at = AppTest.from_function(_render_household_with_one_grant)
        at.run()
        assert not at.exception

        next(b for b in at.button if b.label == "Save schedule").click()
        at.run()
        assert not at.exception

        saved = load_exercise_schedule()
        assert saved is not None
        assert saved.price_by_year == {}
        assert 2026 not in saved.price_by_year
        assert 2028 not in saved.price_by_year

    def test_one_manually_edited_cell_is_persisted_alone(self) -> None:
        at = AppTest.from_function(_render_household_with_one_grant)
        at.run()
        assert not at.exception

        at.number_input(key="oe_price_2026").set_value(500.0)
        at.run()
        assert not at.exception

        next(b for b in at.button if b.label == "Save schedule").click()
        at.run()
        assert not at.exception

        saved = load_exercise_schedule()
        assert saved is not None
        assert saved.price_by_year == {2026: pytest.approx(500.0)}
        assert 2028 not in saved.price_by_year

    def test_saved_untouched_cells_reproject_on_a_later_fetch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for the exact reported bug: a prior save of untouched
        cells must not leave behind stale price_by_year entries that shadow a
        subsequent live-quote fetch."""
        import views.option_exercise as oe_module

        at = AppTest.from_function(_render_household_with_one_grant)
        at.run()
        next(b for b in at.button if b.label == "Save schedule").click()
        at.run()
        assert not at.exception

        # A fresh "page load" that re-reads the just-saved schedule from disk
        # (mirrors app.py's ``hh.exercise_schedule = load_exercise_schedule()``).
        store_path = tmp_path / "candidate_store.json"

        def _patched_handler() -> QuoteResult:
            return handle_stock_quote_fetch(store_path=store_path, fetcher=_stub_fetch_284)

        monkeypatch.setattr(oe_module, "handle_stock_quote_fetch", _patched_handler)

        at2 = AppTest.from_function(_render_household_loading_schedule)
        at2.run()
        assert not at2.exception

        next(b for b in at2.button if b.label.endswith("quote (Yahoo)")).click()
        at2.run()
        assert not at2.exception

        # The fetch is only a pending candidate: cells keep projecting from the
        # committed hh.stock_price_now (192), not the fetched 284.02.
        assert at2.number_input(key="oe_price_2026").value == pytest.approx(192.0)
        assert at2.number_input(key="oe_price_2028").value == pytest.approx(192.0 * 1.07**2)
