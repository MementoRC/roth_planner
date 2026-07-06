"""Regression tests for audit-0706 wave-2 YTD findings.

psync-income-0: non-qualified dividend substring trap
psync-income-1 / psync-income-3: dead interest fallback
"""

import warnings  # noqa: I001
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(json_data: dict) -> MagicMock:
    """Return a mock requests.Response that yields json_data."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def _raise_connection_error(*args, **kwargs):
    import requests

    raise requests.RequestException("simulated unavailable")


# ---------------------------------------------------------------------------
# psync-income-0: non-qualified dividend misclassification
# ---------------------------------------------------------------------------


class TestNonQualifiedDividendClassification:
    """psync-income-0: 'non-qualified dividend' must NOT be parsed as qualified."""

    def test_non_qualified_dividend_label_goes_to_total_dividends(self):
        """A label 'non-qualified dividend' should land in total_dividends, not qualified_dividends."""
        from engine.portfolio_sync.ytd import _parse_ytd_income_rows

        rows = [{"label": "non-qualified dividend", "amount": 500.0}]
        result = _parse_ytd_income_rows(rows)

        assert result.get("qualified_dividends", 0.0) == 0.0, (
            "non-qualified dividend must not increment qualified_dividends"
        )
        assert result.get("total_dividends", 0.0) == 500.0, (
            "non-qualified dividend should fall through to total_dividends bucket"
        )

    def test_qualified_dividend_label_still_goes_to_qualified(self):
        """A plain 'qualified dividend' label must still land in qualified_dividends."""
        from engine.portfolio_sync.ytd import _parse_ytd_income_rows

        rows = [{"label": "qualified dividend", "amount": 800.0}]
        result = _parse_ytd_income_rows(rows)

        assert result.get("qualified_dividends", 0.0) == 800.0
        assert result.get("total_dividends", 0.0) == 0.0

    def test_non_qualified_dividends_plural_label(self):
        """Plural 'non-qualified dividends' also must not be classified as qualified."""
        from engine.portfolio_sync.ytd import _parse_ytd_income_rows

        rows = [{"label": "non-qualified dividends", "amount": 300.0}]
        result = _parse_ytd_income_rows(rows)

        assert result.get("qualified_dividends", 0.0) == 0.0
        assert result.get("total_dividends", 0.0) == 300.0


# ---------------------------------------------------------------------------
# psync-income-1 / psync-income-3: dead interest fallback
# ---------------------------------------------------------------------------


class TestInterestFallback:
    """psync-income-1/3: interest_ytd must be populated from parsed rows when
    investment_income endpoint is unavailable."""

    def _build_ytd_income_response(self, rows: list[dict]) -> dict:
        return {
            "rows": rows,
            "institution": "test",
            "captured_at": "2026-07-01T00:00:00Z",
        }

    def test_interest_ytd_populated_via_fallback_when_investment_income_fails(self):
        """When investment_income endpoint fails, interest_ytd should come from ytd_income rows."""
        from engine.portfolio_sync.ytd import fetch_ytd_snapshot

        ytd_income_payload = self._build_ytd_income_response(
            [{"label": "interest income", "amount": 1_200.0}]
        )

        def fake_get(path: str, **kwargs):
            if path == "/status":
                resp = MagicMock()
                resp.raise_for_status.return_value = None
                return resp
            if path == "/query/brokerage":
                params = kwargs.get("params", {})
                if params.get("data_type") == "realized_gains":
                    return _make_response({"rows": []})
                # investment_income → simulate failure
                import requests

                raise requests.RequestException("endpoint unavailable")
            if path == "/query/tax_return":
                return _make_response(ytd_income_payload)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {}
            return resp

        with patch("engine.portfolio_sync.ytd._get", side_effect=fake_get), \
                warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ytd = fetch_ytd_snapshot()

        assert ytd.interest_ytd == 1_200.0, (
            f"interest_ytd should be 1200 via fallback, got {ytd.interest_ytd}"
        )
        warning_texts = [str(w.message) for w in caught]
        assert any("interest" in t.lower() for t in warning_texts), (
            f"Expected a UserWarning mentioning interest; got: {warning_texts}"
        )

    def test_interest_ytd_not_overwritten_when_investment_income_succeeds(self):
        """When investment_income endpoint succeeds, its interest_ytd value wins."""
        from engine.portfolio_sync.ytd import fetch_ytd_snapshot

        ytd_income_payload = self._build_ytd_income_response(
            [{"label": "interest income", "amount": 999.0}]
        )

        def fake_get(path: str, **kwargs):
            if path == "/status":
                resp = MagicMock()
                resp.raise_for_status.return_value = None
                return resp
            if path == "/query/brokerage":
                params = kwargs.get("params", {})
                if params.get("data_type") == "realized_gains":
                    return _make_response({"rows": []})
                # investment_income → succeeds with its own interest value
                return _make_response(
                    {"rows": [{"received_dividends": 0.0, "received_interest": 500.0}]}
                )
            if path == "/query/tax_return":
                return _make_response(ytd_income_payload)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {}
            return resp

        with patch("engine.portfolio_sync.ytd._get", side_effect=fake_get), \
                warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ytd = fetch_ytd_snapshot()

        # investment_income endpoint won → 500, not 999
        assert ytd.interest_ytd == 500.0, (
            f"investment_income interest should take precedence; got {ytd.interest_ytd}"
        )
        # No fallback warning expected
        interest_warnings = [w for w in caught if "interest" in str(w.message).lower()]
        assert len(interest_warnings) == 0, (
            f"No interest fallback warning expected when primary endpoint succeeded; got {interest_warnings}"
        )
