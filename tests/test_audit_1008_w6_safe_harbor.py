"""audit-1008 W6 — safe-harbor guidance (F-35, F-68, F-73).

All figures are synthetic.

F-35  The "prior year tax" must come from the PRIOR tax year's 1040, not from
      whichever cached year happens to be newest / have a Line 24.
F-68  The 100% vs 110% test (§6654(d)(1)(C)) uses the prior-year AGI shown on
      the return, not IRMAA MAGI (AGI + tax-exempt interest).
F-73  Due dates roll forward past Saturdays, Sundays AND legal holidays
      (§7503), including DC Emancipation Day and MLK Day.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from engine.tax import (
    _next_quarterly_due,
    load_prior_year_agi,
    load_prior_year_federal_tax,
    safe_harbor_payment,
)


def _write_cache(tmp_path, monkeypatch, payload: dict) -> None:
    import engine.tax_return_pdf as tax_return_pdf_module

    cache = tmp_path / ".tax_pdf_cache.json"
    cache.write_text(json.dumps(payload))
    monkeypatch.setattr(tax_return_pdf_module, "_PDF_TAX_CACHE_PATH", cache)


# ---------------------------------------------------------------------------
# F-35: prior-year tax is taken from the requested tax year only
# ---------------------------------------------------------------------------


class TestPriorYearTaxIsYearSpecific:
    def test_page1_only_prior_year_does_not_fall_back_to_older_year(self, tmp_path, monkeypatch):
        # 2025 imported without page 2 (Line 24 = 0); 2024 is complete.
        _write_cache(
            tmp_path,
            monkeypatch,
            {
                "2025": {"agi": 200_000.0, "total_federal_tax": 0.0},
                "2024": {"agi": 180_000.0, "total_federal_tax": 30_000.0},
            },
        )
        assert load_prior_year_federal_tax(2025) == pytest.approx(0.0)

    def test_missing_prior_year_returns_zero_even_with_older_year(self, tmp_path, monkeypatch):
        _write_cache(tmp_path, monkeypatch, {"2023": {"total_federal_tax": 10_000.0}})
        assert load_prior_year_federal_tax(2025) == pytest.approx(0.0)

    def test_complete_prior_year_is_returned(self, tmp_path, monkeypatch):
        _write_cache(
            tmp_path,
            monkeypatch,
            {
                "2025": {"total_federal_tax": 30_000.0},
                "2024": {"total_federal_tax": 10_000.0},
            },
        )
        assert load_prior_year_federal_tax(2025) == pytest.approx(30_000.0)

    def test_newer_year_is_not_used_for_prior_year(self, tmp_path, monkeypatch):
        # A current-year return (e.g. a draft) must not stand in for the prior year.
        _write_cache(
            tmp_path,
            monkeypatch,
            {
                "2026": {"total_federal_tax": 99_000.0},
                "2025": {"total_federal_tax": 30_000.0},
            },
        )
        assert load_prior_year_federal_tax(2025) == pytest.approx(30_000.0)


# ---------------------------------------------------------------------------
# F-68: prior-year AGI (not IRMAA MAGI) drives the 100%/110% rule
# ---------------------------------------------------------------------------


class TestPriorYearAgi:
    def test_returns_agi_shown_on_return(self, tmp_path, monkeypatch):
        _write_cache(
            tmp_path,
            monkeypatch,
            {
                "2025": {
                    "agi": 140_000.0,
                    "tax_exempt_interest": 15_000.0,
                    "magi": 155_000.0,
                    "total_federal_tax": 20_000.0,
                }
            },
        )
        assert load_prior_year_agi(2025) == pytest.approx(140_000.0)

    def test_none_when_year_not_imported(self, tmp_path, monkeypatch):
        _write_cache(tmp_path, monkeypatch, {"2024": {"agi": 140_000.0}})
        assert load_prior_year_agi(2025) is None

    def test_none_when_no_cache(self, tmp_path, monkeypatch):
        import engine.tax_return_pdf as tax_return_pdf_module

        monkeypatch.setattr(tax_return_pdf_module, "_PDF_TAX_CACHE_PATH", tmp_path / "absent.json")
        assert load_prior_year_agi(2025) is None


# ---------------------------------------------------------------------------
# F-73: §7503 legal-holiday rollover
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payment_date", "expected"),
    [
        # Q4 Jan 15 falls on MLK Day -> next business day.
        ("2023-12-01", "2024-01-16"),
        ("2022-12-01", "2023-01-17"),
        ("2021-12-01", "2022-01-18"),
        ("2027-12-01", "2028-01-18"),
        # Q1 Apr 15 rolls past DC Emancipation Day (observed).
        ("2023-03-01", "2023-04-18"),
        ("2028-03-01", "2028-04-18"),
        ("2022-03-01", "2022-04-18"),
        # Controls: ordinary business days / plain weekends.
        ("2026-12-01", "2027-01-15"),
        ("2026-03-01", "2026-04-15"),
        ("2024-06-18", "2024-09-16"),
    ],
)
def test_due_date_rolls_past_weekends_and_legal_holidays(payment_date, expected):
    assert _next_quarterly_due(payment_date) == expected


@pytest.mark.parametrize(
    ("payment_date", "expected"),
    [
        # Paying ON the true (rolled) deadline stays in that quarter.
        ("2028-04-18", "2028-04-18"),
        ("2023-04-18", "2023-04-18"),
        # Days between the nominal and rolled deadline map to the rolled one.
        ("2028-04-17", "2028-04-18"),
        ("2023-04-17", "2023-04-18"),
        # audit-1018 F-13: Jan 1-15 maps to the prior tax year's Q4 (Jan 15, here
        # rolled to Tue Jan 16 because the 15th is MLK Day), not to Q1.
        ("2024-01-10", "2024-01-16"),
    ],
)
def test_true_deadline_day_does_not_skip_a_quarter(payment_date, expected):
    assert _next_quarterly_due(payment_date) == expected


def test_safe_harbor_payment_reports_holiday_rolled_due_date():
    g = safe_harbor_payment(10_000.0, 12_000.0, 2_000.0, "2027-12-01", prior_year_agi=100_000.0)
    assert g.next_quarterly_due == "2028-01-18"


# ---------------------------------------------------------------------------
# View wiring (F-35 + F-68)
# ---------------------------------------------------------------------------


class _FrozenDate(date):
    """date.today() pinned to 2028 so the tests never depend on the wall clock."""

    @classmethod
    def today(cls) -> date:
        return cls(2028, 6, 1)


# The view derives the prior year from the snapshot's own tax_year, so the tests
# pin tax_year explicitly instead of inheriting the model default or today's year.
_TAX_YEAR = 2027
_PRIOR_YEAR = _TAX_YEAR - 1


class TestAnalysisViewWiring:
    def _render_capture(self, hh, ytd, *, agi_return, tax_return=50_000.0):
        from engine.tax import SafeHarborGuidance
        from tests.test_views_ytd_income import (
            _make_mock_st,
            analysis_mod,
            event_log_mod,
            manual_entry_mod,
            sync_scan_mod,
            ytd_income_mod,
        )

        mock_st = _make_mock_st(ytd)
        captured: dict = {}
        tax_calls: list = []

        def _capture(**kwargs):
            captured.update(kwargs)
            return SafeHarborGuidance()

        def _tax(*args, **kwargs):
            tax_calls.append((args, kwargs))
            return tax_return

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(manual_entry_mod, "st", mock_st),
            patch.object(event_log_mod, "st", mock_st),
            patch.object(analysis_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
            patch(
                "views.ytd_income._partials._analysis.load_prior_year_federal_tax",
                side_effect=_tax,
            ),
            patch(
                "views.ytd_income._partials._analysis.load_prior_year_agi",
                return_value=agi_return,
            ),
            patch("views.ytd_income._partials._analysis.safe_harbor_payment", side_effect=_capture),
            patch.object(analysis_mod, "_date", _FrozenDate),
        ):
            ytd_income_mod.render(hh)
        return captured, tax_calls, mock_st

    def test_view_requests_prior_tax_year_specifically(self):
        from models.ytd_income import YTDSnapshot
        from tests.test_views_ytd_income import _stub_hh

        _captured, tax_calls, _ = self._render_capture(
            _stub_hh(), YTDSnapshot(tax_year=_TAX_YEAR, wages_ytd=100_000.0), agi_return=None
        )
        assert tax_calls, "load_prior_year_federal_tax was not called"
        args, kwargs = tax_calls[0]
        requested = args[0] if args else kwargs.get("tax_year")
        assert requested == _PRIOR_YEAR

    def test_view_prefers_return_agi_over_irmaa_magi(self):
        from models.ytd_income import YTDSnapshot
        from tests.test_views_ytd_income import _stub_hh

        hh = _stub_hh(prior_year_magi={_PRIOR_YEAR: 155_000.0})
        captured, _tax_calls, _ = self._render_capture(
            hh, YTDSnapshot(tax_year=_TAX_YEAR, wages_ytd=100_000.0), agi_return=140_000.0
        )
        assert captured.get("prior_year_agi") == 140_000.0

    def test_view_falls_back_to_magi_proxy_when_no_return_agi(self):
        from models.ytd_income import YTDSnapshot
        from tests.test_views_ytd_income import _stub_hh

        hh = _stub_hh(prior_year_magi={_PRIOR_YEAR: 155_000.0})
        captured, _tax_calls, mock_st = self._render_capture(
            hh, YTDSnapshot(tax_year=_TAX_YEAR, wages_ytd=100_000.0), agi_return=None
        )
        assert captured.get("prior_year_agi") == 155_000.0
        captions = " ".join(
            str(c.args[0]) if c.args else "" for c in mock_st.caption.call_args_list
        )
        assert "proxy" in captions.lower()
