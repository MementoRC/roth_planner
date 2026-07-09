"""Tests for YTD snapshot wiring — fetch, dividend split, MAGI, federal tax estimate."""

import pytest

from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from engine.tax import (
    federal_tax,
)
from models.household import Household
from models.ytd_income import IncomeEvent, sum_income_events


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestYTDSnapshot:
    """Test YTD income data model properties."""

    def test_ltcg_not_in_ordinary(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(ltcg_ytd=200_000, stcg_ytd=10_000, wages_ytd=50_000)
        # LTCG should NOT be in ordinary income
        assert ytd.total_ordinary_income == approx(60_000)  # wages + stcg only
        # But should be in MAGI
        assert ytd.magi_ytd == approx(260_000)

    def test_stcg_in_ordinary(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(stcg_ytd=30_000)
        assert ytd.total_ordinary_income == approx(30_000)

    def test_magi_includes_all(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            wages_ytd=100_000,
            ltcg_ytd=200_000,
            stcg_ytd=10_000,
            ordinary_dividends_ytd=5_000,
            interest_ytd=3_000,
            ira_conversions_ytd=20_000,
        )
        expected = 100_000 + 200_000 + 10_000 + 5_000 + 3_000 + 20_000
        assert ytd.magi_ytd == approx(expected)

    def test_investment_income_for_niit(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            ltcg_ytd=150_000,
            stcg_ytd=20_000,
            ordinary_dividends_ytd=10_000,
            interest_ytd=5_000,
            wages_ytd=80_000,
        )
        # Investment income: LTCG + STCG + dividends + interest (no wages)
        assert ytd.total_investment_income == approx(185_000)

    def test_gain_event_properties(self):
        from models.ytd_income import RealizedGainEvent

        event = RealizedGainEvent(
            date="2026-03-15",
            description="TXN stop-loss",
            proceeds=250_000,
            cost_basis=150_000,
            holding_period="long",
            account_name="Schwab Brokerage",
        )
        assert event.gain_loss == approx(100_000)
        assert event.is_ltcg is True

        short_event = RealizedGainEvent(
            date="2026-03-15",
            description="AAPL sale",
            proceeds=50_000,
            cost_basis=45_000,
            holding_period="short",
        )
        assert short_event.gain_loss == approx(5_000)
        assert short_event.is_ltcg is False

    def test_total_ordinary_income_includes_ordinary_dividends_not_qualified(self):
        """Ordinary dividends are taxed as ordinary income; qualified are LTCG-rate only."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            wages_ytd=50_000,
            ordinary_dividends_ytd=3_000,
            qualified_dividends_ytd=2_000,
        )
        # ordinary income = wages + ordinary_dividends; qualified excluded
        assert ytd.total_ordinary_income == approx(53_000)
        # sum property still works
        assert ytd.dividends_ytd == approx(5_000)

    def test_total_ordinary_income_includes_interest(self):
        """Interest is fully ordinary income and must be included in total_ordinary_income."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=50_000, interest_ytd=3_000)
        assert ytd.total_ordinary_income == approx(53_000)

    def test_ltcg_stack_walk_uses_interest_inclusive_base(self):
        """Regression: interest_ytd shifts the LTCG bracket boundary AFTER std-ded subtraction.

        The fix to estimate_ytd_federal_tax subtracts the standard deduction from ordinary
        income before stack-walking LTCG brackets.  interest_ytd must therefore be included
        in the pre-deduction ordinary base so it survives the subtraction and still pushes
        taxable_ordinary above the 0%-LTCG threshold.

        Arithmetic (both spouses <65, STD_DEDUCTION_MFJ=32_200, LTCG_THRESHOLDS_MFJ[0]=98_900):
          wages chosen so that:
            without interest: taxable_ordinary = wages - STD_DEDUCTION_MFJ = threshold - 5_000
            with    interest: taxable_ordinary = wages + interest - STD_DEDUCTION_MFJ = threshold + 5_000

          With interest: ltcg_start=threshold+5_000, ltcg_end=threshold+25_000
            ltcg_at_15 = (threshold+25_000) - (threshold+5_000) = 20_000  → tax = 3_000
          Without interest: ltcg_start=threshold-5_000, ltcg_end=threshold+15_000
            ltcg_at_15 = (threshold+15_000) - threshold = 15_000  → tax = 2_250

        The interest is the discriminator: it pushes more LTCG out of the 0%-band.
        """
        from engine.tax import LTCG_THRESHOLDS_MFJ, STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(your_age=61, spouse_age=55, your_ira=500_000, spouse_ira=500_000)

        # wages chosen so taxable_ordinary without interest = threshold - 5_000
        wages = STD_DEDUCTION_MFJ + LTCG_THRESHOLDS_MFJ[0] - 5_000
        interest = 10_000.0
        ltcg = 20_000.0

        # --- with interest ---
        # taxable_ordinary = wages + interest - STD_DEDUCTION_MFJ = LTCG_THRESHOLDS_MFJ[0] + 5_000
        # ltcg_start = threshold+5_000, ltcg_end = threshold+25_000
        # ltcg_at_15 = 20_000 → tax = 3_000
        ytd = YTDSnapshot(wages_ytd=wages, interest_ytd=interest, ltcg_ytd=ltcg)
        result = estimate_ytd_federal_tax(ytd, hh)
        assert result.ltcg_tax == approx(ltcg * 0.15)

        # --- anchor: without interest ---
        # taxable_ordinary = wages - STD_DEDUCTION_MFJ = LTCG_THRESHOLDS_MFJ[0] - 5_000
        # ltcg_start = threshold-5_000, ltcg_end = threshold+15_000
        # ltcg_at_15 = (threshold+15_000) - threshold = 15_000 → tax = 2_250
        ytd_no_interest = YTDSnapshot(wages_ytd=wages, ltcg_ytd=ltcg)
        result_no_interest = estimate_ytd_federal_tax(ytd_no_interest, hh)
        taxable_ord_no_int = wages - STD_DEDUCTION_MFJ
        expected_ltcg_at_15_no_int = (taxable_ord_no_int + ltcg - LTCG_THRESHOLDS_MFJ[0]) * 0.15
        assert result_no_interest.ltcg_tax == approx(expected_ltcg_at_15_no_int)
        assert result_no_interest.ltcg_tax < result.ltcg_tax


class TestYTDDividendSplit:
    """Tests for the qualified/ordinary YTD dividend split."""

    def test_backward_compat_property(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot(
            qualified_dividends_ytd=500.0,
            ordinary_dividends_ytd=300.0,
        )
        assert snap.dividends_ytd == 800.0

    def test_zero_split(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot()
        assert snap.dividends_ytd == 0.0
        assert snap.qualified_dividends_ytd == 0.0
        assert snap.ordinary_dividends_ytd == 0.0

    def test_niit_includes_both_dividend_types(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot(
            qualified_dividends_ytd=500.0,
            ordinary_dividends_ytd=300.0,
            ltcg_ytd=1000.0,
            interest_ytd=200.0,
        )
        # total_investment_income = ltcg + stcg + dividends (qual + ord) + interest
        # = 1000 + 0 + 800 + 200 = 2000
        assert snap.total_investment_income == pytest.approx(2000.0)

    def test_scenario_year_dividend_split_fields_and_compat(self):
        """YearResult carries split fields; ytd_dividends is backward-compat aggregate."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(
            tax_year=2026,
            qualified_dividends_ytd=1_000,
            ordinary_dividends_ytd=500,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        assert yr2026.ytd_qualified_dividends == approx(1_000)
        assert yr2026.ytd_ordinary_dividends == approx(500)
        # backward-compat aggregate
        assert yr2026.ytd_dividends == approx(1_500)


class TestFetchYTDSnapshotNoDoubleCount:
    """Guard against double-count when both YTD endpoints respond with dividend/interest data.

    Math audit 2026-06-12 finding #4: investment_income and ytd_income both
    accumulated into ordinary_dividends_ytd and interest_ytd via +=.  When both
    endpoints returned data for the same period (mid-year syncs), those fields
    were silently 2x'd → wrong MAGI → wrong IRMAA tier.

    Endpoint ownership contract:
      investment_income  → ordinary_dividends_ytd, interest_ytd
      ytd_income         → wages_ytd, nec_income_ytd, qualified_dividends_ytd,
                           ira_conversions_ytd, ira_distributions_ytd
    """

    def _make_investment_income_response(self, dividends: float, interest: float) -> dict:
        """Simulate /query/brokerage?data_type=investment_income multi-institution shape."""
        return {
            "institutions": {
                "fidelity": {
                    "rows": [{"received_dividends": dividends, "received_interest": interest}]
                }
            }
        }

    def _make_ytd_income_response(
        self,
        wages: float = 0.0,
        total_dividends: float = 0.0,
        qualified_dividends: float = 0.0,
        interest: float = 0.0,
        conversions: float = 0.0,
    ) -> dict:
        """Simulate /query/tax_return?data_type=ytd_income rows shape."""
        rows = []
        if wages:
            rows.append({"label": "Wages (W-2)", "amount": wages})
        if total_dividends:
            rows.append({"label": "1099-DIV dividends", "amount": total_dividends})
        if qualified_dividends:
            rows.append({"label": "Qualified dividends (1099-DIV)", "amount": qualified_dividends})
        if interest:
            rows.append({"label": "Interest income (1099-INT)", "amount": interest})
        if conversions:
            rows.append({"label": "IRA conversion", "amount": conversions})
        return {"rows": rows}

    def test_no_double_count_dividends(self, monkeypatch):
        """Both endpoints return $5_000 dividends — result must be $5_000 not $10_000."""
        import requests

        from engine import portfolio_sync
        from engine.portfolio_sync import fetch_ytd_snapshot

        call_log: list[str] = []

        class _FakeResp:
            status_code = 200

            def __init__(self, data: dict) -> None:
                self._data = data

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return self._data

        def _fake_get(url: str, params: dict | None = None, **kwargs) -> _FakeResp:
            data_type = (params or {}).get("data_type", "")
            call_log.append(data_type)
            if data_type == "investment_income":
                return _FakeResp(
                    self._make_investment_income_response(dividends=5_000.0, interest=0.0)
                )
            if data_type == "ytd_income":
                # ytd_income also has 1099-DIV data for the same period
                return _FakeResp(
                    self._make_ytd_income_response(total_dividends=5_000.0, wages=80_000.0)
                )
            return _FakeResp({"rows": []})

        monkeypatch.setattr(requests, "get", _fake_get)
        monkeypatch.setattr(portfolio_sync, "_headers", lambda: {})

        ytd = fetch_ytd_snapshot()

        assert ytd.ordinary_dividends_ytd == approx(5_000.0), (
            f"Expected 5_000 (no double-count), got {ytd.ordinary_dividends_ytd}"
        )

    def test_no_double_count_interest(self, monkeypatch):
        """Both endpoints return $3_000 interest — result must be $3_000 not $6_000."""
        import requests

        from engine import portfolio_sync
        from engine.portfolio_sync import fetch_ytd_snapshot

        class _FakeResp:
            status_code = 200

            def __init__(self, data: dict) -> None:
                self._data = data

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return self._data

        def _fake_get(url: str, params: dict | None = None, **kwargs) -> _FakeResp:
            data_type = (params or {}).get("data_type", "")
            if data_type == "investment_income":
                return _FakeResp(
                    self._make_investment_income_response(dividends=0.0, interest=3_000.0)
                )
            if data_type == "ytd_income":
                return _FakeResp(self._make_ytd_income_response(interest=3_000.0, wages=80_000.0))
            return _FakeResp({"rows": []})

        monkeypatch.setattr(requests, "get", _fake_get)
        monkeypatch.setattr(portfolio_sync, "_headers", lambda: {})

        ytd = fetch_ytd_snapshot()

        assert ytd.interest_ytd == approx(3_000.0), (
            f"Expected 3_000 (no double-count), got {ytd.interest_ytd}"
        )

    def test_fallback_when_investment_income_empty(self, monkeypatch):
        """When investment_income returns no rows, ytd_income wages/conversions still populate."""
        import requests

        from engine import portfolio_sync
        from engine.portfolio_sync import fetch_ytd_snapshot

        class _FakeResp:
            status_code = 200

            def __init__(self, data: dict) -> None:
                self._data = data

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return self._data

        def _fake_get(url: str, params: dict | None = None, **kwargs) -> _FakeResp:
            data_type = (params or {}).get("data_type", "")
            if data_type == "investment_income":
                # Empty — no dividend/interest data from brokerage
                return _FakeResp({"rows": []})
            if data_type == "ytd_income":
                return _FakeResp(
                    self._make_ytd_income_response(
                        wages=120_000.0,
                        qualified_dividends=2_000.0,
                        conversions=50_000.0,
                    )
                )
            return _FakeResp({"rows": []})

        monkeypatch.setattr(requests, "get", _fake_get)
        monkeypatch.setattr(portfolio_sync, "_headers", lambda: {})

        ytd = fetch_ytd_snapshot()

        # ytd_income-owned fields must be populated
        assert ytd.wages_ytd == approx(120_000.0)
        assert ytd.qualified_dividends_ytd == approx(2_000.0)
        assert ytd.ira_conversions_ytd == approx(50_000.0)
        # investment_income was empty → dividend/interest stay zero
        assert ytd.ordinary_dividends_ytd == approx(0.0)
        assert ytd.interest_ytd == approx(0.0)

    def test_fallback_when_ytd_income_empty(self, monkeypatch):
        """When ytd_income returns no rows, investment_income dividends/interest survive."""
        import requests

        from engine import portfolio_sync
        from engine.portfolio_sync import fetch_ytd_snapshot

        class _FakeResp:
            status_code = 200

            def __init__(self, data: dict) -> None:
                self._data = data

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return self._data

        def _fake_get(url: str, params: dict | None = None, **kwargs) -> _FakeResp:
            data_type = (params or {}).get("data_type", "")
            if data_type == "investment_income":
                return _FakeResp(
                    self._make_investment_income_response(dividends=4_500.0, interest=800.0)
                )
            if data_type == "ytd_income":
                # Empty — tax-return endpoint has no data yet
                return _FakeResp({"rows": []})
            return _FakeResp({"rows": []})

        monkeypatch.setattr(requests, "get", _fake_get)
        monkeypatch.setattr(portfolio_sync, "_headers", lambda: {})

        ytd = fetch_ytd_snapshot()

        assert ytd.ordinary_dividends_ytd == approx(4_500.0)
        assert ytd.interest_ytd == approx(800.0)
        # ytd_income-owned fields stay at defaults
        assert ytd.wages_ytd == approx(0.0)
        assert ytd.ira_conversions_ytd == approx(0.0)


class TestFetchYTDSnapshotOrdinaryDivFallback:
    """Regression for audit D-4: ordinary_dividends_ytd must be populated from
    1099-DIV box 1a when the investment_income endpoint is unavailable."""

    def test_fetch_ytd_snapshot_fallback_total_dividends(self, monkeypatch):
        """investment_income unavailable → ordinary_dividends_ytd = total_div - qual_div."""
        import warnings

        import requests

        from engine.portfolio_sync import fetch_ytd_snapshot

        class _FakeResp:
            status_code = 200

            def __init__(self, data: dict) -> None:
                self._data = data

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return self._data

        def _fake_get(url: str, params: dict | None = None, **kwargs) -> _FakeResp:
            data_type = (params or {}).get("data_type", "")
            if data_type == "investment_income":
                # Simulate unavailable endpoint
                raise requests.exceptions.ConnectionError("refused")
            if data_type == "ytd_income":
                return _FakeResp(
                    {
                        "rows": [
                            {"label": "1099-DIV dividends", "amount": 8_000.0},
                            {
                                "label": "Qualified dividends (1099-DIV)",
                                "amount": 3_000.0,
                            },
                        ]
                    }
                )
            # All other endpoints (realized_gains, etc.) raise to skip cleanly
            raise requests.exceptions.ConnectionError("refused")

        monkeypatch.setattr(requests, "get", _fake_get)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ytd = fetch_ytd_snapshot()

        assert ytd.ordinary_dividends_ytd == 5_000.0  # 8_000 - 3_000
        assert ytd.qualified_dividends_ytd == 3_000.0
        # Fallback warning must be emitted
        messages = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
        assert any("1099-DIV box 1a" in m for m in messages)

    def test_fetch_ytd_snapshot_no_fallback_when_investment_income_available(self, monkeypatch):
        """investment_income endpoint succeeds → ordinary_dividends_ytd is NOT
        overwritten by the 1099-DIV box 1a fallback."""
        import warnings

        import requests

        from engine.portfolio_sync import fetch_ytd_snapshot

        class _FakeResp:
            status_code = 200

            def __init__(self, data: dict) -> None:
                self._data = data

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return self._data

        def _fake_get(url: str, params: dict | None = None, **kwargs) -> _FakeResp:
            data_type = (params or {}).get("data_type", "")
            if data_type == "investment_income":
                return _FakeResp(
                    {
                        "institutions": {
                            "fidelity": {
                                "rows": [
                                    {
                                        "received_dividends": 6_000.0,
                                        "received_interest": 0.0,
                                    }
                                ]
                            }
                        }
                    }
                )
            if data_type == "ytd_income":
                return _FakeResp(
                    {
                        "rows": [
                            {"label": "1099-DIV dividends", "amount": 8_000.0},
                            {
                                "label": "Qualified dividends (1099-DIV)",
                                "amount": 3_000.0,
                            },
                        ]
                    }
                )
            raise requests.exceptions.ConnectionError("refused")

        monkeypatch.setattr(requests, "get", _fake_get)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ytd = fetch_ytd_snapshot()

        # investment_income was available → its value wins; no fallback applied
        assert ytd.ordinary_dividends_ytd == 6_000.0
        messages = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
        assert not any("1099-DIV box 1a" in m for m in messages)


class TestFetchMagi:
    """Verify fetch_magi + apply_magi end-to-end (A3 — prior-year MAGI consumer)."""

    # ------------------------------------------------------------------
    # fetch_magi tests
    # ------------------------------------------------------------------

    def test_fetch_magi_happy_path_returns_dict(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_magi

        payload = {
            "year": 2024,
            "filing_status": "MFJ",
            "agi": 180_000.0,
            "magi": 183_000.0,
            "tax_exempt_interest": 3_000.0,
            "ss_taxable_amount": 0.0,
            "foreign_earned_income_exclusion": 0.0,
            "source": "turbotax",
        }

        class _FakeResp:
            status_code = 200

            def json(self):
                return payload

            def raise_for_status(self):
                pass

        monkeypatch.setattr(req, "get", lambda *a, **kw: _FakeResp())
        result = fetch_magi(2024)
        assert isinstance(result, dict)
        assert result["year"] == 2024
        assert result["magi"] == 183_000.0

    def test_fetch_magi_404_returns_none(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_magi

        class _FakeResp:
            status_code = 404

            def raise_for_status(self):
                pass

        monkeypatch.setattr(req, "get", lambda *a, **kw: _FakeResp())
        assert fetch_magi(2020) is None

    def test_fetch_magi_network_error_returns_none(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_magi

        def _raise(*args, **kwargs):
            raise req.exceptions.ConnectionError("refused")

        monkeypatch.setattr(req, "get", _raise)
        assert fetch_magi(2024) is None

    def test_fetch_magi_malformed_shape_returns_none(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_magi

        class _FakeList:
            status_code = 200

            def json(self):
                return [{"year": 2024}]

            def raise_for_status(self):
                pass

        monkeypatch.setattr(req, "get", lambda *a, **kw: _FakeList())
        assert fetch_magi(2024) is None


class TestTaxExemptInterestParsing:
    """PR-3: tax-exempt (muni) interest rows must route to their own bucket,
    not the taxable interest bucket, in _parse_ytd_income_rows."""

    def test_tax_exempt_interest_label_routes_to_own_bucket(self):
        """A row labeled 'Tax-exempt interest' must populate tax_exempt_interest,
        NOT interest, in the parser result dict."""
        from engine.portfolio_sync.ytd import _parse_ytd_income_rows

        rows = [{"label": "Tax-exempt interest", "amount": 4_200.0}]
        result = _parse_ytd_income_rows(rows)
        assert result.get("tax_exempt_interest", 0.0) == 4_200.0, (
            f"Expected tax_exempt_interest=4200; got {result}"
        )
        # Must NOT bleed into the taxable interest bucket
        assert result.get("interest", 0.0) == 0.0, (
            f"Muni interest must not be in taxable 'interest' bucket; got {result}"
        )

    def test_municipal_interest_label_routes_to_own_bucket(self):
        """A row labeled 'Municipal bond interest' must populate tax_exempt_interest."""
        from engine.portfolio_sync.ytd import _parse_ytd_income_rows

        rows = [{"label": "Municipal bond interest", "amount": 1_500.0}]
        result = _parse_ytd_income_rows(rows)
        assert result.get("tax_exempt_interest", 0.0) == 1_500.0
        assert result.get("interest", 0.0) == 0.0

    def test_taxable_interest_still_routes_to_interest_bucket(self):
        """A plain 'Interest income' row must still populate the taxable interest bucket."""
        from engine.portfolio_sync.ytd import _parse_ytd_income_rows

        rows = [{"label": "Interest income (1099-INT)", "amount": 3_000.0}]
        result = _parse_ytd_income_rows(rows)
        assert result.get("interest", 0.0) == 3_000.0
        assert result.get("tax_exempt_interest", 0.0) == 0.0


class TestTaxExemptInterestSaveLoad:
    """PR-3: tax_exempt_interest_ytd must survive a save_ytd_snapshot/load round-trip."""

    def test_save_load_roundtrip_preserves_tax_exempt_interest(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import YTDSnapshot

        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", tmp_path / "ytd_muni.json")

        ytd = YTDSnapshot(tax_year=2026, wages_ytd=80_000.0, tax_exempt_interest_ytd=6_000.0)
        save_ytd_snapshot(ytd)
        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.tax_exempt_interest_ytd == 6_000.0, (
            f"Expected tax_exempt_interest_ytd=6000 after round-trip; got {loaded.tax_exempt_interest_ytd}"
        )


class TestEstimateYtdFederalTax:
    """Tests for engine.tax.estimate_ytd_federal_tax."""

    def _hh(self) -> "Household":
        from models.household import Household

        return Household(your_age=61, spouse_age=55, your_ira=500_000, spouse_ira=500_000)

    def test_zero_income_returns_all_zeros(self):
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot()
        result = estimate_ytd_federal_tax(ytd, self._hh())
        assert result.ordinary_tax == 0.0
        assert result.ltcg_tax == 0.0
        assert result.niit == 0.0
        assert result.total == 0.0
        assert result.effective_rate == 0.0

    def test_pure_wages_no_ltcg(self):
        """W-2 wages only — ordinary_tax matches bracket calc on taxable (not gross) income, ltcg_tax=0."""
        from engine.tax import STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        wages = 150_000.0
        ytd = YTDSnapshot(wages_ytd=wages)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        # updated: F19/F1 — std deduction applied before bracket walk; ordinary_tax uses
        # taxable_ordinary = wages - std_ded, not gross wages.
        assert result.ordinary_tax == pytest.approx(federal_tax(wages - STD_DEDUCTION_MFJ))
        assert result.ltcg_tax == 0.0
        assert result.niit == 0.0
        assert result.total == pytest.approx(result.ordinary_tax)

    def test_mix_wages_and_ltcg_uses_preferential_rate(self):
        """Taxable ordinary below LTCG 0%-threshold → LTCG at 0%; above → 15%.

        Standard deduction ($32,200 MFJ, no seniors) is subtracted before the
        LTCG stack-walk per IRC §1(h)(1). ltcg_start = max(wages - std_ded, 0).
        """
        from engine.tax import STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        # taxable_ordinary = 50,000 - 32,200 = 17,800 → ltcg_end = 27,800 < 96,700 → 0%
        ytd_zero = YTDSnapshot(wages_ytd=50_000.0, ltcg_ytd=10_000.0)
        r_zero = estimate_ytd_federal_tax(ytd_zero, self._hh())
        assert r_zero.ltcg_tax == pytest.approx(0.0)

        # Rev. Proc. 2025-32 §3.03: 0%/15% threshold = $98,900 (MFJ).
        # wages = 32,200 + 99,900 = 132,100 → taxable_ordinary = 99,900 > 98,900 threshold
        # ltcg_start = 99,900, ltcg_end = 119,900 → all $20K above threshold → 15%
        ytd_15 = YTDSnapshot(wages_ytd=STD_DEDUCTION_MFJ + 99_900, ltcg_ytd=20_000.0)
        r_15 = estimate_ytd_federal_tax(ytd_15, self._hh())
        assert r_15.ltcg_tax == pytest.approx(20_000.0 * 0.15)

    def test_above_niit_threshold_niit_nonzero(self):
        """MAGI above $250K with investment income → NIIT non-zero."""
        from engine.niit import NIIT_RATE, NIIT_THRESHOLD_MFJ
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        wages = NIIT_THRESHOLD_MFJ + 20_000  # $270K
        ltcg = 15_000.0
        ytd = YTDSnapshot(wages_ytd=float(wages), ltcg_ytd=ltcg)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        # magi_excess = 20_000; NII = ltcg = 15_000 → niit = 15_000 * 0.038
        assert result.niit == pytest.approx(min(ltcg, 20_000.0) * NIIT_RATE)

    def test_marginal_bracket_and_room_correct(self):
        """Marginal bracket and room-to-next-bracket are correct for mid-bracket income."""
        from engine.tax import BRACKETS_MFJ, STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        # Put wages midway through the 12% bracket (24_800–100_800 taxable)
        wages = 60_000.0  # taxable_ordinary = 60_000 - 32_200 = 27_800 → inside 12% bracket
        ytd = YTDSnapshot(wages_ytd=wages)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        assert result.marginal_bracket_pct == pytest.approx(0.12)
        # updated: F14 — room measured from taxable income (wages - std_ded), not gross wages.
        # room = BRACKETS_MFJ[1][0] - (wages - STD_DEDUCTION_MFJ) = 100_800 - 27_800 = 73_000
        taxable = wages - STD_DEDUCTION_MFJ
        assert result.room_to_next_bracket == pytest.approx(BRACKETS_MFJ[1][0] - taxable)

    def test_ltcg_tax_when_stack_crosses_15pct_threshold(self):
        """User scenario: $27K ordinary + $283K LTCG + $2,977 qual-div.

        Rev. Proc. 2025-32 §3.03: 0%/15% threshold = $98,900 (MFJ).
        std_ded = $32,200 (MFJ, no seniors). taxable_ordinary = max(27K - 32.2K, 0) = $0.
        ltcg_start = $0, ltcg_end = $285,977.
        ltcg_at_15 = min($285,977, $613,700) - max($0, $98,900) = $285,977 - $98,900 = $187,077.
        ltcg_tax = $187,077 x 0.15 = $28,061.55.
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            wages_ytd=27_000.0,
            ltcg_ytd=283_000.0,
            qualified_dividends_ytd=2_977.0,
        )
        result = estimate_ytd_federal_tax(ytd, self._hh())
        assert result.ltcg_tax == pytest.approx(187_077.0 * 0.15, abs=1.0)

    def test_ltcg_tax_all_in_0pct_bracket(self):
        """Stack entirely under $96,700 threshold → LTCG tax = $0."""
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=50_000.0, ltcg_ytd=40_000.0)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        # ltcg_start=$50K, ltcg_end=$90K — entirely below $96,700
        assert result.ltcg_tax == pytest.approx(0.0)

    def test_ltcg_tax_crosses_20pct_threshold(self):
        """Stack crosses into 20% bracket: $200K ordinary + $500K LTCG.

        Rev. Proc. 2025-32 §3.03: 0%/15% = $98,900; 15%/20% = $613,700 (MFJ).
        std_ded = $32,200 (MFJ, no seniors). taxable_ordinary = $167,800.
        ltcg_start = $167,800, ltcg_end = $667,800.
        ltcg_at_15 = min($667,800, $613,700) - max($167,800, $98,900) = $613,700 - $167,800 = $445,900.
        ltcg_at_20 = $667,800 - $613,700 = $54,100.
        ltcg_tax = $445,900 x 0.15 + $54,100 x 0.20 = $66,885.00 + $10,820.00 = $77,705.00.
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=200_000.0, ltcg_ytd=500_000.0)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        assert result.ltcg_tax == pytest.approx(77_705.00, abs=0.01)

    def test_ltcg_new_threshold_boundary_0pct_to_15pct(self):
        """Rev. Proc. 2025-32 §3.03 boundary: stack crosses $98,900 yielding partial 15%.

        std_ded = $32,200 (MFJ, no seniors).
        wages = $32,200 + $90,700 = $122,900 → taxable_ordinary = $90,700.
        ltcg_end = $90,700 + $10,000 = $100,700.
        Stack crosses Rev. Proc. 2025-32 threshold ($98,900): $1,800 in 15% band.
        Old Rev. Proc. 2024-40 threshold ($96,700) would have put $4,000 at 15% — confirms new value is used.
        """
        from engine.tax import STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=STD_DEDUCTION_MFJ + 90_700, ltcg_ytd=10_000.0)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        # ltcg_at_15 = min($100,700, $613,700) - max($90,700, $98,900) = $100,700 - $98,900 = $1,800
        # ltcg_tax = $1,800 x 0.15 = $270.00
        assert result.ltcg_tax == pytest.approx(1_800.0 * 0.15, abs=0.01)

    def test_ltcg_std_ded_both_seniors_mfj_all_zero_pct(self):
        """Regression A-2/E-7: MFJ both 65+, modest ordinary → all LTCG at 0%.

        std_ded = $32,200 + 2 x $1,650 = $35,500.
        ordinary = $80,000 → taxable_ordinary = $44,500.
        LTCG 0%-threshold ($96,700) headroom = $52,200 > $40,000 LTCG → 0% rate.

        Pre-fix (gross as stack base): ltcg_start=$80K, ltcg_end=$120K,
        ltcg_at_15 = $120K - $96,700 = $23,300 → tax $3,495 (wrong).
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(your_age=65, spouse_age=65, your_ira=500_000, spouse_ira=500_000)
        ytd = YTDSnapshot(wages_ytd=80_000.0, ltcg_ytd=40_000.0)
        result = estimate_ytd_federal_tax(ytd, hh)
        assert result.ltcg_tax == pytest.approx(0.0)

    def test_ltcg_std_ded_neither_senior_mfj_all_zero_pct(self):
        """Regression A-2/E-7: MFJ neither 65+, ordinary=$120K, LTCG=$10K → 0% LTCG.

        std_ded = $32,200.  taxable_ordinary = $87,800.
        LTCG 0%-threshold headroom = $96,700 - $87,800 = $8,900 < $10,000 LTCG.
        Wait — $10K > $8,900 headroom, so $1,100 spills into 15%.
        Use $8,000 LTCG to stay fully in 0% band.

        ordinary=$120K, LTCG=$8K → taxable_ordinary=$87,800.
        ltcg_end=$95,800 < $96,700 → all at 0%.
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(your_age=55, spouse_age=52, your_ira=500_000, spouse_ira=500_000)
        ytd = YTDSnapshot(wages_ytd=120_000.0, ltcg_ytd=8_000.0)
        result = estimate_ytd_federal_tax(ytd, hh)
        assert result.ltcg_tax == pytest.approx(0.0)

    def test_ltcg_std_ded_single_senior_all_zero_pct(self):
        """Regression A-2/E-7: Single 65+, ordinary=$40K, LTCG=$30K → all LTCG at 0%.

        std_ded = $16,100 + $1,850 = $17,950.
        taxable_ordinary = $40,000 - $17,950 = $22,050.
        Single 0%-threshold = $48,350; headroom = $26,300 > $30,000? No — $26,300 < $30,000.
        Use $25,000 LTCG to keep entirely in 0% band.

        taxable_ordinary=$22,050; ltcg_end=$47,050 < $48,350 → 0%.
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=65, spouse_age=55, your_ira=500_000, spouse_ira=0, filing_status="Single"
        )
        ytd = YTDSnapshot(wages_ytd=40_000.0, ltcg_ytd=25_000.0)
        result = estimate_ytd_federal_tax(ytd, hh)
        assert result.ltcg_tax == pytest.approx(0.0)


class TestIncomeEvent:
    def test_income_event_fields(self):
        event = IncomeEvent(date="2026-03-15", amount=25_000.0, kind="conversion", owner="you")
        assert event.date == "2026-03-15"
        assert event.amount == 25_000.0
        assert event.kind == "conversion"
        assert event.owner == "you"

    def test_income_event_owner_defaults_to_you(self):
        event = IncomeEvent(date="2026-03-15", amount=10_000.0, kind="distribution")
        assert event.owner == "you"


class TestSumIncomeEvents:
    def test_sums_matching_kind_and_owner(self):
        events = [
            IncomeEvent(date="2026-01-10", amount=10_000.0, kind="conversion", owner="you"),
            IncomeEvent(date="2026-02-10", amount=15_000.0, kind="conversion", owner="you"),
            IncomeEvent(date="2026-03-10", amount=5_000.0, kind="conversion", owner="spouse"),
            IncomeEvent(date="2026-04-10", amount=8_000.0, kind="distribution", owner="you"),
        ]
        assert sum_income_events(events, kind="conversion", owner="you") == 25_000.0
        assert sum_income_events(events, kind="conversion", owner="spouse") == 5_000.0
        assert sum_income_events(events, kind="distribution", owner="you") == 8_000.0

    def test_sum_with_no_owner_filter_combines_both(self):
        events = [
            IncomeEvent(date="2026-01-10", amount=8_000.0, kind="distribution", owner="you"),
            IncomeEvent(date="2026-02-10", amount=3_000.0, kind="distribution", owner="spouse"),
        ]
        assert sum_income_events(events, kind="distribution") == 11_000.0

    def test_empty_list_sums_to_zero(self):
        assert sum_income_events([], kind="conversion", owner="you") == 0.0
