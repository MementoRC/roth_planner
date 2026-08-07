"""Tests for engine.portfolio_sync — option exercises fetch/apply, grant_id normalization, equity sales cache."""

import json
from types import SimpleNamespace

import pytest

from models.grants import StockGrant
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestOptionExercisesFetchAndApply:
    """Verify fetch_option_exercises + apply_option_exercises end-to-end."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fake_resp(self, status_code: int, payload: dict):
        """Build a minimal requests.Response stub."""

        class _Resp:
            def __init__(self, code, data):
                self.status_code = code
                self._data = data

            def json(self):
                return self._data

        return _Resp(status_code, payload)

    def _one_row(
        self,
        grant_price: float = 104.0,
        execution_quantity: float = 1000.0,
        gross_proceeds: float = 200_000.0,
        grant_number: str = "G1",
    ) -> dict:
        return {
            "grant_price": grant_price,
            "execution_quantity": execution_quantity,
            "gross_proceeds": gross_proceeds,
            "grant_number": grant_number,
        }

    # ------------------------------------------------------------------
    # fetch_option_exercises tests
    # ------------------------------------------------------------------

    def test_multi_institution_shape_parsed(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {
            "domain": "equity_compensation",
            "data_type": "order_detail_summary",
            "institutions": {
                "UBS": {
                    "rows": [self._one_row()],
                    "captured_at": "2026-03-15T10:00:00Z",
                }
            },
        }
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.rows_count == 1
        expected_spread = 200_000.0 - 104.0 * 1000.0
        assert abs(snap.total_spread - expected_spread) < 0.01

    def test_single_institution_shape_parsed(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {"rows": [self._one_row()]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.rows_count == 1
        expected_spread = 200_000.0 - 104.0 * 1000.0
        assert abs(snap.total_spread - expected_spread) < 0.01

    def test_empty_rows_zero_spread(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {"institutions": {}}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0

    def test_same_day_sale_math(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        # gross=200000, grant_price=104, qty=1000 → spread=200000 - 104*1000 = 96000
        payload = {"rows": [self._one_row(gross_proceeds=200_000.0)]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert abs(snap.total_spread - 96_000.0) < 0.01

    def test_per_grant_aggregation(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        row1 = self._one_row(
            grant_price=104.0,
            execution_quantity=500.0,
            gross_proceeds=100_000.0,
            grant_number="G1",
        )
        row2 = self._one_row(
            grant_price=104.0,
            execution_quantity=300.0,
            gross_proceeds=60_000.0,
            grant_number="G1",
        )
        payload = {"rows": [row1, row2]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        # Both rows same grant_number → summed in by_grant_id["G1"]
        assert "G1" in snap.by_grant_id
        spread1 = 100_000.0 - 104.0 * 500.0
        spread2 = 60_000.0 - 104.0 * 300.0
        assert abs(snap.by_grant_id["G1"] - (spread1 + spread2)) < 0.01
        assert snap.rows_count == 2

    def test_per_grant_fallback_when_id_empty(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {"rows": [self._one_row(grant_number="")]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        # Empty grant_number → contributes to total but NOT to by_grant_id
        assert snap.total_spread > 0.0
        assert snap.by_grant_id == {}

    def test_404_empty_snapshot_when_server_available(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(404, {}))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0
        assert snap.error == ""

    def test_captured_at_propagated_from_multi_institution(self, monkeypatch):
        """captured_at from first institution batch is surfaced on the snapshot."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {
            "institutions": {
                "UBS": {
                    "rows": [self._one_row()],
                    "captured_at": "2026-06-10T12:00:00Z",
                }
            }
        }
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.captured_at == "2026-06-10T12:00:00Z"

    def test_captured_at_empty_for_single_institution_shape(self, monkeypatch):
        """Single-institution (rows-only) shape has no captured_at — defaults to empty string."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {"rows": [self._one_row()]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.captured_at == ""

    # ------------------------------------------------------------------
    # mode=history aggregation tests
    # ------------------------------------------------------------------

    def test_mode_history_aggregates_across_batches(self, monkeypatch):
        """mode=history: rows from all batches are combined, not just the latest."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        batches = [
            {
                "batch_id": "b1",
                "captured_at": "2026-06-01T10:00:00Z",
                "row_count": 3,
                "rows": [
                    self._one_row(gross_proceeds=200_000.0),
                    self._one_row(gross_proceeds=200_000.0),
                    self._one_row(gross_proceeds=200_000.0),
                ],
            },
            {
                "batch_id": "b2",
                "captured_at": "2026-06-05T10:00:00Z",
                "row_count": 1,
                "rows": [self._one_row(gross_proceeds=200_000.0)],
            },
            {
                "batch_id": "b3",
                "captured_at": "2026-06-10T10:00:00Z",
                "row_count": 1,
                "rows": [self._one_row(gross_proceeds=200_000.0)],
            },
        ]
        payload = {"batches": batches}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.rows_count == 5
        expected_spread = 5 * (200_000.0 - 104.0 * 1000.0)
        assert abs(snap.total_spread - expected_spread) < 0.01

    def test_mode_history_latest_captured_at_picked(self, monkeypatch):
        """mode=history: snapshot captured_at reflects the most recent batch timestamp."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        batches = [
            {
                "batch_id": "b1",
                "captured_at": "2026-06-01T10:00:00Z",
                "rows": [self._one_row()],
            },
            {
                "batch_id": "b2",
                "captured_at": "2026-06-10T12:00:00Z",
                "rows": [self._one_row()],
            },
            {
                "batch_id": "b3",
                "captured_at": "2026-06-05T08:00:00Z",
                "rows": [self._one_row()],
            },
        ]
        payload = {"batches": batches}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.captured_at == "2026-06-10T12:00:00Z"

    def test_mode_history_fallback_to_legacy_shape(self, monkeypatch):
        """When response has no batches key, falls back to legacy _flatten_query_rows path."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        # Legacy multi-institution shape — no "batches" key
        payload = {
            "institutions": {
                "UBS": {
                    "rows": [self._one_row()],
                    "captured_at": "2026-06-10T12:00:00Z",
                }
            }
        }
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.rows_count == 1
        expected_spread = 200_000.0 - 104.0 * 1000.0
        assert abs(snap.total_spread - expected_spread) < 0.01

    # ------------------------------------------------------------------
    # apply_option_exercises grant_id normalization tests
    # ------------------------------------------------------------------

    def test_grant_id_match_case_insensitive(self):
        """Household grant_id 'GR-2019'; UBS sends 'gr2019' — normalizes to same key."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=96_000.0,
            by_grant_id={"gr2019": 96_000.0},
        )
        ytd_snap = apply_option_exercises(YTDSnapshot(), exercises, hh)
        # Key remapped to household format; no warning
        assert "GR-2019" in exercises.by_grant_id
        assert "gr2019" not in exercises.by_grant_id
        assert exercises.warnings == []
        assert ytd_snap.nqo_exercise_ytd == 96_000.0

    def test_grant_id_match_strips_special_characters(self):
        """Household grant_id 'GR-2019'; UBS sends 'GR2019' (no dash) — normalized match."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=96_000.0,
            by_grant_id={"GR2019": 96_000.0},
        )
        apply_option_exercises(YTDSnapshot(), exercises, hh)
        assert "GR-2019" in exercises.by_grant_id
        assert "GR2019" not in exercises.by_grant_id
        assert exercises.warnings == []

    def test_grant_id_unmatched_warning_and_total_preserved(self):
        """Unmatched grant_id keeps raw key, emits warning, total_spread unchanged."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=50_000.0,
            by_grant_id={"GR-OTHER": 50_000.0},
        )
        ytd_snap = apply_option_exercises(YTDSnapshot(), exercises, hh)
        assert "GR-OTHER" in exercises.by_grant_id
        assert len(exercises.warnings) == 1
        assert "GR-OTHER" in exercises.warnings[0]
        assert ytd_snap.nqo_exercise_ytd == 50_000.0

    def test_grant_id_prefix_substring_match(self):
        """Household grant_id 'N0000197825'; UBS sends '197825' — tier 3 substring match."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2021, strike=169.0, shares=500, expiry_year=2031, grant_id="N0000197825"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=75_000.0,
            by_grant_id={"197825": 75_000.0},
        )
        ytd_snap = apply_option_exercises(YTDSnapshot(), exercises, hh)
        assert "N0000197825" in exercises.by_grant_id
        assert "197825" not in exercises.by_grant_id
        assert exercises.warnings == []
        assert ytd_snap.nqo_exercise_ytd == 75_000.0

    def test_grant_id_substring_picks_longest_on_ambiguity(self):
        """Two grants 'N1234' and 'N00001234' both contain '1234'; UBS sends '1234' — picks longer."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(year=2020, strike=130.0, shares=300, expiry_year=2030, grant_id="N1234"),
                StockGrant(
                    year=2021, strike=169.0, shares=400, expiry_year=2031, grant_id="N00001234"
                ),
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=40_000.0,
            by_grant_id={"1234": 40_000.0},
        )
        apply_option_exercises(YTDSnapshot(), exercises, hh)
        # Longest normalized match: "N00001234" (9 chars) beats "N1234" (5 chars)
        assert "N00001234" in exercises.by_grant_id
        assert "N1234" not in exercises.by_grant_id
        assert "1234" not in exercises.by_grant_id

    def test_grant_id_short_substring_does_not_match(self):
        """UBS sends '19' (2 chars after normalization) — below 3-char threshold, no substring match."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=20_000.0,
            by_grant_id={"19": 20_000.0},
        )
        apply_option_exercises(YTDSnapshot(), exercises, hh)
        assert "19" in exercises.by_grant_id
        assert len(exercises.warnings) == 1
        assert "19" in exercises.warnings[0]

    def test_load_path_migration_legacy_cache(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot

        cache_file = tmp_path / "ytd_legacy.json"
        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", cache_file)

        # Write a cache dict that deliberately omits nqo_exercise_ytd
        legacy_data = {
            "tax_year": 2026,
            "snapshot_date": "2026-03-01",
            "wages_ytd": 80_000.0,
            "nec_income_ytd": 0.0,
            "ira_conversions_ytd": 0.0,
            "ira_distributions_ytd": 0.0,
            "ltcg_ytd": 0.0,
            "stcg_ytd": 0.0,
            "qualified_dividends_ytd": 0.0,
            "ordinary_dividends_ytd": 0.0,
            "interest_ytd": 0.0,
            "gain_events": [],
            "manually_entered": True,
            # nqo_exercise_ytd intentionally absent
        }
        cache_file.write_text(json.dumps(legacy_data))

        result = load_ytd_snapshot()
        assert result is not None
        assert result.nqo_exercise_ytd == 0.0

    def test_sale_info_by_grant_populated_from_rows(self):
        """_parse_option_exercises_rows populates sale_info_by_grant with grant_year/strike/shares_ytd."""
        from engine.portfolio_sync import _parse_option_exercises_rows

        rows = [
            {
                "grant_number": "G2019",
                "grant_price": 104.0,
                "execution_quantity": 500.0,
                "gross_proceeds": 100_000.0,
                "grant_date": "2019-03-10",
            },
            {
                "grant_number": "G2019",
                "grant_price": 104.0,
                "execution_quantity": 300.0,
                "gross_proceeds": 60_000.0,
                "grant_date": "2019-03-10",
            },
        ]
        snap = _parse_option_exercises_rows(rows)
        info = snap.sale_info_by_grant.get("G2019", {})
        assert info.get("grant_year") == 2019
        assert abs(info.get("strike", 0) - 104.0) < 0.01
        assert info.get("shares_ytd") == 800  # 500 + 300


class TestEquitySalesCacheConsumer:
    """Verify _parse_equity_sales_lots + fetch_option_exercises_with_cache."""

    def _lot(
        self,
        grant_number: str = "N0000197825",
        grant_price: float = 169.0,
        execution_quantity: str = "100",
        gross_proceeds: float = 24400.0,
    ) -> dict:
        return {
            "grant_number": grant_number,
            "grant_price": grant_price,
            "execution_quantity": execution_quantity,
            "gross_proceeds": gross_proceeds,
        }

    def test_parses_lots_with_string_quantities(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        lots = [self._lot()]
        snap = _parse_equity_sales_lots(lots)
        assert snap.server_available is True
        assert snap.rows_count == 1
        # 24400 - 169 * 100 = 7500
        assert abs(snap.total_spread - 7500.0) < 0.01
        assert abs(snap.by_grant_id["N0000197825"] - 7500.0) < 0.01

    def test_parses_multiple_lots_per_execution(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        # 3 lots sharing same grant_number — handoff doc: lots >= executions
        lots = [
            self._lot(execution_quantity="50", gross_proceeds=12200.0),
            self._lot(execution_quantity="30", gross_proceeds=7320.0),
            self._lot(execution_quantity="20", gross_proceeds=4880.0),
        ]
        snap = _parse_equity_sales_lots(lots)
        assert snap.rows_count == 3
        # spreads: 12200-8450=3750, 7320-5070=2250, 4880-3380=1500 → total 7500
        assert abs(snap.total_spread - 7500.0) < 0.01
        assert abs(snap.by_grant_id["N0000197825"] - 7500.0) < 0.01

    def test_empty_lots_returns_empty_snapshot(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        snap = _parse_equity_sales_lots([])
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0
        assert snap.server_available is True
        assert snap.by_grant_id == {}

    def test_skips_zero_quantity_lots(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        lots = [self._lot(execution_quantity="0")]
        snap = _parse_equity_sales_lots(lots)
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0
        assert snap.warnings == []

    def test_skips_negative_spread_with_warning(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        # gross < strike * qty → negative spread
        lots = [self._lot(grant_price=200.0, execution_quantity="100", gross_proceeds=1000.0)]
        snap = _parse_equity_sales_lots(lots)
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0
        assert len(snap.warnings) == 1
        assert "negative spread" in snap.warnings[0]

    def test_fallback_to_query_when_no_lots(self, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            PortfolioSnapshot,
            fetch_option_exercises_with_cache,
        )

        fallback_snap = OptionExercisesSnapshot(server_available=True, total_spread=99.0)
        called = []

        def fake_fetch_option_exercises():
            called.append(True)
            return fallback_snap

        monkeypatch.setattr(portfolio_sync, "fetch_option_exercises", fake_fetch_option_exercises)

        snapshot = PortfolioSnapshot(equity_sales_lots=[])
        result = fetch_option_exercises_with_cache(snapshot)
        assert called == [True]
        assert result.total_spread == 99.0

    def test_uses_captured_at_from_snapshot(self):
        from engine.portfolio_sync import (
            PortfolioSnapshot,
            fetch_option_exercises_with_cache,
        )

        ts = "2026-06-11T22:30Z"
        snapshot = PortfolioSnapshot(
            equity_sales_lots=[self._lot()],
            order_detail_summary_captured_at=ts,
        )
        result = fetch_option_exercises_with_cache(snapshot)
        assert result.captured_at == ts

    def test_save_snapshot_preserves_existing_equity_sales(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import PortfolioSnapshot, save_snapshot

        cache = tmp_path / ".portfolio_cache.json"
        monkeypatch.setattr(portfolio_sync, "_CACHE_PATH", cache)

        # Simulate FinExtract's rebuild write — equity_sales and sources on disk.
        finextract_data = {
            "equity_sales": {
                "lots": [{"grant_number": "N0000197825", "grant_price": 169.0}],
                "executions": [{"id": "E001"}],
            },
            "sources": {
                "order_detail_summary": {"captured_at": "2026-06-10T12:00Z"},
            },
        }
        cache.write_text(json.dumps(finextract_data))

        # Live HTTP sync produces a snap with empty equity_sales_lots.
        snap = PortfolioSnapshot(equity_sales_lots=[], equity_sales_executions=[])
        save_snapshot(snap)

        result = json.loads(cache.read_text())
        assert "equity_sales" in result


class TestAwardsRedirectGuard:
    """S2/S3 — fetch_equity_awards and fetch_shares must not follow 3xx redirects (audit H2)."""

    def test_fetch_equity_awards_302_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 302 from FinExtract must yield [] for fetch_equity_awards, not follow the redirect."""
        from engine.portfolio_sync import client as client_module
        from engine.portfolio_sync import fetch_equity_awards

        def fake_get(url: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                status_code=302,
                headers={"Location": "http://attacker.example/steal"},
            )

        monkeypatch.setattr(client_module.requests, "get", fake_get)
        result = fetch_equity_awards()
        assert result == []

    def test_fetch_shares_302_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 302 from FinExtract must yield [] for fetch_shares, not follow the redirect."""
        from engine.portfolio_sync import client as client_module
        from engine.portfolio_sync import fetch_shares

        def fake_get(url: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                status_code=302,
                headers={"Location": "http://attacker.example/steal"},
            )

        monkeypatch.setattr(client_module.requests, "get", fake_get)
        result = fetch_shares()
        assert result == []
