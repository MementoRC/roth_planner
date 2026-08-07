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
        """fetch_portfolio must be unreachable BEFORE the is_pyodide() guard.

        Static assertion: confirms the guard is not accidentally removed and that
        fetch_portfolio cannot be reached on Pyodide even without executing Streamlit.

        Post-W2-Part-B refactor: the actual ``fetch_portfolio(`` call now lives in
        ``sync_portfolio_from_finextract`` (extracted so ``views._shared.
        sync_everything`` can reuse it). Post-Task-6 (ui-shell-theme-toggle plan):
        the ``is_pyodide()`` guard + the ``sync_portfolio_from_finextract(`` call
        both moved out of ``render_portfolio_tab`` into
        ``views/setup/_partials.py:render_portfolio_partial`` — this test now
        inspects that partial's source instead.
        """
        import inspect

        from views.setup import portfolio as portfolio_mod
        from views.setup._partials import render_portfolio_partial

        source = inspect.getsource(render_portfolio_partial)
        guard_pos = source.find("is_pyodide()")
        sync_call_pos = source.find("sync_portfolio_from_finextract(")
        assert guard_pos != -1, "is_pyodide() guard not found in render_portfolio_partial()"
        assert sync_call_pos != -1, (
            "sync_portfolio_from_finextract( call not found in render_portfolio_partial()"
        )
        assert guard_pos < sync_call_pos, (
            "sync_portfolio_from_finextract() appears before is_pyodide() guard — "
            "sync block is not properly gated on Pyodide"
        )

        helper_source = inspect.getsource(portfolio_mod.sync_portfolio_from_finextract)
        assert "fetch_portfolio(" in helper_source, (
            "fetch_portfolio( call not found in sync_portfolio_from_finextract()"
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

    def test_pdf_1040_import_called_in_the_joint_sub(self):
        """_render_pdf_1040_import must be called inside render() (joint sub-tab context)."""
        import inspect

        from views import setup

        # Post-refactor: Joint sub-tab moved into render_parameters_tab (PR #145).
        source = inspect.getsource(setup.render_parameters_tab)
        assert "_render_pdf_1040_import()" in source, (
            "_render_pdf_1040_import() not called in render_parameters_tab — widget not wired up"
        )
        # Task 7 (ui-shell-theme-toggle): the prior-year-MAGI anchor (and the rest of
        # the Joint tab's assumptions widgets) moved into
        # views.setup._partials._assumptions.render_assumptions_partial, called
        # once from here — this test now pins THAT call's position relative to
        # _render_pdf_1040_import() instead of the (no-longer-present) private
        # helper name.
        assumptions_pos = source.find("render_assumptions_partial(")
        pdf_pos = source.find("_render_pdf_1040_import()")
        assert assumptions_pos != -1
        assert pdf_pos != -1
        assert assumptions_pos < pdf_pos, (
            "_render_pdf_1040_import() appears before render_assumptions_partial() — "
            "expected PDF import to follow the assumptions widgets"
        )


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

    def test_widget_renders_before_subtabs(self):
        """The filing-status radio (rendered by ``render_household_partial(hh, st,
        "joint")``, extracted from ``render_parameters_tab`` in Task 3) must still
        run before the Me/Spouse/Joint sub-tabs are created, so spouse state can be
        zeroed in the same render pass."""
        import inspect

        from views import setup
        from views.setup._partials import render_household_partial

        tab_source = inspect.getsource(setup.render_parameters_tab)
        partial_call_pos = tab_source.find('render_household_partial(hh, st, "joint")')
        tabs_pos = tab_source.find("st.tabs(")
        assert partial_call_pos != -1, (
            'render_household_partial(hh, st, "joint") call not found in render_parameters_tab'
        )
        assert tabs_pos != -1
        assert partial_call_pos < tabs_pos, (
            "Filing status must render before the Me/Spouse/Joint sub-tabs so spouse "
            "state can be zeroed in the same render pass"
        )

        partial_source = inspect.getsource(render_household_partial)
        assert '"Filing status"' in partial_source, (
            "Filing status radio not found in render_household_partial's 'joint' branch"
        )

    def test_filing_status_written_to_session_state(self):
        import inspect

        from views.setup._partials import render_household_partial

        source = inspect.getsource(render_household_partial)
        assert 'st.session_state["filing_status"]' in source


class TestWorkplacePlanCheckboxesInParametersTab:
    """W3: render_household_partial (extracted Task 3 from Setup / Parameters
    Me & Spouse tabs) owns the workplace-plan checkboxes."""

    def _partial_source(self) -> str:
        import inspect

        from views.setup._partials import render_household_partial

        return inspect.getsource(render_household_partial)

    def test_your_checkbox_present_after_your_age_input(self):
        source = self._partial_source()
        age_pos = source.find('"Your Age"')
        wp_pos = source.find("your_has_workplace_plan = container.checkbox(")
        assert age_pos != -1, "Your Age input not found in render_household_partial"
        assert wp_pos != -1, "your_has_workplace_plan checkbox not found in 'your' branch"
        assert age_pos < wp_pos, "Workplace-plan checkbox must render after Your Age input"

    def test_spouse_checkbox_present_after_spouse_age_input(self):
        source = self._partial_source()
        age_pos = source.find('"Spouse Age"')
        wp_pos = source.find("spouse_has_workplace_plan = container.checkbox(")
        assert age_pos != -1, "Spouse Age input not found in render_household_partial"
        assert wp_pos != -1, "spouse_has_workplace_plan checkbox not found in 'spouse' branch"
        assert age_pos < wp_pos, "Workplace-plan checkbox must render after Spouse Age input"

    def test_checkboxes_write_session_state(self):
        source = self._partial_source()
        assert "st.session_state.your_has_workplace_plan = container.checkbox(" in source
        assert "st.session_state.spouse_has_workplace_plan = container.checkbox(" in source


class TestAppFilingStatusWiring:
    """app.py get_household must thread filing_status into Household and seed it."""

    def _app_source(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parent.parent / "app.py").read_text()

    def test_get_household_threads_the_filing_status(self):
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
    """Contract: conversion amounts are capped at each owner's IRA balance.

    ui-primary-2 (audit-0706 w2) replaced per-row number_input widgets with a
    single st.data_editor + post-edit validation in apply_conversion_grid_edits.
    The IRA-balance cap is now enforced in that helper rather than as a widget
    max_value kwarg.  These tests verify the new enforcement pattern.
    """

    def _planner_source(self) -> str:
        import inspect

        import views.planner as planner_mod

        return inspect.getsource(planner_mod)

    def test_spouse_conv_input_has_max_value(self):
        """apply_conversion_grid_edits must clamp sp_conv to spouse_ira_begin.

        Previously enforced via number_input max_value; now enforced in the
        post-edit helper.  The source must reference spouse_ira_begin in the
        clamp/block logic for sp_conv.
        """
        source = self._planner_source()
        # Locate the sp_conv block inside apply_conversion_grid_edits
        sp_block_start = source.find("# --- sp_conv:")
        assert sp_block_start != -1, (
            "sp_conv clamp block comment not found in apply_conversion_grid_edits"
        )
        sp_block = source[sp_block_start : sp_block_start + 600]
        assert "spouse_ira_begin" in sp_block, (
            "sp_conv clamp does not reference spouse_ira_begin — "
            "users can enter amounts exceeding the spouse IRA balance"
        )

    def test_spouse_conv_cap_uses_spouse_ira_begin(self):
        """sp_conv clamp must compare against spouse_ira_begin, not your_ira_begin."""
        source = self._planner_source()
        sp_block_start = source.find("# --- sp_conv:")
        sp_block = source[sp_block_start : sp_block_start + 600]
        assert "spouse_ira_begin" in sp_block, (
            "Spouse conversion clamp does not use spouse_ira_begin"
        )

    def test_your_conv_cap_uses_your_ira_begin(self):
        """Symmetry: your_conv clamp uses your_ira_begin as the cap."""
        source = self._planner_source()
        yc_block_start = source.find("# --- your_conv:")
        assert yc_block_start != -1, (
            "your_conv clamp block comment not found in apply_conversion_grid_edits"
        )
        yc_block = source[yc_block_start : yc_block_start + 600]
        assert "your_ira_begin" in yc_block, (
            "Your conversion clamp does not reference your_ira_begin"
        )

    def test_spouse_cap_is_not_greater_than_your_cap_pattern(self):
        """Both conversions follow the same owner_ira_begin clamp pattern."""
        source = self._planner_source()
        assert "your_ira_begin" in source, (
            "your_ira_begin not referenced in planner.py clamp logic"
        )
        assert "spouse_ira_begin" in source, (
            "spouse_ira_begin not referenced in planner.py clamp logic"
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

    def test_suppress_snapshot_autoload_set_after_clear(self):
        """F7: _clear_personal_session_state must set _suppress_snapshot_autoload=True.

        Static assertion: confirms the sentinel assignment is present so the
        app.py auto-load guard cannot silently re-hydrate from disk cache.
        """
        source = self._state_source()
        assert "_suppress_snapshot_autoload" in source, (
            "_clear_personal_session_state does not set _suppress_snapshot_autoload — "
            "disk cache auto-load silently undoes the Reset-to-demo"
        )

    def test_survivor_enabled_key_cleared(self):
        """F10: _survivor_enabled toggle must be cleared on Reset-to-demo.

        Without this, the survivor scenario UI remains active after reset even
        though the underlying 'survivor' data has been cleared.
        """
        source = self._state_source()
        assert "_survivor_enabled" in source, (
            "_clear_personal_session_state does not clear _survivor_enabled — "
            "survivor scenario toggle stays active after Reset to demo"
        )


class TestSuppressSnapshotAutoloadSentinel:
    """F7: app.py auto-load guard must honour _suppress_snapshot_autoload sentinel."""

    def _app_source(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parent.parent / "app.py").read_text()

    def test_autoload_guard_checks_sentinel(self):
        """app.py must gate portfolio_snapshot auto-load on the sentinel flag."""
        text = self._app_source()
        assert "_suppress_snapshot_autoload" in text, (
            "app.py does not check _suppress_snapshot_autoload — "
            "disk cache auto-load cannot be suppressed after Reset-to-demo"
        )

    def test_autoload_guard_uses_not_get(self):
        """The sentinel check must use `not st.session_state.get(...)` pattern."""
        text = self._app_source()
        assert "not st.session_state.get(\"_suppress_snapshot_autoload\")" in text, (
            "app.py sentinel check does not use the expected pattern"
        )


class TestAcaCaptionSingleFiler:
    """F13: ACA caption must omit Spouse age for Single filer."""

    def _aca_source(self) -> str:
        import inspect

        import views.aca_irmaa as mod

        return inspect.getsource(mod.render)

    def test_caption_gates_spouse_age_on_filing_status(self):
        """Caption rendering must branch on filing_status == 'Single'."""
        source = self._aca_source()
        assert "filing_status" in source, (
            "aca_irmaa render does not reference filing_status in caption logic"
        )
        assert "Single" in source, (
            "aca_irmaa render does not gate caption on 'Single' filing status"
        )

    def test_caption_single_branch_omits_spouse(self):
        """The Single branch of the caption must not include 'Spouse'."""
        source = self._aca_source()
        # Locate the _age_part conditional block
        age_part_pos = source.find("_age_part")
        assert age_part_pos != -1, "_age_part variable not found in aca_irmaa.render source"
        single_branch_start = source.find('"Single"', age_part_pos)
        assert single_branch_start != -1, "No 'Single' check found near _age_part"
        # The line immediately after the Single check should NOT contain 'Spouse'
        single_branch_end = source.find("else", single_branch_start)
        single_expr = source[single_branch_start:single_branch_end]
        assert "Spouse" not in single_expr, (
            "The Single branch of the ACA caption still includes 'Spouse'"
        )


class TestRothEligibilitySpouseGating:
    """F14: Roth eligibility spouse widgets must be hidden for Single filer."""

    def _roth_source(self) -> str:
        import inspect

        import views.roth_eligibility as mod

        return inspect.getsource(mod.render)

    def test_spouse_age_input_gated_on_not_single(self):
        """Spouse Age read-only display must be inside a `filing != 'Single'` guard.

        W1 (Command Center redirect) converted the Spouse Age input from an
        editable ``st.number_input`` to a read-only ``render_canonical_field``
        display; it must stay gated behind ``filing != "Single"`` so a Single
        filer never sees a spouse row.
        """
        source = self._roth_source()
        # Find the guard and the spouse_age display; guard must precede display
        guard_pos = source.find('filing != "Single"')
        assert guard_pos != -1, 'No `filing != "Single"` guard found in roth_eligibility.render'
        spouse_age_pos = source.find('"Spouse Age (end of tax year)"')
        assert spouse_age_pos != -1, "Spouse Age display not found in roth_eligibility.render"
        assert guard_pos < spouse_age_pos, (
            "Spouse Age display appears before the `filing != 'Single'` guard"
        )

    def test_spouse_workplace_field_gated_on_not_single(self):
        """Spouse workplace field must be inside a `filing != 'Single'` guard.

        W3 (Command Center redirect) converted the Spouse workplace-plan
        checkbox to a read-only ``render_canonical_field`` display; it must
        stay gated behind ``filing != "Single"`` so a Single filer never sees
        a spouse row. (Inverted from the pre-W3 checkbox-presence assertion —
        the checkbox itself no longer exists; see TestNoWorkplaceCheckboxOnEligibility.)
        """
        source = self._roth_source()
        guard_pos = source.find('filing != "Single"')
        spouse_wp_pos = source.find('"Spouse has a workplace plan"')
        assert spouse_wp_pos != -1, "Spouse workplace field not found"
        assert guard_pos < spouse_wp_pos, (
            "Spouse workplace field appears before the `filing != 'Single'` guard"
        )

    def test_spouse_trad_contrib_gated_on_not_single(self):
        """Spouse Trad IRA contribution input must be inside a `filing != 'Single'` guard."""
        source = self._roth_source()
        guard_pos = source.find('filing != "Single"')
        spouse_contrib_pos = source.find('"Spouse Trad IRA contribution (this year)"')
        assert spouse_contrib_pos != -1, "Spouse Trad IRA contribution input not found"
        assert guard_pos < spouse_contrib_pos, (
            "Spouse Trad IRA contribution input appears before the `filing != 'Single'` guard"
        )

    def test_spouse_excluded_from_persons_list_for_single(self):
        """Spouse must not be appended to the persons iteration list for Single filer."""
        source = self._roth_source()
        # The persons list construction must gate Spouse append on filing != Single
        persons_pos = source.find("persons = [")
        assert persons_pos != -1, "persons list not found in roth_eligibility.render"
        append_pos = source.find('persons.append', persons_pos)
        assert append_pos != -1, "persons.append not found after persons list"
        guard_pos = source.find('filing != "Single"', persons_pos)
        assert guard_pos != -1, "no `filing != 'Single'` guard found before Spouse append"
        assert guard_pos < append_pos, (
            "Spouse is appended to persons list without a `filing != 'Single'` guard"
        )


class TestNoDataMsg:
    """Unit tests for views.setup._partials._no_data_msg (U4/U5/U13).

    Moved from ``views.setup.portfolio`` into ``views.setup._partials`` as
    part of Task 6 of the ui-shell-theme-toggle plan (co-located with the
    accounts/holdings table helpers that use it).

    Post Task-6b (package split): ``_no_data_msg`` now lives in the
    ``_partials`` package's ``_portfolio`` submodule, so these tests patch
    that submodule directly rather than the package's ``__init__.py``
    re-export — ``_no_data_msg``'s internal call to ``is_pyodide()``
    resolves against its own defining module's globals, not the package
    namespace.
    """

    def test_pyodide_true_returns_the_upload_message(self, monkeypatch: pytest.MonkeyPatch):
        import views.setup._partials._portfolio as mod

        monkeypatch.setattr(mod, "is_pyodide", lambda: True)
        msg = mod._no_data_msg("accounts")
        assert "upload a data file" in msg
        assert "Sync button" not in msg

    def test_pyodide_false_returns_sync_message(self, monkeypatch: pytest.MonkeyPatch):
        import views.setup._partials._portfolio as mod

        monkeypatch.setattr(mod, "is_pyodide", lambda: False)
        msg = mod._no_data_msg("holdings")
        assert "Sync button" in msg
        assert "upload a data file" in msg

    def test_noun_interpolated(self, monkeypatch: pytest.MonkeyPatch):
        import views.setup._partials._portfolio as mod

        monkeypatch.setattr(mod, "is_pyodide", lambda: False)
        assert "widgets" in mod._no_data_msg("widgets")


class TestClampWidgetBounds:
    """C4 regression: _clamp keeps out-of-bounds cached/uploaded JSON from crashing
    st.number_input at render, without corrupting legitimate large/past values.

    ``_clamp`` moved from ``views.setup.parameters`` to
    ``views.setup._partials._assumptions`` as of Task 7 of the
    ui-shell-theme-toggle plan (its only callers — growth_rate/
    living_expenses/ACA-benchmark/etc., the prior-year-MAGI anchor, the
    survivor-scenario expander, and the inherited-IRAs expander — all moved
    there too), so these tests import it from its new home.
    """

    def test_in_range_unchanged(self) -> None:
        from views.setup._partials._assumptions import _clamp

        assert _clamp(500, 0, 1000) == 500

    def test_above_hi_clamped(self) -> None:
        from views.setup._partials._assumptions import _clamp

        assert _clamp(1500, 0, 1000) == 1000

    def test_below_lo_clamped(self) -> None:
        from views.setup._partials._assumptions import _clamp

        assert _clamp(-5, 0, 1000) == 0

    def test_preserves_int_type(self) -> None:
        from views.setup._partials._assumptions import _clamp

        r = _clamp(5, 0, 10)
        assert isinstance(r, int)
        assert r == 5

    def test_preserves_float_type(self) -> None:
        from views.setup._partials._assumptions import _clamp

        r = _clamp(2434.80, 0.0, 5000.0)
        assert isinstance(r, float)
        assert r == 2434.80

    def test_legitimate_large_magi_is_not_corrupted(self) -> None:
        # $2.5M filed MAGI is legitimate for a large-IRA household; the widened bound
        # keeps it intact (a pure clamp-to-$2M would corrupt the IRMAA anchor).
        from views.setup._partials._assumptions import _clamp

        assert _clamp(2_500_000, 0, 100_000_000) == 2_500_000

    def test_past_inherited_year_not_corrupted(self) -> None:
        # Inheriting 4 years before base_year is a valid mid-drain SECURE 10-year case.
        from views.setup._partials._assumptions import _clamp

        base_year = 2026
        assert _clamp(2022, base_year - 15, base_year + 30) == 2022


class TestResetToDemoClearsAllKeys:
    """audit-0705 ui-5: _clear_personal_session_state must clear ACA, defer-RMD,
    and growth_rate keys so demo defaults re-seed correctly after a reset."""

    _PERSONAL_VALUES: dict = {
        "your_aca": True,
        "spouse_aca": True,
        "your_defer_first_rmd": True,
        "spouse_defer_first_rmd": True,
        "growth_rate": 12.0,
    }

    def _run_reset(self, monkeypatch: pytest.MonkeyPatch) -> dict:
        """Seed session_state with personal values, call reset, return state dict."""
        import views.setup._state as state_mod

        fake_state: dict = dict(self._PERSONAL_VALUES)
        # _clear_personal_session_state also sets _suppress_snapshot_autoload
        monkeypatch.setattr(state_mod.st, "session_state", fake_state)
        state_mod._clear_personal_session_state()
        return fake_state

    def test_your_aca_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """your_aca=True (personal) must not survive a reset-to-demo."""
        state = self._run_reset(monkeypatch)
        assert "your_aca" not in state, (
            "your_aca persists after _clear_personal_session_state — "
            "personal ACA enrolment leaks into the demo household"
        )

    def test_spouse_aca_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """spouse_aca=True (personal) must not survive a reset-to-demo."""
        state = self._run_reset(monkeypatch)
        assert "spouse_aca" not in state, (
            "spouse_aca persists after _clear_personal_session_state — "
            "personal ACA enrolment leaks into the demo household"
        )

    def test_your_defer_first_rmd_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """your_defer_first_rmd=True (personal) must not survive a reset-to-demo."""
        state = self._run_reset(monkeypatch)
        assert "your_defer_first_rmd" not in state, (
            "your_defer_first_rmd persists after _clear_personal_session_state"
        )

    def test_spouse_defer_first_rmd_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """spouse_defer_first_rmd=True (personal) must not survive a reset-to-demo."""
        state = self._run_reset(monkeypatch)
        assert "spouse_defer_first_rmd" not in state, (
            "spouse_defer_first_rmd persists after _clear_personal_session_state"
        )

    def test_growth_rate_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """growth_rate=12.0 (personal) must not survive a reset-to-demo."""
        state = self._run_reset(monkeypatch)
        assert "growth_rate" not in state, (
            "growth_rate persists after _clear_personal_session_state — "
            "personal growth rate (e.g. 12%) leaks into the demo projection"
        )

    def test_all_five_keys_cleared_together(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All five leaked keys must be absent in a single reset call."""
        state = self._run_reset(monkeypatch)
        leaked = [k for k in self._PERSONAL_VALUES if k in state]
        assert not leaked, f"Keys still present after reset: {leaked}"


class TestPersistenceRoundTripAudit0802:
    """audit-0802 F3/F4/F5: clearing personal state must be *persisted*, not
    silently resurrected from a stale .user_defaults.json on the next startup.

    Root cause: save_user_defaults merges incoming data ON TOP of whatever is on
    disk ({**existing, **data}), so a key that _user_defaults_from_session omits
    (because the cleared value is falsy) leaves the stale on-disk value intact.
    """

    def test_reset_to_demo_deletes_disk_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """F3: _clear_personal_session_state must remove the on-disk personal
        file, or an autosave-before-reset is reseeded on the next startup."""
        import config.loader as loader_mod
        import views.setup._state as state_mod
        from config.loader import DEFAULTS, load_defaults, save_user_defaults

        monkeypatch.chdir(tmp_path)
        # audit-0805 W1: tests/conftest.py's autouse cache-path redirect
        # fixture already patches this to a DIFFERENT tmp dir than this
        # test's own `tmp_path` -- re-target it so chdir + the relative
        # ".user_defaults.json" assertions below land in the same place.
        monkeypatch.setattr(loader_mod, "_USER_DEFAULTS_PATH", tmp_path / ".user_defaults.json")
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)

        # Autosave wrote personal data to disk before the user clicked Reset.
        save_user_defaults({"your_ira": 1_700_000})
        assert (tmp_path / ".user_defaults.json").exists()

        monkeypatch.setattr(state_mod.st, "session_state", {})
        state_mod._clear_personal_session_state()

        assert not (tmp_path / ".user_defaults.json").exists(), (
            "Reset-to-demo left .user_defaults.json on disk; restart will reseed "
            "personal data (your_ira=$1.7M) instead of the demo default"
        )
        assert load_defaults()["your_ira"] == DEFAULTS["your_ira"]

    def test_cleared_survivor_not_resurrected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """F4: unchecking the survivor scenario (survivor=None) must overwrite the
        on-disk value, not leave the prior scenario to be reseeded on restart."""
        import views.setup._state as state_mod
        from config.loader import load_defaults, save_user_defaults

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)

        # 1. user configures a survivor scenario; autosave persists it
        monkeypatch.setattr(
            state_mod.st,
            "session_state",
            {"survivor": {"who_dies": "you", "death_year": 2035}},
        )
        save_user_defaults(state_mod._user_defaults_from_session())
        assert load_defaults()["survivor"] == {"who_dies": "you", "death_year": 2035}

        # 2. user unchecks it -> survivor=None; autosave runs again
        monkeypatch.setattr(state_mod.st, "session_state", {"survivor": None})
        save_user_defaults(state_mod._user_defaults_from_session())

        # 3. restart: the cleared scenario must NOT come back
        assert load_defaults().get("survivor") is None, (
            "cleared survivor scenario resurrected from stale .user_defaults.json"
        )

    def test_cleared_inherited_iras_not_resurrected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """F5: removing the last inherited IRA (inherited_iras=[]) must overwrite
        the on-disk list, not leave the distributions to be reseeded on restart."""
        import views.setup._state as state_mod
        from config.loader import load_defaults, save_user_defaults

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)

        iras = [{"balance": 50_000.0, "inherited_year": 2024, "owner": "you"}]
        monkeypatch.setattr(state_mod.st, "session_state", {"inherited_iras": iras})
        save_user_defaults(state_mod._user_defaults_from_session())
        assert load_defaults()["inherited_iras"] == iras

        monkeypatch.setattr(state_mod.st, "session_state", {"inherited_iras": []})
        save_user_defaults(state_mod._user_defaults_from_session())

        assert load_defaults().get("inherited_iras") == [], (
            "removed inherited IRA resurrected from stale .user_defaults.json"
        )


class TestApplySingleFiler:
    """C9 / ui-streamlit-4: Single-filer zeroing happens on the derived Household,
    not in session_state, so MFJ round-trips keep real spouse balances."""

    def test_single_zeroes_spouse_fields(self) -> None:
        from models.household import Household
        from views.setup.parameters import apply_single_filer

        hh = Household(
            filing_status="Single",
            spouse_ira=1_700_000,
            spouse_roth=200_000,
            spouse_age=55,
            spouse_ss_fra=2_000.0,
            spouse_aca_enrolled=True,
            spouse_has_workplace_plan=True,
            your_ira=1_700_000,
        )
        out = apply_single_filer(hh)
        assert out.spouse_ira == 0
        assert out.spouse_roth == 0
        assert out.spouse_age == 0
        assert out.spouse_ss_fra == 0.0
        assert out.spouse_aca_enrolled is False
        assert out.spouse_has_workplace_plan is False
        assert out.your_ira == 1_700_000  # your_* untouched

    def test_mfj_untouched(self) -> None:
        from models.household import Household
        from views.setup.parameters import apply_single_filer

        hh = Household(filing_status="MFJ", spouse_ira=1_700_000)
        assert apply_single_filer(hh).spouse_ira == 1_700_000


class TestSyncSsaForRecordsCandidate:
    """C2 (Command Center W2 Part C): _sync_ssa_for must record a
    FINEXTRACT_LIVE candidate via record_ss_fra_candidate instead of writing
    st.session_state["your_ss_fra"/"spouse_ss_fra"] directly — the synced
    value sits pending until confirmed through the freeze-until-confirm gate,
    same seam as your_ira/your_roth/txn_price_now. The int-coercion contract
    (StreamlitMixedNumericTypesError regression) now lives inside
    record_ss_fra_candidate itself (see TestRecordSsFraCandidate).

    _sync_ssa_for moved from views/setup/parameters.py to
    views/setup/_partials.py in Task 4 of the ui-shell-theme-toggle plan
    (render_accounts_partial's "Sync SS from FinExtract" button now calls it
    directly, avoiding a parameters.py <-> _partials.py import cycle).

    Post Task-6b (package split): ``_sync_ssa_for`` now lives in the
    ``_partials`` package's ``_accounts`` submodule, so this test patches
    that submodule directly rather than the package's ``__init__.py``
    re-export — ``_sync_ssa_for``'s internal calls resolve against its own
    defining module's globals, not the package namespace.
    """

    def _run_sync(self, monkeypatch: pytest.MonkeyPatch, owner: str) -> tuple[dict, list]:
        from types import SimpleNamespace

        import views.setup._partials._accounts as partials_mod

        fake_snap = SimpleNamespace(
            error=None,
            estimates=[SimpleNamespace(monthly_amount=2501.4)],
        )
        fake_state: dict = {}
        calls: list = []

        def _fake_record(field_key, monthly_amount, source, detail, recorded_at):  # noqa: ANN001
            calls.append((field_key, monthly_amount, source, detail))

        monkeypatch.setattr(partials_mod.st, "session_state", fake_state)
        monkeypatch.setattr(partials_mod, "fetch_ssa_snapshot", lambda: fake_snap)
        monkeypatch.setattr(
            partials_mod, "match_fra_estimate", lambda estimates, fra_age: estimates[0]
        )
        monkeypatch.setattr(partials_mod, "save_ssa_snapshot", lambda snap, *, owner: None)
        monkeypatch.setattr(partials_mod, "record_ss_fra_candidate", _fake_record)
        warning = partials_mod._sync_ssa_for(owner, 67)
        assert warning is None
        return fake_state, calls

    def test_your_ss_fra_records_candidate_not_direct_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from models.sourced import Source

        state, calls = self._run_sync(monkeypatch, "you")
        assert calls == [("your_ss_fra", 2501.4, Source.FINEXTRACT_LIVE, "SSA statement")]
        assert "your_ss_fra" not in state, (
            "_sync_ssa_for must not write your_ss_fra to session_state directly "
            "— it now records a candidate for the freeze-until-confirm gate"
        )
        assert state["ssa_snapshot_you"] is not None

    def test_spouse_ss_fra_records_candidate_not_direct_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from models.sourced import Source

        state, calls = self._run_sync(monkeypatch, "spouse")
        assert calls == [("spouse_ss_fra", 2501.4, Source.FINEXTRACT_LIVE, "SSA statement")]
        assert "spouse_ss_fra" not in state
        assert state["ssa_snapshot_spouse"] is not None
