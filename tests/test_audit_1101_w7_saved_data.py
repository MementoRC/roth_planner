"""Audit-1101 W7 -- saved-data safety (F-10, F-11, F-21).

F-10: the Koinly ledger kept one slot per owner, so scanning next year's
      report erased this year's. It now keeps one slot per owner per tax year
      (as the brokerage ledger does since audit-1026 F-15), and still reads
      ledgers saved in the old one-slot-per-owner shape.
F-11: a failed grants/shares fetch came back as "no grants, no shares" and
      was saved over the cached values. A failed fetch now keeps the cached
      grants/shares and says so; a successful empty answer still clears.
F-21: an infinite share count in the saved exercise schedule raised
      OverflowError out of load_exercise_schedule and crashed the app. It is
      now rejected like a non-finite price: the cache reads as "none saved".

All figures are synthetic.
"""

from __future__ import annotations

import math

import pytest
import requests  # type: ignore[import-untyped]

from engine.koinly_report_pdf import KoinlyReport
from engine.pdf_ledger import (
    derive_koinly_interest,
    derive_koinly_totals,
    latest_koinly_capture,
    load_ledger,
    off_year_contributions,
    prune_duplicates_elsewhere,
    save_ledger,
    write_koinly_contribution,
)


def _report(year: int, stcg: float, *, captured: str | None = None) -> KoinlyReport:
    return KoinlyReport(
        tax_year=year,
        crypto_stcg=stcg,
        crypto_ltcg=stcg * 2,
        crypto_income=stcg / 10,
        captured_at=captured or f"{year}-12-31T00:00:00",
        provenance={"pages": 3, "income_by_category": {"Lending interest": stcg / 100}},
    )


# ---------------------------------------------------------------------------
# F-10 -- Koinly keeps one slot per owner per tax year
# ---------------------------------------------------------------------------


class TestKoinlySlotPerTaxYear:
    def test_next_year_report_keeps_this_years(self) -> None:
        ledger = write_koinly_contribution({}, "you", _report(2026, 1_000.0))
        ledger = write_koinly_contribution(ledger, "you", _report(2027, 5_000.0))

        assert derive_koinly_totals(ledger, tax_year=2026) == {
            "stcg": 1_000.0,
            "ltcg": 2_000.0,
            "income": 100.0,
        }
        assert derive_koinly_totals(ledger, tax_year=2027)["stcg"] == 5_000.0

    def test_older_report_scanned_after_newer_is_kept_for_its_year(self) -> None:
        ledger = write_koinly_contribution({}, "you", _report(2027, 5_000.0))
        ledger = write_koinly_contribution(ledger, "you", _report(2026, 1_000.0))

        assert derive_koinly_totals(ledger, tax_year=2026)["stcg"] == 1_000.0
        assert derive_koinly_totals(ledger, tax_year=2027)["stcg"] == 5_000.0

    def test_same_year_rescan_replaces_not_adds(self) -> None:
        ledger = write_koinly_contribution({}, "you", _report(2026, 1_000.0))
        ledger = write_koinly_contribution(ledger, "you", _report(2026, 1_500.0))

        assert derive_koinly_totals(ledger, tax_year=2026)["stcg"] == 1_500.0

    def test_interest_and_capture_are_per_year(self) -> None:
        ledger = write_koinly_contribution(
            {}, "you", _report(2026, 1_000.0, captured="2027-01-15T00:00:00")
        )
        ledger = write_koinly_contribution(
            ledger, "you", _report(2027, 5_000.0, captured="2028-01-15T00:00:00")
        )

        assert derive_koinly_interest(ledger, tax_year=2026) == pytest.approx(10.0)
        assert latest_koinly_capture(ledger, tax_year=2026) == "2027-01-15"

    def test_off_year_note_names_only_the_other_year(self) -> None:
        ledger = write_koinly_contribution({}, "you", _report(2026, 1_000.0))
        ledger = write_koinly_contribution(ledger, "you", _report(2027, 5_000.0))

        notes = off_year_contributions(ledger, 2027, brokerage=False)
        assert len(notes) == 1
        assert "2026" in notes[0]

    def test_same_report_moves_between_owners_per_year(self) -> None:
        ledger = write_koinly_contribution({}, "household", _report(2026, 1_000.0))
        ledger = write_koinly_contribution(ledger, "household", _report(2027, 5_000.0))
        ledger = write_koinly_contribution(ledger, "you", _report(2026, 1_000.0))

        # The 2026 report moved to "you"; household keeps only its 2027 one.
        assert derive_koinly_totals(ledger, tax_year=2026)["stcg"] == 1_000.0
        assert derive_koinly_totals(ledger, tax_year=2027)["stcg"] == 5_000.0

    def test_bundle_prune_drops_duplicate_years_only(self) -> None:
        ledger = write_koinly_contribution({}, "household", _report(2026, 1_000.0))
        ledger = write_koinly_contribution(ledger, "household", _report(2027, 5_000.0))
        incoming = write_koinly_contribution({}, "you", _report(2026, 1_000.0))
        merged = {"koinly": {**ledger["koinly"], **incoming["koinly"]}, "brokerage": {}}

        pruned = prune_duplicates_elsewhere(merged, frozenset({"you"}))

        assert derive_koinly_totals(pruned, tax_year=2026)["stcg"] == 1_000.0
        assert derive_koinly_totals(pruned, tax_year=2027)["stcg"] == 5_000.0


class TestKoinlyLegacyShape:
    """Ledgers saved before F-10 hold one flat slot per owner."""

    @staticmethod
    def _legacy(year: int, stcg: float) -> dict:
        return {
            "tax_year": year,
            "stcg": stcg,
            "ltcg": stcg * 2,
            "income": stcg / 10,
            "interest": stcg / 100,
            "captured_at": f"{year}-12-31T00:00:00",
            "source": "koinly_pdf",
            "parser_version": "1",
            "provenance": {"pages": 3},
            "owner_key": None,
        }

    def test_legacy_slot_still_counts(self) -> None:
        ledger = {"koinly": {"you": self._legacy(2026, 1_000.0)}, "brokerage": {}}

        assert derive_koinly_totals(ledger, tax_year=2026)["stcg"] == 1_000.0
        assert derive_koinly_interest(ledger, tax_year=2026) == pytest.approx(10.0)

    def test_new_year_written_over_legacy_slot_keeps_it(self) -> None:
        ledger = {"koinly": {"you": self._legacy(2026, 1_000.0)}, "brokerage": {}}

        ledger = write_koinly_contribution(ledger, "you", _report(2027, 5_000.0))

        assert derive_koinly_totals(ledger, tax_year=2026)["stcg"] == 1_000.0
        assert derive_koinly_totals(ledger, tax_year=2027)["stcg"] == 5_000.0

    def test_per_year_ledger_round_trips_through_the_store(self) -> None:
        ledger = write_koinly_contribution({}, "you", _report(2026, 1_000.0))
        ledger = write_koinly_contribution(ledger, "you", _report(2027, 5_000.0))

        save_ledger(ledger)
        loaded = load_ledger()

        assert derive_koinly_totals(loaded, tax_year=2026)["stcg"] == 1_000.0
        assert derive_koinly_totals(loaded, tax_year=2027)["stcg"] == 5_000.0


# ---------------------------------------------------------------------------
# F-11 -- a failed grants/shares fetch must not erase the cached values
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestAwardsFetchFailure:
    def test_failed_fetch_is_none_not_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from engine.portfolio_sync import awards as awards_module

        def boom(*_a: object, **_kw: object) -> object:
            raise requests.ConnectionError("down")

        monkeypatch.setattr(awards_module, "_get", boom)

        assert awards_module.fetch_equity_awards() is None
        assert awards_module.fetch_shares() is None

    def test_successful_empty_answer_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from engine.portfolio_sync import awards as awards_module

        monkeypatch.setattr(awards_module, "_get", lambda *_a, **_kw: _Resp({"rows": []}))

        assert awards_module.fetch_equity_awards() == []
        assert awards_module.fetch_shares() == []

    @staticmethod
    def _sync_with(
        monkeypatch: pytest.MonkeyPatch, awards: list | None, shares: list | None
    ) -> object:
        from engine.portfolio_sync import portfolio as portfolio_module
        from engine.portfolio_sync.shapes import HoldingsSnapshot

        monkeypatch.setattr(portfolio_module, "_get", lambda *_a, **_kw: _Resp({}))
        monkeypatch.setattr(
            portfolio_module,
            "fetch_holdings_snapshot",
            lambda: HoldingsSnapshot(
                server_available=True,
                rows=[{"account": "Brokerage One", "symbol": "AAA", "market_value": 10.0}],
                as_of="2026-10-06",
                as_of_source="sync_time",
            ),
        )
        monkeypatch.setattr(portfolio_module, "fetch_equity_awards", lambda: awards)
        monkeypatch.setattr(portfolio_module, "fetch_shares", lambda: shares)
        return portfolio_module.fetch_portfolio()

    @staticmethod
    def _save_cached() -> object:
        from engine.portfolio_sync.portfolio import save_snapshot
        from engine.portfolio_sync.shapes import AccountSummary, EquityGrant, PortfolioSnapshot

        grant = EquityGrant(
            grant_id="G-1",
            grant_type="NQO",
            grant_date="2021-02-01",
            shares_granted=1_000,
            outstanding=1_000,
            current_value=12_345.0,
        )
        save_snapshot(
            PortfolioSnapshot(
                accounts=[AccountSummary(account_type="brokerage", owner="you")],
                equity_grants=[grant],
                stock_shares_held=250,
                stock_shares_value=9_999.0,
                server_available=True,
            )
        )
        return grant

    def test_failed_awards_fetch_keeps_cached_grants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        grant = self._save_cached()

        snap = self._sync_with(monkeypatch, awards=None, shares=[])

        assert snap.equity_grants == [grant]
        assert snap.error is not None
        assert "grants" in snap.error

    def test_failed_shares_fetch_keeps_cached_shares(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._save_cached()

        snap = self._sync_with(monkeypatch, awards=[], shares=None)

        assert (snap.stock_shares_held, snap.stock_shares_value) == (250, 9_999.0)
        assert snap.error is not None
        assert "shares" in snap.error

    def test_successful_empty_fetch_still_clears(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._save_cached()

        snap = self._sync_with(monkeypatch, awards=[], shares=[])

        assert snap.equity_grants == []
        assert (snap.stock_shares_held, snap.stock_shares_value) == (0, 0)
        assert snap.error is None


# ---------------------------------------------------------------------------
# F-21 -- a non-finite share count must not crash the schedule load
# ---------------------------------------------------------------------------


class TestExerciseScheduleNonFiniteShares:
    @pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
    def test_from_dict_rejects_non_finite_share_count(self, bad: float) -> None:
        from models.exercise_schedule import ExerciseSchedule

        with pytest.raises(ValueError, match="non-finite share count"):
            ExerciseSchedule.from_dict(
                {"version": 1, "shares_by_grant_year": {"G-1": {"2027": bad}}}
            )

    @pytest.mark.parametrize("bad", [math.inf, math.nan])
    def test_load_reads_non_finite_share_count_as_no_schedule(self, bad: float) -> None:
        from engine.exercise_schedule_store import load_exercise_schedule
        from engine.store import get_store
        from engine.store.namespaces import EXERCISE_SCHEDULE

        get_store().put(
            EXERCISE_SCHEDULE,
            {"version": 1, "shares_by_grant_year": {"G-1": {"2027": bad}}, "price_by_year": {}},
        )

        assert load_exercise_schedule() is None

    def test_finite_share_counts_still_load(self) -> None:
        from models.exercise_schedule import ExerciseSchedule

        sched = ExerciseSchedule.from_dict(
            {"version": 1, "shares_by_grant_year": {"G-1": {"2027": 300.0, "2028": -5}}}
        )

        assert sched.shares("G-1", 2027) == 300
        assert sched.shares("G-1", 2028) == 0
