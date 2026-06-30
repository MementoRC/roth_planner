"""Smoke tests for views.setup module."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from models.household import Household


def test_setup_module_imports():
    """views.setup must import without error."""
    from views import setup

    assert hasattr(setup, "render")


def test_render_signature():
    """render must accept a Household and return None."""
    from views import setup

    sig = inspect.signature(setup.render)
    params = list(sig.parameters.values())
    assert len(params) == 1, "render must take exactly one positional arg"
    assert params[0].annotation is Household or params[0].annotation == "Household"


def test_render_is_callable():
    from views import setup

    assert callable(setup.render)


class TestPyodideGating:
    """Verify the FinExtract sync block is gated behind is_pyodide()."""

    def test_fetch_portfolio_inside_pyodide_else_branch(self):
        """fetch_portfolio must appear AFTER the is_pyodide() guard in setup.py source.

        Static assertion: confirms the guard is not accidentally removed and that
        fetch_portfolio cannot be reached on Pyodide even without executing Streamlit.
        """
        import inspect

        from views import setup

        # Post-refactor: the sync block lives in render_portfolio_tab (extracted
        # from the original render() Portfolio tab body in PR #145).
        source = inspect.getsource(setup.render_portfolio_tab)
        guard_pos = source.find("is_pyodide()")
        fetch_pos = source.find("fetch_portfolio(")
        assert guard_pos != -1, "is_pyodide() guard not found in render()"
        assert fetch_pos != -1, "fetch_portfolio( call not found in render()"
        assert guard_pos < fetch_pos, (
            "fetch_portfolio() appears before is_pyodide() guard — "
            "sync block is not properly gated on Pyodide"
        )


class TestPdf1040ImportHelper:
    """Smoke tests for the _render_pdf_1040_import helper."""

    def test_helper_exists_and_is_callable(self):
        """_render_pdf_1040_import must exist and be callable."""
        from views import setup

        assert hasattr(setup, "_render_pdf_1040_import")
        assert callable(setup._render_pdf_1040_import)

    def test_pyodide_gate_present_in_source(self):
        """_render_pdf_1040_import must contain is_pyodide() guard."""
        import inspect

        from views import setup

        source = inspect.getsource(setup._render_pdf_1040_import)
        assert "is_pyodide()" in source, (
            "is_pyodide() guard missing from _render_pdf_1040_import — "
            "pdfplumber would be called in the Pyodide web build"
        )

    def test_filing_status_options_complete(self):
        """_FILING_STATUS_OPTIONS must contain all four canonical statuses."""
        from views.setup import _FILING_STATUS_OPTIONS

        assert set(_FILING_STATUS_OPTIONS) == {
            "married_filing_jointly",
            "single",
            "married_filing_separately",
            "head_of_household",
        }

    def test_pdf_1040_import_called_in_joint_sub(self):
        """_render_pdf_1040_import must be called inside render() (joint sub-tab context)."""
        import inspect

        from views import setup

        # Post-refactor: Joint sub-tab moved into render_parameters_tab (PR #145).
        source = inspect.getsource(setup.render_parameters_tab)
        assert "_render_pdf_1040_import()" in source, (
            "_render_pdf_1040_import() not called in render_parameters_tab — widget not wired up"
        )
        magi_pos = source.find("_render_prior_year_magi_anchor()")
        pdf_pos = source.find("_render_pdf_1040_import()")
        assert magi_pos != -1
        assert pdf_pos != -1
        assert magi_pos < pdf_pos, (
            "_render_pdf_1040_import() appears before _render_prior_year_magi_anchor() — "
            "expected PDF import to follow the manual number_input widget"
        )


class TestMergePdfMagi:
    """Unit tests for engine.tax_return_pdf.merge_pdf_magi."""

    def _make_record(self, year: int, magi: float) -> object:
        from engine.tax_return_pdf import Form1040Record

        return Form1040Record(
            tax_year=year,
            agi=magi - 500.0,
            tax_exempt_interest=300.0,
            taxable_ss=0.0,
            qualified_dividends=0.0,
            ordinary_dividends=0.0,
            feie=200.0,
            magi=magi,
            filing_status="married_filing_jointly",
            captured_at="2026-01-01T00:00:00+00:00",
        )

    def test_absent_year_is_filled(self):
        from engine.tax_return_pdf import merge_pdf_magi

        records = {2023: self._make_record(2023, 162_000.0)}
        result = merge_pdf_magi({}, records)
        assert result[2023] == pytest.approx(162_000.0)

    def test_zero_year_is_filled(self):
        from engine.tax_return_pdf import merge_pdf_magi

        records = {2023: self._make_record(2023, 162_000.0)}
        result = merge_pdf_magi({2023: 0.0}, records)
        assert result[2023] == pytest.approx(162_000.0)

    def test_nonzero_existing_is_preserved(self):
        from engine.tax_return_pdf import merge_pdf_magi

        records = {2023: self._make_record(2023, 162_000.0)}
        result = merge_pdf_magi({2023: 175_000.0}, records)
        assert result[2023] == pytest.approx(175_000.0)

    def test_empty_records_returns_copy_of_existing(self):
        from engine.tax_return_pdf import merge_pdf_magi

        existing = {2023: 100_000.0, 2024: 120_000.0}
        result = merge_pdf_magi(existing, {})
        assert result == existing
        assert result is not existing  # must be a new dict

    def test_multiple_years_gap_fill(self):
        from engine.tax_return_pdf import merge_pdf_magi

        records = {
            2022: self._make_record(2022, 140_000.0),
            2023: self._make_record(2023, 162_000.0),
        }
        existing = {2023: 0.0}
        result = merge_pdf_magi(existing, records)
        assert result[2022] == pytest.approx(140_000.0)
        assert result[2023] == pytest.approx(162_000.0)


class TestPdfRecordRoundTrip:
    """Confirm save/load round-trip preserves filing_status and magi."""

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        from engine.tax_return_pdf import (
            _PDF_TAX_CACHE_PATH,
            Form1040Record,
            load_pdf_tax_records,
            save_pdf_tax_records,
        )

        # Patch the cache path to tmp_path
        original = _PDF_TAX_CACHE_PATH
        import engine.tax_return_pdf as _mod

        _mod._PDF_TAX_CACHE_PATH = tmp_path / ".tax_pdf_cache.json"
        try:
            rec = Form1040Record(
                tax_year=2023,
                agi=162_433.0,
                tax_exempt_interest=2_511.0,
                taxable_ss=0.0,
                qualified_dividends=500.0,
                ordinary_dividends=1_200.0,
                feie=3_000.0,
                magi=167_944.0,
                filing_status="married_filing_jointly",
                captured_at="2026-06-09T12:00:00+00:00",
            )
            save_pdf_tax_records({2023: rec})
            loaded = load_pdf_tax_records()
            assert 2023 in loaded
            assert loaded[2023].magi == pytest.approx(167_944.0)
            assert loaded[2023].filing_status == "married_filing_jointly"
            assert loaded[2023].tax_year == 2023
        finally:
            _mod._PDF_TAX_CACHE_PATH = original


class TestFilingStatusGate:
    """Gate wiring: household filing_status flows into Household via canonical values (R1 #6)."""

    def test_filing_status_from_label_canonical(self):
        from views.setup import filing_status_from_label

        assert filing_status_from_label("Single") == "Single"
        assert filing_status_from_label("Married filing jointly") == "MFJ"
        assert filing_status_from_label("anything else") == "MFJ"

    def test_gate_uses_capitalized_not_pdf_lowercase(self):
        """Engine compares == 'Single'; the gate must NOT emit the lowercase
        PDF-import vocabulary or the Single branches stay dead code (R1 #6)."""
        from views.setup import _FILING_STATUS_OPTIONS, filing_status_from_label

        assert filing_status_from_label("Single") == "Single"
        assert filing_status_from_label("Single") not in _FILING_STATUS_OPTIONS

    def test_spouse_single_overrides_zeroes_spouse(self):
        from views.setup import spouse_single_overrides

        ov = spouse_single_overrides()
        assert ov["spouse_ira"] == 0
        assert ov["spouse_roth"] == 0
        assert ov["spouse_age"] == 0
        assert ov["spouse_ss_fra"] == 0
        assert ov["spouse_aca"] is False

    def test_widget_renders_before_subtabs(self):
        import inspect

        from views import setup

        source = inspect.getsource(setup.render_parameters_tab)
        radio_pos = source.find('"Filing status"')
        tabs_pos = source.find("st.tabs(")
        assert radio_pos != -1, "Filing status radio not found in render_parameters_tab"
        assert tabs_pos != -1
        assert radio_pos < tabs_pos, (
            "Filing status must render before the Me/Spouse/Joint sub-tabs so spouse "
            "state can be zeroed in the same render pass"
        )

    def test_filing_status_written_to_session_state(self):
        import inspect

        from views import setup

        source = inspect.getsource(setup.render_parameters_tab)
        assert 'st.session_state["filing_status"]' in source


class TestAppFilingStatusWiring:
    """app.py get_household must thread filing_status into Household and seed it."""

    def _app_source(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parent.parent / "app.py").read_text()

    def test_get_household_threads_filing_status(self):
        text = self._app_source()
        assert 'filing_status=st.session_state.get("filing_status"' in text, (
            "get_household must thread filing_status from session_state into Household"
        )

    def test_seed_defaults_filing_status_mfj(self):
        text = self._app_source()
        assert 'setdefault("filing_status", "MFJ")' in text, (
            "_seed_session_state must seed filing_status default 'MFJ'"
        )


class TestPlannerSpouseConversionCap:
    """Static contract: spouse conversion number_input must have max_value capped
    at the spouse IRA balance, mirroring the 'Your Conv' input cap."""

    def _planner_source(self) -> str:
        import inspect

        import views.planner as planner_mod

        return inspect.getsource(planner_mod)

    def test_spouse_conv_input_has_max_value(self):
        """Spouse conversion number_input must include a max_value kwarg.

        Without max_value, a user can enter a conversion larger than the
        spouse IRA balance, fabricating Roth dollars that don't exist.
        """
        source = self._planner_source()
        # Locate the Spouse conversion input block
        sp_block_start = source.find("# Spouse conversion input")
        assert sp_block_start != -1, "Spouse conversion input comment not found"
        # Grab from that comment to the next blank line / else clause
        sp_block = source[sp_block_start : sp_block_start + 600]
        assert "max_value" in sp_block, (
            "Spouse conversion number_input is missing max_value — "
            "users can enter amounts exceeding the spouse IRA balance"
        )

    def test_spouse_conv_cap_uses_spouse_ira_begin(self):
        """The spouse conversion max_value must reference yr.spouse_ira_begin,
        not yr.your_ira_begin or a hardcoded constant."""
        source = self._planner_source()
        sp_block_start = source.find("# Spouse conversion input")
        sp_block = source[sp_block_start : sp_block_start + 600]
        assert "spouse_ira_begin" in sp_block, (
            "Spouse conversion max_value does not use yr.spouse_ira_begin"
        )

    def test_your_conv_cap_uses_your_ira_begin(self):
        """Symmetry check: 'Your Conv' input uses yr.your_ira_begin as cap."""
        source = self._planner_source()
        your_block_start = source.find("# Your conversion input")
        assert your_block_start != -1, "Your conversion input comment not found"
        your_block = source[your_block_start : your_block_start + 600]
        assert "your_ira_begin" in your_block, (
            "Your conversion max_value does not reference yr.your_ira_begin"
        )

    def test_spouse_cap_is_not_greater_than_your_cap_pattern(self):
        """Both conversion inputs follow the same int(yr.<owner>_ira_begin) pattern."""
        source = self._planner_source()
        assert "int(yr.your_ira_begin)" in source, (
            "Your Conv max_value pattern int(yr.your_ira_begin) not found"
        )
        assert "int(yr.spouse_ira_begin)" in source, (
            "Spouse Conv max_value pattern int(yr.spouse_ira_begin) not found"
        )


class TestClearPersonalSessionState:
    """SEC-02: _clear_personal_session_state must wipe V2 private-key session keys."""

    def _state_source(self) -> str:
        import inspect

        from views.setup import _state

        return inspect.getsource(_state._clear_personal_session_state)

    def test_data_bridge_privkey_b64_cleared(self):
        """data_bridge_privkey_b64 must be in the keys_to_clear list."""
        source = self._state_source()
        assert "data_bridge_privkey_b64" in source, (
            "_clear_personal_session_state does not clear data_bridge_privkey_b64 — "
            "V2 private key survives a Reset to demo"
        )

    def test_v2_privkey_input_cleared(self):
        """_v2_privkey_input must be in the keys_to_clear list."""
        source = self._state_source()
        assert "_v2_privkey_input" in source, (
            "_clear_personal_session_state does not clear _v2_privkey_input — "
            "the text_input widget retains the pasted key after Reset to demo"
        )

    def test_tax_return_snapshot_cleared(self):
        """tax_return_snapshot must be cleared on Reset-to-demo (M16)."""
        source = self._state_source()
        assert "tax_return_snapshot" in source, (
            "_clear_personal_session_state does not clear tax_return_snapshot — "
            "stale prior-year MAGI survives a Reset to demo"
        )

    def test_ytd_snapshot_cleared(self):
        """ytd_snapshot must be cleared on Reset-to-demo (M16)."""
        source = self._state_source()
        assert "ytd_snapshot" in source, (
            "_clear_personal_session_state does not clear ytd_snapshot — "
            "stale YTD income drives the YTD Income page after Reset to demo"
        )

    def test_ytd_toggles_cleared(self):
        """apply_ytd_to_projection and ytd_manual_entry must be cleared (M16)."""
        source = self._state_source()
        assert "apply_ytd_to_projection" in source, (
            "_clear_personal_session_state does not clear apply_ytd_to_projection"
        )
        assert "ytd_manual_entry" in source, (
            "_clear_personal_session_state does not clear ytd_manual_entry"
        )


class TestNoDataMsg:
    """Unit tests for views.setup.portfolio._no_data_msg (U4/U5/U13)."""

    def test_pyodide_true_returns_upload_message(self, monkeypatch: pytest.MonkeyPatch):
        import views.setup.portfolio as mod

        monkeypatch.setattr(mod, "is_pyodide", lambda: True)
        msg = mod._no_data_msg("accounts")
        assert "upload a data file" in msg
        assert "Sync button" not in msg

    def test_pyodide_false_returns_sync_message(self, monkeypatch: pytest.MonkeyPatch):
        import views.setup.portfolio as mod

        monkeypatch.setattr(mod, "is_pyodide", lambda: False)
        msg = mod._no_data_msg("holdings")
        assert "Sync button" in msg
        assert "upload a data file" in msg

    def test_noun_interpolated(self, monkeypatch: pytest.MonkeyPatch):
        import views.setup.portfolio as mod

        monkeypatch.setattr(mod, "is_pyodide", lambda: False)
        assert "widgets" in mod._no_data_msg("widgets")
