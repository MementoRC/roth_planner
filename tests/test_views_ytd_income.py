"""Smoke tests for views/ytd_income.py — NQO exercises display (PR3)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import views.ytd_income as ytd_income_mod
from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.koinly_report_pdf import KoinlyReport
from engine.pdf_import import PdfImportResult
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot


def _stub_hh(**kwargs) -> Household:
    return Household(
        your_age=61,
        spouse_age=55,
        your_ira=500_000,
        spouse_ira=500_000,
        **kwargs,
    )


def _make_mock_st(ytd: YTDSnapshot) -> MagicMock:
    """Build a streamlit mock whose session_state returns the given YTDSnapshot."""
    mock_st = MagicMock()
    # session_state needs .get() like a dict AND supports attribute assignment.
    session_state = MagicMock()
    _state: dict = {"ytd_snapshot": ytd, "apply_ytd_to_projection": False}
    session_state.get.side_effect = lambda key, default=None: _state.get(key, default)
    mock_st.session_state = session_state
    # number_input returns 0 so manual-entry YTDSnapshot construction is numeric
    mock_st.number_input.return_value = 0
    # checkbox returns False so the manual-entry block is skipped
    mock_st.checkbox.return_value = False

    # columns returns MagicMocks that support context-manager and attribute access
    def _columns_side_effect(arg):
        n = arg if isinstance(arg, int) else len(arg)
        return [MagicMock() for _ in range(n)]

    mock_st.columns.side_effect = _columns_side_effect
    mock_st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_st.expander.return_value.__exit__ = MagicMock(return_value=False)
    mock_st.form.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_st.form.return_value.__exit__ = MagicMock(return_value=False)
    mock_st.form_submit_button.return_value = False
    mock_st.button.return_value = False
    return mock_st


class TestYtdIncomeNqoDisplay:
    """View-layer smoke tests for NQO exercises metric and per-grant breakdown."""

    def test_renders_with_nqo_exercises(self):
        """Render with nqo_exercise_ytd set — should not raise; NQO metric present."""
        hh = _stub_hh()
        ytd = YTDSnapshot(nqo_exercise_ytd=96_000.0)
        ytd._option_exercises_by_grant = {"GR-2019": 96_000.0}  # noqa: SLF001
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        metric_labels = [
            (call.args[0] if call.args else call.kwargs.get("label", ""))
            for call in mock_st.metric.call_args_list
        ]
        assert any("NQO" in lbl for lbl in metric_labels), (
            f"Expected st.metric call with 'NQO' label; got: {metric_labels}"
        )

    def test_renders_empty_no_nqo_section(self):
        """Render with nqo_exercise_ytd=0 — should not raise; NQO metric absent."""
        hh = _stub_hh()
        ytd = YTDSnapshot(nqo_exercise_ytd=0.0)
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        metric_labels = [
            (call.args[0] if call.args else call.kwargs.get("label", ""))
            for call in mock_st.metric.call_args_list
        ]
        assert not any("NQO" in lbl for lbl in metric_labels), (
            "NQO metric should not appear when nqo_exercise_ytd=0"
        )

    def test_per_grant_table_with_household_grants(self):
        """Render with by_grant breakdown and grants without grant_id — single table, all unmatched."""
        grants = [
            StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029),
            StockGrant(year=2020, strike=130.0, shares=500, expiry_year=2030),
            StockGrant(year=2021, strike=169.0, shares=300, expiry_year=2031),
        ]
        hh = _stub_hh(grants=grants)
        ytd = YTDSnapshot(nqo_exercise_ytd=96_000.0)
        ytd._option_exercises_by_grant = {  # noqa: SLF001
            "GR-2019": 80_000.0,
            "GR-2020": 16_000.0,
        }
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        # Now a single st.dataframe call (joined table) instead of two separate tables
        # All rows show Year="—" since grants have no grant_id to match
        dataframe_calls = mock_st.dataframe.call_args_list
        joined_call = next(
            (
                c
                for c in dataframe_calls
                if isinstance(c.args[0] if c.args else c.kwargs.get("data"), list)
            ),
            None,
        )
        assert joined_call is not None, (
            "Expected at least one st.dataframe call with a list of rows"
        )
        rows = joined_call.args[0] if joined_call.args else joined_call.kwargs["data"]
        assert all(r["Year"] == "—" for r in rows), (
            f"All rows should be unmatched (Year='—') since grants have no grant_id; got: {rows}"
        )

    def test_per_grant_table_joins_when_grant_id_matches(self):
        """Single joined table uses grant data when grant_id matches."""
        grants = [
            StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"),
        ]
        hh = _stub_hh(grants=grants)
        ytd = YTDSnapshot(nqo_exercise_ytd=96_000.0)
        ytd._option_exercises_by_grant = {"GR-2019": 96_000.0}  # noqa: SLF001
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        dataframe_calls = mock_st.dataframe.call_args_list
        joined_call = next(
            (
                c
                for c in dataframe_calls
                if isinstance(c.args[0] if c.args else c.kwargs.get("data"), list)
            ),
            None,
        )
        assert joined_call is not None, "Expected st.dataframe call with list of rows"
        rows = joined_call.args[0] if joined_call.args else joined_call.kwargs["data"]
        assert len(rows) == 1
        assert rows[0]["Year"] == "2019", (
            f"Matched row should show Year='2019' (str); got: {rows[0]}"
        )
        assert rows[0]["Strike"] == "$104.00"
        assert rows[0]["Grant #"] == "GR-2019"

    def test_per_grant_table_year_column_is_string(self):
        """Year/Shares/Expiry columns must be strings to avoid PyArrow ArrowTypeError."""
        grants = [
            StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"),
        ]
        hh = _stub_hh(grants=grants)
        ytd = YTDSnapshot(nqo_exercise_ytd=96_000.0)
        ytd._option_exercises_by_grant = {"GR-2019": 96_000.0}  # noqa: SLF001
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        dataframe_calls = mock_st.dataframe.call_args_list
        joined_call = next(
            (
                c
                for c in dataframe_calls
                if isinstance(c.args[0] if c.args else c.kwargs.get("data"), list)
            ),
            None,
        )
        assert joined_call is not None
        rows = joined_call.args[0] if joined_call.args else joined_call.kwargs["data"]
        assert len(rows) == 1
        row = rows[0]
        # Verify Year, Shares, Expiry are all strings (not int)
        assert isinstance(row["Year"], str), (
            f"Year should be str, got {type(row['Year'])}: {row['Year']}"
        )
        assert isinstance(row["Shares"], str), (
            f"Shares should be str, got {type(row['Shares'])}: {row['Shares']}"
        )
        assert isinstance(row["Expiry"], str), (
            f"Expiry should be str, got {type(row['Expiry'])}: {row['Expiry']}"
        )
        assert row["Year"] == "2019"
        assert row["Shares"] == "1000"
        assert row["Expiry"] == "2029"


class TestInvestmentIncomeDisplay:
    """View-layer smoke tests for dividend/interest metrics in the YTD Position block."""

    def test_renders_dividend_interest_metrics_when_nonzero(self):
        """All three investment-income metrics appear when any value is non-zero."""
        hh = _stub_hh()
        ytd = YTDSnapshot(
            qualified_dividends_ytd=5_000.0,
            ordinary_dividends_ytd=2_000.0,
            interest_ytd=1_000.0,
        )
        mock_st = _make_mock_st(ytd)
        # Track every column mock returned so we can inspect .metric calls on them
        col_mocks: list[MagicMock] = []
        _orig_columns = mock_st.columns.side_effect

        def _tracking_columns(arg):
            cols = _orig_columns(arg)
            col_mocks.extend(cols)
            return cols

        mock_st.columns.side_effect = _tracking_columns

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        # Collect all metric labels: direct st.metric + column.metric calls
        all_metric_calls = list(mock_st.metric.call_args_list)
        for col in col_mocks:
            all_metric_calls.extend(col.metric.call_args_list)
        metric_labels = [
            (call.args[0] if call.args else call.kwargs.get("label", ""))
            for call in all_metric_calls
        ]
        assert any("Qualified dividends" in lbl for lbl in metric_labels), (
            f"Expected 'Qualified dividends (YTD)' metric; got: {metric_labels}"
        )
        assert any("Ordinary dividends" in lbl for lbl in metric_labels), (
            f"Expected 'Ordinary dividends (YTD)' metric; got: {metric_labels}"
        )
        assert any("Interest" in lbl for lbl in metric_labels), (
            f"Expected 'Interest (YTD)' metric; got: {metric_labels}"
        )
        caption_calls = [
            (call.args[0] if call.args else call.kwargs.get("body", ""))
            for call in mock_st.caption.call_args_list
        ]
        assert any("Investment income impacting headroom" in c for c in caption_calls), (
            f"Expected caption 'Investment income impacting headroom'; got: {caption_calls}"
        )

    def test_skips_dividend_interest_block_when_all_zero(self):
        """No dividend/interest metrics rendered when all three fields are 0.0."""
        hh = _stub_hh()
        ytd = YTDSnapshot(
            qualified_dividends_ytd=0.0,
            ordinary_dividends_ytd=0.0,
            interest_ytd=0.0,
        )
        mock_st = _make_mock_st(ytd)
        col_mocks: list[MagicMock] = []
        _orig_columns = mock_st.columns.side_effect

        def _tracking_columns(arg):
            cols = _orig_columns(arg)
            col_mocks.extend(cols)
            return cols

        mock_st.columns.side_effect = _tracking_columns

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        all_metric_calls = list(mock_st.metric.call_args_list)
        for col in col_mocks:
            all_metric_calls.extend(col.metric.call_args_list)
        metric_labels = [
            (call.args[0] if call.args else call.kwargs.get("label", ""))
            for call in all_metric_calls
        ]
        assert not any("Qualified dividends" in lbl for lbl in metric_labels), (
            "Qualified dividends metric should not appear when value is 0"
        )
        assert not any("Ordinary dividends" in lbl for lbl in metric_labels), (
            "Ordinary dividends metric should not appear when value is 0"
        )
        assert not any("Interest" in lbl for lbl in metric_labels), (
            "Interest metric should not appear when value is 0"
        )


class TestManualEntryAutoDeselect:
    """Tests for auto-deselect of manual-entry checkbox on successful YTD sync."""

    def test_successful_sync_clears_manual_entry_state(self):
        """On successful YTD sync, manual entry is auto-deselected and page reruns."""
        hh = _stub_hh()
        ytd_empty = YTDSnapshot()
        mock_st = _make_mock_st(ytd_empty)

        # Setup: sync button clicked, checkbox initially checked
        mock_st.button.return_value = True
        mock_st.checkbox.return_value = True

        # Create synced snapshot with data
        synced_ytd = YTDSnapshot(
            tax_year=hh.base_year,
            wages_ytd=150_000.0,
            ltcg_ytd=50_000.0,
            snapshot_date="2026-06-12",  # Mark as synced
        )

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.fetch_ytd_snapshot", return_value=synced_ytd),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            # Mock fetch_option_exercises to return unavailable result
            mock_exercises = MagicMock()
            mock_exercises.server_available = False
            mock_fetch_ex.return_value = mock_exercises

            ytd_income_mod.render(hh)

        # Assert: session_state["ytd_manual_entry"] was set to False
        # MagicMock tracks __setitem__ calls automatically
        setitem_calls = [
            call
            for call in mock_st.session_state.__setitem__.call_args_list
            if call[0][0] == "ytd_manual_entry"
        ]
        assert any(call[0][1] is False for call in setitem_calls), (
            f"Expected ytd_manual_entry set to False; got calls: {setitem_calls}"
        )
        # Assert: rerun was called
        assert mock_st.rerun.called, "Expected st.rerun() to be called on successful sync"

    def test_failed_sync_does_not_clear_manual_entry(self):
        """On failed YTD sync, manual entry remains unchanged and page does not rerun."""
        hh = _stub_hh()
        ytd_empty = YTDSnapshot()
        mock_st = _make_mock_st(ytd_empty)

        # Setup: sync button clicked
        mock_st.button.return_value = True
        mock_st.checkbox.return_value = True

        # Create failed snapshot (no snapshot_date)
        failed_ytd = YTDSnapshot(
            tax_year=hh.base_year,
            wages_ytd=0.0,
        )
        # snapshot_date stays empty (falsy)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.fetch_ytd_snapshot", return_value=failed_ytd),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            # Mock fetch_option_exercises
            mock_exercises = MagicMock()
            mock_exercises.server_available = False
            mock_fetch_ex.return_value = mock_exercises

            ytd_income_mod.render(hh)

        # Assert: ytd_manual_entry was NOT set to False in the success branch
        setitem_calls = [
            call
            for call in mock_st.session_state.__setitem__.call_args_list
            if call[0][0] == "ytd_manual_entry" and call[0][1] is False
        ]
        assert not setitem_calls, "On failed sync, ytd_manual_entry should not be set to False"
        # Assert: rerun was NOT called
        assert not mock_st.rerun.called, "On failed sync, st.rerun() should not be called"

    def test_sync_preserves_manual_only_fields_not_zeroed(self):
        """The FinExtract sync button is NQO-exercises-only now — investment income
        (interest/dividends/gains) comes from brokerage statement PDFs instead, and
        the manual-entry-only fields were always FinExtract-independent. Neither
        category may be zeroed out by an NQO-only sync."""
        hh = _stub_hh()
        prior_ytd = YTDSnapshot(
            tax_year=hh.base_year,
            wages_ytd=80_000.0,
            qualified_dividends_ytd=2_000.0,
            nec_income_ytd=5_000.0,
            ira_conversions_ytd=25_000.0,
            spouse_ira_conversions_ytd=7_500.0,
            ira_distributions_ytd=10_000.0,
            tax_exempt_interest_ytd=1_500.0,
            ltcg_ytd=50_000.0,
            stcg_ytd=4_000.0,
            ordinary_dividends_ytd=3_000.0,
            interest_ytd=600.0,
        )
        mock_st = _make_mock_st(prior_ytd)
        mock_st.button.return_value = True
        # Manual entry off: this mock harness runs render() in a single pass with no
        # real rerun, so leaving checkbox truthy would fall through into the manual
        # entry block afterward and re-clobber session_state.ytd_snapshot from
        # (mocked, zero-valued) widget inputs — unlike production, where st.rerun()
        # halts execution immediately after a successful sync.
        mock_st.checkbox.return_value = False

        # Fresh sync — fetch_ytd_snapshot no longer supplies ANY investment-income
        # field (that's brokerage-statement-sourced now); a real sync only ever sets
        # manually_entered/snapshot_date, so the fresh snapshot has none of the
        # fields above populated.
        synced_ytd = YTDSnapshot(
            tax_year=hh.base_year,
            snapshot_date="2026-06-12",
        )

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.fetch_ytd_snapshot", return_value=synced_ytd),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            mock_exercises = MagicMock()
            mock_exercises.server_available = False
            mock_fetch_ex.return_value = mock_exercises

            ytd_income_mod.render(hh)

        result = mock_st.session_state.ytd_snapshot
        assert result.wages_ytd == 80_000.0
        assert result.qualified_dividends_ytd == 2_000.0
        assert result.nec_income_ytd == 5_000.0
        assert result.ira_conversions_ytd == 25_000.0
        assert result.spouse_ira_conversions_ytd == 7_500.0
        assert result.ira_distributions_ytd == 10_000.0
        assert result.tax_exempt_interest_ytd == 1_500.0
        # Investment-income fields are statement-sourced now, not FinExtract-sourced
        # -- an NQO-only sync must preserve them from the prior snapshot too.
        assert result.ltcg_ytd == 50_000.0
        assert result.stcg_ytd == 4_000.0
        assert result.ordinary_dividends_ytd == 3_000.0
        assert result.interest_ytd == 600.0


class TestTaxBracketAndSafeHarborSections:
    """Smoke tests for the new tax-bracket, estimated-tax, and safe-harbor sections."""

    def test_renders_tax_bracket_section(self):
        """Tax Bracket Position section renders without exception."""
        hh = _stub_hh()
        ytd = YTDSnapshot(wages_ytd=120_000.0, ltcg_ytd=15_000.0)
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        # At least one subheader call with bracket-related text
        subheader_calls = [
            (call.args[0] if call.args else call.kwargs.get("body", ""))
            for call in mock_st.subheader.call_args_list
        ]
        assert any("Tax Bracket" in s for s in subheader_calls), (
            f"Expected 'Tax Bracket Position' subheader; got: {subheader_calls}"
        )

    def test_renders_estimated_tax_section(self):
        """Estimated YTD Federal Tax section renders without exception."""
        hh = _stub_hh()
        ytd = YTDSnapshot(wages_ytd=200_000.0, ltcg_ytd=20_000.0)
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        subheader_calls = [
            (call.args[0] if call.args else call.kwargs.get("body", ""))
            for call in mock_st.subheader.call_args_list
        ]
        assert any("Federal Tax" in s for s in subheader_calls), (
            f"Expected 'Estimated YTD Federal Tax' subheader; got: {subheader_calls}"
        )

    def test_renders_safe_harbor_warning_when_no_prior_year(self):
        """Safe-harbor section shows warning when prior year tax is unknown (returns 0)."""
        hh = _stub_hh()
        ytd = YTDSnapshot(wages_ytd=180_000.0)
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
            patch("views.ytd_income.load_prior_year_federal_tax", return_value=0.0),
        ):
            ytd_income_mod.render(hh)

        # st.warning should have been called (prior year unknown)
        assert mock_st.warning.called, "Expected st.warning() when prior year tax is unknown"
        warning_msgs = [
            (call.args[0] if call.args else call.kwargs.get("body", ""))
            for call in mock_st.warning.call_args_list
        ]
        assert any("Prior year tax unknown" in m for m in warning_msgs), (
            f"Expected 'Prior year tax unknown' warning; got: {warning_msgs}"
        )

    def test_safe_harbor_call_threads_filing_status_and_prior_agi(self):
        """Caller passes hh.filing_status and prior-year AGI (from hh.prior_year_magi) into safe_harbor_payment."""
        from datetime import date

        from engine.tax import SafeHarborGuidance

        prior_year = date.today().year - 1
        hh = _stub_hh(filing_status="Single", prior_year_magi={prior_year: 120_000.0})
        ytd = YTDSnapshot(wages_ytd=180_000.0)
        mock_st = _make_mock_st(ytd)

        captured: dict = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return SafeHarborGuidance()

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
            patch("views.ytd_income.load_prior_year_federal_tax", return_value=50_000.0),
            patch("views.ytd_income.safe_harbor_payment", side_effect=_capture),
        ):
            ytd_income_mod.render(hh)

        assert captured.get("filing_status") == "Single"
        assert captured.get("prior_year_agi") == 120_000.0

    def test_renders_capital_gains_section_with_events(self):
        """Realized Capital Gains section renders breakdown when gain_events present."""
        from models.ytd_income import RealizedGainEvent

        hh = _stub_hh()
        events = [
            RealizedGainEvent(
                date="2026-03-15",
                description="AAPL sale",
                proceeds=15_000.0,
                cost_basis=10_000.0,
                holding_period="long",
                account_name="Brokerage",
            )
        ]
        ytd = YTDSnapshot(ltcg_ytd=5_000.0, gain_events=events)
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        subheader_calls = [
            (call.args[0] if call.args else call.kwargs.get("body", ""))
            for call in mock_st.subheader.call_args_list
        ]
        assert any("Capital Gains" in s for s in subheader_calls), (
            f"Expected 'Realized Capital Gains' subheader; got: {subheader_calls}"
        )


class TestManualEntryFieldCoverage:
    """Regression: manual entry must not silently drop fields with no dedicated widget.

    Note: `ira_distributions_ytd` is intentionally NOT asserted here. As of this task
    (Task 3 of the ytd-manual-entry-coverage plan), `views/ytd_income.py` has no widget
    or passthrough for `ira_distributions_ytd` in the manual-entry `YTDSnapshot(...)`
    reconstruction at all — it is dropped regardless of this fix. That field gets full
    treatment as part of the income-event log in Task 4 (separate, later dispatch).
    Task 3 is narrowly scoped to `nec_income_ytd` only.
    """

    def test_manual_entry_preserves_nec_income(self):
        """Covers nec_income_ytd only; ira_distributions_ytd deferred to Task 4."""
        hh = _stub_hh()
        ytd = YTDSnapshot(nec_income_ytd=5_000.0)
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = True  # "Manual entry" ON
        # Echo back whatever `value=` kwarg each number_input was pre-filled with,
        # simulating a user who hasn't touched a given widget yet.
        mock_st.number_input.side_effect = lambda *a, **kw: kw.get("value", 0)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        saved = mock_st.session_state.ytd_snapshot
        assert saved.nec_income_ytd == 5_000.0

    def test_manual_entry_preserves_negative_ltcg_loss(self):
        """Stored ltcg_ytd=-3000 (a real net realized capital LOSS) must not be
        clamped to 0 by the manual-entry LTCG widget -- unlike the sibling STCG
        field, the LTCG widget used to force its displayed/default value to 0
        whenever ltcg_ytd <= 0, silently discarding a legitimate loss that the
        unconditional save_ytd_snapshot() call would then persist as 0.0."""
        hh = _stub_hh()
        ytd = YTDSnapshot(ltcg_ytd=-3_000.0)
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = True  # "Manual entry" ON
        # Echo back whatever `value=` kwarg each number_input was pre-filled with,
        # simulating a user who hasn't touched a given widget yet.
        mock_st.number_input.side_effect = lambda *a, **kw: kw.get("value", 0)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        saved = mock_st.session_state.ytd_snapshot
        assert saved.ltcg_ytd == -3_000.0, (
            f"Expected the stored -3000 LTCG loss to survive manual-entry render "
            f"unclamped; got {saved.ltcg_ytd}"
        )


class TestIncomeEventLog:
    """Tests for the income event log UI (replaces flat conversion/distribution inputs)."""

    def test_owner_options_include_spouse_for_mfj(self):
        """Manual entry ON, MFJ household: owner selectbox options include 'Spouse'."""
        hh = _stub_hh()  # default filing_status == "MFJ"
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = True  # "Manual entry" ON
        mock_st.form_submit_button.return_value = False

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        found_spouse_options = False
        for call in mock_st.selectbox.call_args_list:
            options = call.args[1] if len(call.args) > 1 else call.kwargs.get("options")
            if options and "Spouse" in options:
                found_spouse_options = True
                break
        assert found_spouse_options, (
            f"Expected a selectbox with 'Spouse' in options for MFJ; "
            f"calls: {mock_st.selectbox.call_args_list}"
        )

    def test_owner_options_exclude_spouse_for_single(self):
        """Manual entry ON, Single household: no selectbox options include 'Spouse'."""
        hh = _stub_hh(filing_status="Single")
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = True  # "Manual entry" ON
        mock_st.form_submit_button.return_value = False

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        for call in mock_st.selectbox.call_args_list:
            options = call.args[1] if len(call.args) > 1 else call.kwargs.get("options")
            if options:
                assert "Spouse" not in options, (
                    f"Did not expect 'Spouse' in any selectbox options for Single filer; "
                    f"got: {options}"
                )

    def test_existing_entries_display_in_dataframe(self):
        """Seeded income_events render as rows in an st.dataframe call."""
        from models.ytd_income import IncomeEvent

        hh = _stub_hh()
        events = [
            IncomeEvent(date="2026-02-01", amount=10_000.0, kind="conversion", owner="you"),
            IncomeEvent(date="2026-03-15", amount=4_000.0, kind="distribution", owner="spouse"),
        ]
        ytd = YTDSnapshot(income_events=events)
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = True  # "Manual entry" ON
        mock_st.form_submit_button.return_value = False

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        found = None
        for call in mock_st.dataframe.call_args_list:
            data = call.args[0] if call.args else call.kwargs.get("data")
            try:
                import pandas as pd

                if isinstance(data, pd.DataFrame) and len(data) == 2:
                    found = data
                    break
            except ImportError:
                pass
        assert found is not None, (
            f"Expected an st.dataframe call with 2 rows reflecting the seeded income_events; "
            f"calls: {mock_st.dataframe.call_args_list}"
        )

    def test_add_entry_updates_conversions_done(self):
        """Submitting the add-entry form with a conversion for 'You' updates ira_conversions_ytd."""
        from datetime import date as _dt_date

        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = True  # "Manual entry" ON
        mock_st.number_input.return_value = 25_000
        mock_st.form_submit_button.return_value = True
        mock_st.date_input.return_value = _dt_date(2026, 3, 1)
        # Form selectboxes (Type, Whose) return these; the post-add "Remove an entry"
        # selectbox (a third selectbox call, since income_events is now non-empty)
        # gets None so the "if del_idx is not None" branch is skipped.
        mock_st.selectbox.side_effect = ["Conversion", "You", None]

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        saved = mock_st.session_state.ytd_snapshot
        assert saved.ira_conversions_ytd == 25_000.0


class TestBrokerageStatementSync:
    """Tests for the 'Sync from Brokerage Statements (PDF)' section — the account-type
    safety property this whole feature exists for: a Roth/IRA statement scanned
    alongside a taxable one must never contribute to taxable YTD income."""

    def test_scan_stores_parsed_accounts_in_session_state(self, tmp_path, monkeypatch):
        """Clicking 'Scan statement folder' stores the parsed-and-latest-per-account
        dict in session_state under 'statement_by_account' (verified via the same
        __setitem__ call-tracking pattern used elsewhere in this file, since the
        session_state mock's .get() is not linked to bracket-assignment)."""
        import engine.pdf_ledger as ledger_mod
        import engine.pdf_owner as owner_mod
        from engine.brokerage_statement_pdf import BrokerageStatementRecord

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False  # manual entry off
        mock_st.text_input.return_value = str(tmp_path)
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st.selectbox.return_value = "household"  # no owner_key -> manual confirm

        taxable_rec = BrokerageStatementRecord(
            account_number="XXXX9320",
            broker="vanguard",
            account_type="taxable",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=0.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=1028.55,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-10T00:00:00+00:00",
        )

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch(
                "engine.pdf_import.scan_pdf_folder",
                return_value=PdfImportResult(brokerage_records=[taxable_rec]),
            ),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path"),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
            patch.object(ledger_mod, "_LEDGER_PATH", tmp_path / ".pdf_import_ledger.json"),
            patch.object(owner_mod, "_OWNER_MAP_PATH", tmp_path / ".pdf_owner_map.json"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        setitem_calls = [
            call for call in mock_st.session_state.__setitem__.call_args_list if call[0][0] == "statement_by_account"
        ]
        assert setitem_calls, "Expected statement_by_account to be stored in session_state after a scan"
        stored = setitem_calls[-1][0][1]
        assert stored["XXXX9320"].account_type == "taxable"

    def test_scan_auto_applies_taxable_statement_to_ytd_snapshot(self, tmp_path, monkeypatch):
        """Clicking 'Scan folder' must auto-apply a taxable account's parsed
        interest/dividends straight into the YTD snapshot and persist it via
        save_ytd_snapshot — no separate 'Apply to YTD snapshot' click required."""
        import engine.pdf_ledger as ledger_mod
        import engine.pdf_owner as owner_mod
        from engine.brokerage_statement_pdf import BrokerageStatementRecord

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False  # manual entry off
        mock_st.text_input.return_value = str(tmp_path)
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st.selectbox.return_value = "household"  # no owner_key -> manual confirm

        taxable_rec = BrokerageStatementRecord(
            account_number="XXXX9320",
            broker="vanguard",
            account_type="taxable",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=500.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=1028.55,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-10T00:00:00+00:00",
        )

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch(
                "engine.pdf_import.scan_pdf_folder",
                return_value=PdfImportResult(brokerage_records=[taxable_rec]),
            ),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path"),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch.object(ytd_income_mod, "save_ytd_snapshot") as mock_save_snapshot,
            patch.object(ledger_mod, "_LEDGER_PATH", tmp_path / ".pdf_import_ledger.json"),
            patch.object(owner_mod, "_OWNER_MAP_PATH", tmp_path / ".pdf_owner_map.json"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        assert mock_save_snapshot.called, "Expected save_ytd_snapshot to be called during scan (auto-apply)"
        saved_snapshot = mock_save_snapshot.call_args[0][0]
        assert saved_snapshot.interest_ytd == 500.0
        assert saved_snapshot.ordinary_dividends_ytd == 1028.55

    def test_roth_ira_statement_excluded_from_taxable_partition(self, tmp_path, monkeypatch):
        """Regression test for the exact bug that motivated this whole feature: a
        Roth IRA statement scanned into session_state must partition to 'excluded',
        never to 'taxable' — verified by re-running the same partition function the
        view uses on whatever it actually stored."""
        from engine.brokerage_statement_pdf import (
            BrokerageStatementRecord,
            partition_by_account_type,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False  # manual entry off
        mock_st.text_input.return_value = str(tmp_path)
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"

        roth_rec = BrokerageStatementRecord(
            account_number="XXXX7368",
            broker="vanguard",
            account_type="roth_ira",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=0.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=283.86,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-10T00:00:00+00:00",
        )

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch(
                "engine.pdf_import.scan_pdf_folder",
                return_value=PdfImportResult(brokerage_records=[roth_rec]),
            ),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path"),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        setitem_calls = [
            call for call in mock_st.session_state.__setitem__.call_args_list if call[0][0] == "statement_by_account"
        ]
        assert setitem_calls
        stored = setitem_calls[-1][0][1]
        taxable, excluded, unknown = partition_by_account_type(stored)
        assert "XXXX7368" not in taxable
        assert "XXXX7368" in excluded
        assert unknown == {}

    def test_blank_folder_input_rejected_without_crashing(self):
        """Empty/blank statement-folder input must produce a clear st.error,
        not a Path()/glob() crash — regression test for the CodeQL path-injection
        finding on PR #348 (validate-before-construct)."""
        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False  # manual entry off
        mock_st.text_input.return_value = "   "  # blank/whitespace-only
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.pdf_import.scan_pdf_folder") as mock_scan,
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path") as mock_save,
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        assert mock_st.error.called, "Expected st.error for blank statement-folder input"
        mock_scan.assert_not_called()
        mock_save.assert_not_called()

    def test_control_char_in_folder_input_rejected(self):
        """A statement-folder input containing a control character (e.g. an
        embedded NUL byte) must be rejected with a clear st.error before a
        Path object is ever constructed — defensive validation requested on
        PR #348 (reject ord(ch) < 32 in user-provided text)."""
        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False  # manual entry off
        mock_st.text_input.return_value = "\x00/some/path"
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.pdf_import.scan_pdf_folder") as mock_scan,
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path") as mock_save,
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        assert mock_st.error.called, "Expected st.error for a control character in statement-folder input"
        error_msg = mock_st.error.call_args[0][0]
        assert "invalid characters" in error_msg
        mock_scan.assert_not_called()
        mock_save.assert_not_called()

    def test_traversal_input_is_rejected_before_path_construction(self, tmp_path, monkeypatch):
        """A folder path containing a literal '..' segment must be rejected
        with a clear st.error BEFORE any Path object is ever constructed —
        this is now the earliest possible rejection point (ahead of
        .expanduser()/.resolve()), per the CodeQL py/path-injection query,
        which flags the Path() construction line itself as the taint sink.
        Silently normalizing '..' away downstream is no longer acceptable;
        traversal-shaped input must never reach Path() at all."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        real_dir = tmp_path / "Statements"
        real_dir.mkdir()
        traversal_input = str(tmp_path / "Statements" / "sub" / "..")

        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False
        mock_st.text_input.return_value = traversal_input
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.pdf_import.scan_pdf_folder") as mock_scan,
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path") as mock_save,
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        assert mock_st.error.called, "Expected st.error for a folder path containing '..'"
        error_msg = mock_st.error.call_args[0][0]
        assert ".." in error_msg
        mock_scan.assert_not_called()
        mock_save.assert_not_called()

    def test_double_dot_substring_rejected_before_scan(self, tmp_path, monkeypatch):
        """A statement-folder input containing '..' as a substring (classic
        path-traversal shape, e.g. '../../etc' or 'foo/../bar') must be
        rejected with a clear st.error before scan_statement_folder or
        save_statement_folder_path is ever called — regression test for the
        persistent CodeQL py/path-injection alert on PR #348, which flags the
        Path() construction line itself as the taint sink. The guard must
        run strictly before any Path()-based operation."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False
        mock_st.text_input.return_value = "../../etc"
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.pdf_import.scan_pdf_folder") as mock_scan,
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path") as mock_save,
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        assert mock_st.error.called, "Expected st.error for a folder input containing '..'"
        error_msg = mock_st.error.call_args[0][0]
        assert ".." in error_msg
        mock_scan.assert_not_called()
        mock_save.assert_not_called()

    def test_folder_outside_home_is_rejected(self, tmp_path, monkeypatch):
        """A resolved statement-folder path outside the user's home directory must
        be rejected with a clear st.error and must never reach scan_statement_folder
        — the real containment boundary behind the CodeQL 'uncontrolled data used in
        path expression' fix, not just normalization."""
        fake_home = tmp_path / "home_dir"
        fake_home.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False
        mock_st.text_input.return_value = str(outside_dir)
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.pdf_import.scan_pdf_folder") as mock_scan,
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path") as mock_save,
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        assert mock_st.error.called, "Expected st.error for a folder outside the home directory"
        error_msg = mock_st.error.call_args[0][0]
        assert "home directory" in error_msg
        mock_scan.assert_not_called()
        mock_save.assert_not_called()

    def test_scan_persists_to_cache_via_save_statement_records(self, tmp_path, monkeypatch):
        """After a successful scan, save_statement_records is called with the
        parsed-and-latest-per-account dict so the scan survives an app restart."""
        import engine.pdf_ledger as ledger_mod
        import engine.pdf_owner as owner_mod
        from engine.brokerage_statement_pdf import BrokerageStatementRecord

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False  # manual entry off
        mock_st.text_input.return_value = str(tmp_path)
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st.selectbox.return_value = "household"  # no owner_key -> manual confirm

        taxable_rec = BrokerageStatementRecord(
            account_number="XXXX9320",
            broker="vanguard",
            account_type="taxable",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=0.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=1028.55,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-10T00:00:00+00:00",
        )

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch(
                "engine.pdf_import.scan_pdf_folder",
                return_value=PdfImportResult(brokerage_records=[taxable_rec]),
            ),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path"),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.brokerage_statement_pdf.save_statement_records") as mock_save_records,
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
            patch.object(ledger_mod, "_LEDGER_PATH", tmp_path / ".pdf_import_ledger.json"),
            patch.object(owner_mod, "_OWNER_MAP_PATH", tmp_path / ".pdf_owner_map.json"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        mock_save_records.assert_called_once()
        (saved_by_account,), _ = mock_save_records.call_args
        assert saved_by_account["XXXX9320"].account_type == "taxable"

    def test_hydrates_statement_by_account_from_cache_on_page_load(self, monkeypatch):
        """With no prior scan this run (no 'statement_by_account' key in
        session_state), the view must hydrate it from load_statement_records()
        and re-apply saved overrides via apply_account_type_overrides, so a
        scan survives an app restart."""
        from engine.brokerage_statement_pdf import BrokerageStatementRecord

        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False  # manual entry off
        mock_st.text_input.return_value = ""
        mock_st.button.return_value = False  # scan button not clicked this run

        cached_rec = BrokerageStatementRecord(
            account_number="XXXX1234",
            broker="schwab",
            account_type="taxable",
            statement_period_end="2026-05-31",
            interest_taxable_ytd=10.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=200.0,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-06-01T00:00:00+00:00",
        )
        cached_by_account = {"XXXX1234": cached_rec}

        # session_state.get() must report "not present" so the hydration guard fires,
        # matching this file's established pattern of a session_state mock whose
        # .get() is not linked to bracket-assignment.
        mock_st.session_state.__contains__ = MagicMock(return_value=False)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.load_statement_records", return_value=cached_by_account),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.brokerage_statement_pdf.apply_account_type_overrides") as mock_apply,
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            mock_apply.return_value = cached_by_account
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        mock_apply.assert_called_once_with(cached_by_account, {})
        setitem_calls = [
            call for call in mock_st.session_state.__setitem__.call_args_list if call[0][0] == "statement_by_account"
        ]
        assert setitem_calls, "Expected statement_by_account to be hydrated into session_state"
        assert setitem_calls[-1][0][1] == cached_by_account

    def test_confirm_account_override_refreshes_statement_cache(self, tmp_path, monkeypatch):
        """Confirming an account's tax status must refresh
        session_state['statement_by_account'] within the same render pass, so the
        confirmed classification survives st.rerun() instead of the stale cached
        'unknown' classification being reused (regression: previously only
        save_account_type_override() persisted to disk, and the in-memory cache
        was never re-hydrated until a fresh 'Scan folder' click)."""
        from engine.brokerage_statement_pdf import BrokerageStatementRecord

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.checkbox.return_value = False  # manual entry off
        mock_st.text_input.return_value = ""
        mock_st.button.return_value = False  # "Scan folder" not clicked this run
        mock_st.selectbox.return_value = "taxable"  # user confirms the unknown account

        unknown_rec = BrokerageStatementRecord(
            account_number="XXXX5555",
            broker="schwab",
            account_type="unknown",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=0.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=0.0,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-10T00:00:00+00:00",
        )
        by_account = {"XXXX5555": unknown_rec}

        # statement_by_account already present in session_state this run (skip
        # the hydration branch, matching a rerun-after-scan scenario).
        mock_st.session_state.__contains__ = MagicMock(return_value=True)
        _state = {
            "ytd_snapshot": ytd,
            "apply_ytd_to_projection": False,
            "statement_by_account": by_account,
        }
        mock_st.session_state.get.side_effect = lambda key, default=None: _state.get(key, default)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_account_type_override") as mock_save_override,
            patch(
                "engine.brokerage_statement_pdf.load_account_type_overrides",
                return_value={"XXXX5555": "taxable"},
            ),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        mock_save_override.assert_called_once_with("XXXX5555", "taxable")
        setitem_calls = [
            call for call in mock_st.session_state.__setitem__.call_args_list if call[0][0] == "statement_by_account"
        ]
        assert setitem_calls, (
            "Expected statement_by_account to be refreshed in session_state after "
            "confirming an account override"
        )
        refreshed = setitem_calls[-1][0][1]
        assert refreshed["XXXX5555"].account_type == "taxable", (
            "Confirmed account must be reclassified as 'taxable' in the refreshed "
            f"cache, not left stale as 'unknown'; got {refreshed['XXXX5555'].account_type!r}"
        )


def _koinly_report(owner_key: str | None, stcg: float, ltcg: float, income: float) -> KoinlyReport:
    return KoinlyReport(
        tax_year=2026,
        crypto_stcg=stcg,
        crypto_ltcg=ltcg,
        crypto_income=income,
        captured_at="2026-07-13T00:00:00+00:00",
        owner_key=owner_key,
    )


class TestOwnerAttributionScanFlow:
    """Regression coverage for the Koinly override bug (docs/superpowers/specs/
    2026-07-13-spouse-pdf-owner-attribution-design.md): scanning a second
    owner's Koinly report must ADD to crypto_stcg_ytd/crypto_ltcg_ytd/
    crypto_income_ytd, not overwrite them."""

    def _run_scan(self, hh, mock_st, canned_result, ledger_path, owner_map_path, tmp_path, monkeypatch):
        import engine.pdf_ledger as ledger_mod
        import engine.pdf_owner as owner_mod

        # validate_local_folder requires the scanned folder to be under
        # Path.home(); pytest's tmp_path lives under /tmp, so home must be
        # repointed here (mirrors the pattern used by TestBrokerageStatementSync
        # elsewhere in this file).
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch.object(ytd_income_mod, "is_pyodide", return_value=False),
            patch("engine.pdf_import.scan_pdf_folder", return_value=canned_result),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
            patch.object(ledger_mod, "_LEDGER_PATH", ledger_path),
            patch.object(owner_mod, "_OWNER_MAP_PATH", owner_map_path),
        ):
            ytd_income_mod.render(hh)

    def test_two_owner_koinly_scan_sums_not_overrides(self, tmp_path, monkeypatch):
        """Core regression: scan 'you' Koinly, then scan 'spouse' Koinly in a
        SEPARATE render call -- final crypto_*_ytd must be the SUM, matching
        the design's derive-sum contract, not the second report's raw value."""
        hh = _stub_hh()

        # First render: "you" scans a Koinly report.
        ytd1 = YTDSnapshot()
        mock_st1 = _make_mock_st(ytd1)
        mock_st1.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st1.text_input.return_value = str(tmp_path)
        mock_st1.selectbox.return_value = "you"
        result1 = PdfImportResult(koinly_reports=[_koinly_report("claude r cirba", 100.0, 200.0, 50.0)])
        self._run_scan(
            hh, mock_st1, result1,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path, monkeypatch,
        )

        # Second render: "spouse" scans a separate Koinly report. Ledger/owner
        # map persist on disk between renders (same tmp_path), same as two
        # separate Streamlit sessions on the same machine.
        ytd2 = YTDSnapshot()
        mock_st2 = _make_mock_st(ytd2)
        mock_st2.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st2.text_input.return_value = str(tmp_path)
        mock_st2.selectbox.return_value = "spouse"
        result2 = PdfImportResult(koinly_reports=[_koinly_report("jane r cirba", 10.0, 20.0, 5.0)])
        self._run_scan(
            hh, mock_st2, result2,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path, monkeypatch,
        )

        # Read back via direct attribute access to match test file's established pattern
        final_snap = mock_st2.session_state.ytd_snapshot
        assert final_snap.crypto_stcg_ytd == pytest.approx(110.0)
        assert final_snap.crypto_ltcg_ytd == pytest.approx(220.0)
        assert final_snap.crypto_income_ytd == pytest.approx(55.0)

    def test_idempotent_rescan_same_owner_unchanged_total(self, tmp_path, monkeypatch):
        hh = _stub_hh()
        ytd1 = YTDSnapshot()
        mock_st1 = _make_mock_st(ytd1)
        mock_st1.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st1.text_input.return_value = str(tmp_path)
        mock_st1.selectbox.return_value = "you"
        result = PdfImportResult(koinly_reports=[_koinly_report("claude r cirba", 100.0, 200.0, 50.0)])
        self._run_scan(
            hh, mock_st1, result,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path, monkeypatch,
        )
        ytd2 = YTDSnapshot()
        mock_st2 = _make_mock_st(ytd2)
        mock_st2.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st2.text_input.return_value = str(tmp_path)
        mock_st2.selectbox.return_value = "you"
        self._run_scan(
            hh, mock_st2, result,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path, monkeypatch,
        )
        # Read back via direct attribute access to match test file's established pattern
        final_snap = mock_st2.session_state.ytd_snapshot
        assert final_snap.crypto_stcg_ytd == pytest.approx(100.0)

    def test_no_owner_key_falls_back_to_manual_selectbox(self, tmp_path, monkeypatch):
        """A Koinly report with owner_key=None must not silently apply --
        the UI's manual role selectbox must be consulted."""
        hh = _stub_hh()
        ytd1 = YTDSnapshot()
        mock_st1 = _make_mock_st(ytd1)
        mock_st1.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st1.text_input.return_value = str(tmp_path)
        mock_st1.selectbox.return_value = "household"
        result = PdfImportResult(koinly_reports=[_koinly_report(None, 100.0, 200.0, 50.0)])
        self._run_scan(
            hh, mock_st1, result,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path, monkeypatch,
        )
        # Manual role selectbox must have been invoked with the owner options.
        selectbox_calls = mock_st1.selectbox.call_args_list
        assert any(
            "you" in (c.args[1] if len(c.args) > 1 else c.kwargs.get("options", []))
            for c in selectbox_calls
        )


def _brokerage_record(
    account_number: str,
    owner_key: str | None,
    interest: float = 0.0,
    dividends: float = 0.0,
) -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number=account_number,
        broker="schwab",
        account_type="taxable",
        statement_period_end="2026-06-30",
        interest_taxable_ytd=interest,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=dividends,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-07-13T00:00:00+00:00",
        owner_key=owner_key,
    )


class TestBrokerageOwnerAttributionScanFlow:
    """Regression coverage for the brokerage override bug (Task 7 of the
    spouse-pdf-owner-attribution plan): scanning a second owner's brokerage
    statements must ADD to interest_ytd/ordinary_dividends_ytd/etc., not
    overwrite them -- the same fix already proven for Koinly in Task 6."""

    def _run_scan(
        self, hh, mock_st, canned_result, ledger_path, owner_map_path, overrides_path, tmp_path, monkeypatch
    ):
        import engine.brokerage_statement_pdf as stmt_mod
        import engine.pdf_ledger as ledger_mod
        import engine.pdf_owner as owner_mod

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch.object(ytd_income_mod, "is_pyodide", return_value=False),
            patch("engine.pdf_import.scan_pdf_folder", return_value=canned_result),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
            patch.object(ledger_mod, "_LEDGER_PATH", ledger_path),
            patch.object(owner_mod, "_OWNER_MAP_PATH", owner_map_path),
            patch.object(stmt_mod, "_ACCOUNT_TYPE_OVERRIDES_PATH", overrides_path),
            patch.object(stmt_mod, "_STATEMENT_CACHE_PATH", tmp_path / ".brokerage_statement_cache.json"),
        ):
            ytd_income_mod.render(hh)

    def test_two_owner_brokerage_scan_sums_not_overrides(self, tmp_path, monkeypatch):
        """Core regression: scan owner A's accounts, then owner B's accounts
        in a SEPARATE render -- final interest_ytd/ordinary_dividends_ytd must
        be the SUM of both owners' accounts, not just B's (today's bug)."""
        hh = _stub_hh()

        ytd1 = YTDSnapshot()
        mock_st1 = _make_mock_st(ytd1)
        mock_st1.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st1.text_input.return_value = str(tmp_path)
        mock_st1.selectbox.return_value = "you"
        result1 = PdfImportResult(
            brokerage_records=[_brokerage_record("A1", "claude r cirba", interest=10.0, dividends=5.0)]
        )
        self._run_scan(
            hh, mock_st1, result1,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json",
            tmp_path / ".statement_account_overrides.json", tmp_path, monkeypatch,
        )

        ytd2 = YTDSnapshot()
        mock_st2 = _make_mock_st(ytd2)
        mock_st2.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st2.text_input.return_value = str(tmp_path)
        mock_st2.selectbox.return_value = "spouse"
        result2 = PdfImportResult(
            brokerage_records=[_brokerage_record("B1", "jane r cirba", interest=20.0, dividends=8.0)]
        )
        self._run_scan(
            hh, mock_st2, result2,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json",
            tmp_path / ".statement_account_overrides.json", tmp_path, monkeypatch,
        )

        final_snap = mock_st2.session_state.ytd_snapshot
        assert final_snap.interest_ytd == pytest.approx(30.0)
        assert final_snap.ordinary_dividends_ytd == pytest.approx(13.0)

    def test_idempotent_rescan_same_owner_same_account_unchanged(self, tmp_path, monkeypatch):
        hh = _stub_hh()
        result = PdfImportResult(
            brokerage_records=[_brokerage_record("A1", "claude r cirba", interest=10.0)]
        )
        ytd1 = YTDSnapshot()
        mock_st1 = _make_mock_st(ytd1)
        mock_st1.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st1.text_input.return_value = str(tmp_path)
        mock_st1.selectbox.return_value = "you"
        self._run_scan(
            hh, mock_st1, result,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json",
            tmp_path / ".statement_account_overrides.json", tmp_path, monkeypatch,
        )
        ytd2 = YTDSnapshot()
        mock_st2 = _make_mock_st(ytd2)
        mock_st2.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st2.text_input.return_value = str(tmp_path)
        mock_st2.selectbox.return_value = "you"
        self._run_scan(
            hh, mock_st2, result,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json",
            tmp_path / ".statement_account_overrides.json", tmp_path, monkeypatch,
        )
        final_snap = mock_st2.session_state.ytd_snapshot
        assert final_snap.interest_ytd == pytest.approx(10.0)

    def test_unstated_account_does_not_contribute_until_confirmed(self, tmp_path, monkeypatch):
        """account_type='unknown' must NOT reach the ledger -- it stays gated
        behind the existing stmt_unknown confirm-loop (unchanged by Task 7)."""
        hh = _stub_hh()
        from dataclasses import replace

        base = _brokerage_record("A1", "claude r cirba", interest=10.0)
        unstated = replace(base, account_type="unknown")
        result = PdfImportResult(brokerage_records=[unstated])
        ytd1 = YTDSnapshot()
        mock_st1 = _make_mock_st(ytd1)
        mock_st1.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st1.text_input.return_value = str(tmp_path)
        mock_st1.selectbox.return_value = "you"
        self._run_scan(
            hh, mock_st1, result,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json",
            tmp_path / ".statement_account_overrides.json", tmp_path, monkeypatch,
        )
        # An unstated (unknown tax-status) account contributes nothing, so
        # applied_bits stays empty and st.session_state.ytd_snapshot is never
        # reassigned this render -- ytd1 itself is the load-bearing check,
        # not the mock's auto-generated attribute (which was never set).
        assert ytd1.interest_ytd == pytest.approx(0.0)


class TestCombinedKoinlyAndBrokerageScanFlow:
    """Regression coverage for the shared owner_map/ledger threading across
    the brokerage loop (views/ytd_income.py runs first) and the Koinly loop
    (runs second) within a SINGLE render, per the spouse-pdf-owner-attribution
    design. Neither loop may clobber the other's owner_map learning or ledger
    writes when both doc types appear in one scan_pdf_folder result."""

    def _run_scan(
        self, hh, mock_st, canned_result, ledger_path, owner_map_path, overrides_path, tmp_path, monkeypatch
    ):
        import engine.brokerage_statement_pdf as stmt_mod
        import engine.pdf_ledger as ledger_mod
        import engine.pdf_owner as owner_mod

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch.object(ytd_income_mod, "is_pyodide", return_value=False),
            patch("engine.pdf_import.scan_pdf_folder", return_value=canned_result),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
            patch.object(ledger_mod, "_LEDGER_PATH", ledger_path),
            patch.object(owner_mod, "_OWNER_MAP_PATH", owner_map_path),
            patch.object(stmt_mod, "_ACCOUNT_TYPE_OVERRIDES_PATH", overrides_path),
            patch.object(stmt_mod, "_STATEMENT_CACHE_PATH", tmp_path / ".brokerage_statement_cache.json"),
        ):
            ytd_income_mod.render(hh)

    def test_combined_koinly_and_brokerage_scan_resolves_both_owners(self, tmp_path, monkeypatch):
        """One scan_pdf_folder result containing BOTH a Koinly report (owner
        'you') and a taxable brokerage record (owner 'spouse') -- both must
        resolve via the manual-confirm selectbox, both must apply to the
        snapshot, and both must persist to the ledger/owner_map without the
        brokerage loop (which runs first) being overwritten by the later
        Koinly loop, or vice versa."""
        hh = _stub_hh()
        ytd = YTDSnapshot()
        mock_st = _make_mock_st(ytd)
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st.text_input.return_value = str(tmp_path)

        # Neither owner_key is in the (empty) learned map yet, so both loops
        # fall into the "no recognized owner" branch and consult the manual
        # confirm selectbox. Route by the selectbox label so the brokerage
        # account resolves to "spouse" and the Koinly report resolves to
        # "you" within the SAME render.
        def _selectbox_router(label, *args, **kwargs):
            if "account" in label.lower():
                return "spouse"
            if "koinly" in label.lower():
                return "you"
            raise AssertionError(f"unexpected selectbox call: {label!r}")

        mock_st.selectbox.side_effect = _selectbox_router

        result = PdfImportResult(
            brokerage_records=[_brokerage_record("A1", "jane r cirba", interest=20.0, dividends=8.0)],
            koinly_reports=[_koinly_report("claude r cirba", 100.0, 200.0, 50.0)],
        )
        ledger_path = tmp_path / ".pdf_import_ledger.json"
        owner_map_path = tmp_path / ".pdf_owner_map.json"
        overrides_path = tmp_path / ".statement_account_overrides.json"
        self._run_scan(hh, mock_st, result, ledger_path, owner_map_path, overrides_path, tmp_path, monkeypatch)

        # Both loops applied their fields to the SAME snapshot in one render.
        final_snap = mock_st.session_state.ytd_snapshot
        assert final_snap.interest_ytd == pytest.approx(20.0)
        assert final_snap.ordinary_dividends_ytd == pytest.approx(8.0)
        assert final_snap.crypto_stcg_ytd == pytest.approx(100.0)
        assert final_snap.crypto_ltcg_ytd == pytest.approx(200.0)
        assert final_snap.crypto_income_ytd == pytest.approx(50.0)

        # Both owners persisted to the on-disk ledger -- neither loop clobbered
        # the other's writes (brokerage under "spouse", Koinly under "you").
        import engine.pdf_ledger as ledger_mod
        import engine.pdf_owner as owner_mod

        with (
            patch.object(ledger_mod, "_LEDGER_PATH", ledger_path),
            patch.object(owner_mod, "_OWNER_MAP_PATH", owner_map_path),
        ):
            persisted_ledger = ledger_mod.load_ledger()
            persisted_owner_map = owner_mod.load_owner_map()

        assert set(persisted_ledger["brokerage"].keys()) == {"spouse"}
        assert set(persisted_ledger["koinly"].keys()) == {"you"}
        assert persisted_owner_map.get("jane r cirba") == "spouse"
        assert persisted_owner_map.get("claude r cirba") == "you"
