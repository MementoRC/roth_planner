"""Tests for audit-0706 w2: portfolio table column_config and planner bracket-legend fix."""

from __future__ import annotations

import inspect


class TestPortfolioColumnConfig:
    """Verify NumberColumn formatting is present in all three portfolio table helpers."""

    def _get_source(self, func_name: str) -> str:
        """``_render_accounts_table``/``_render_holdings_table`` moved from
        ``views.setup.portfolio`` into ``views.setup._partials`` as part of
        Task 6 of the ui-shell-theme-toggle plan.
        """
        from views.setup import _partials as partials_mod

        func = getattr(partials_mod, func_name)
        return inspect.getsource(func)

    def _get_options_partial_source(self) -> str:
        """The equity-grants table moved into render_options_partial (Task 5,
        ui-shell-theme-toggle plan) — grants-specific assertions below read
        its source instead of the old views.setup.portfolio._render_grants_section.
        """
        from views.setup._partials import render_options_partial

        return inspect.getsource(render_options_partial)

    def test_accounts_table_has_market_value_format(self):
        """_render_accounts_table must include a NumberColumn for market_value."""
        src = self._get_source("_render_accounts_table")
        assert "market_value" in src
        assert "NumberColumn" in src
        assert "$%,.0f" in src

    def test_holdings_table_has_market_value_format(self):
        """_render_holdings_table must include a NumberColumn for market_value."""
        src = self._get_source("_render_holdings_table")
        assert "market_value" in src
        assert "NumberColumn" in src
        assert "$%,.0f" in src

    def test_holdings_table_has_quantity_format(self):
        """_render_holdings_table must include a NumberColumn for quantity."""
        src = self._get_source("_render_holdings_table")
        assert "quantity" in src
        assert "NumberColumn" in src
        assert "%,.0f" in src

    def test_grants_section_has_current_value_format(self):
        """render_options_partial's grants table must include a NumberColumn for current_value."""
        src = self._get_options_partial_source()
        assert "current_value" in src
        assert "NumberColumn" in src
        assert "$%,.0f" in src

    def test_column_config_kwarg_present_in_accounts_table(self):
        """_render_accounts_table must pass column_config to st.dataframe."""
        src = self._get_source("_render_accounts_table")
        assert "column_config" in src

    def test_column_config_kwarg_present_in_holdings_table(self):
        """_render_holdings_table must pass column_config to st.dataframe."""
        src = self._get_source("_render_holdings_table")
        assert "column_config" in src

    def test_column_config_kwarg_present_in_grants_section(self):
        """render_options_partial's grants table must pass column_config to st.dataframe."""
        src = self._get_options_partial_source()
        assert "column_config" in src


class TestPlannerBracketLegend:
    """Verify the bracket-usage chart uses pre-aggregated traces (one per segment)."""

    def _get_planner_source(self) -> str:
        import views.planner as planner_mod

        return inspect.getsource(planner_mod)

    def test_no_per_year_loop_adding_traces(self):
        """The inner per-segment loop inside the per-year loop must be gone.

        Old pattern: nested 'for name, val, color in segs: fig_br.add_trace(go.Bar(x=[yr.year], ...))'
        The old pattern produced up to 120 traces; new pattern produces exactly 6.
        Detect the old pattern by checking that add_trace is NOT inside a per-year loop
        that iterates over segs — specifically: 'x=[yr.year]' inside add_trace is the
        tell-tale sign of the old single-year per-call approach.
        """
        src = self._get_planner_source()
        # Old code had `x=[yr.year]` inside the Bar() call within the per-year loop.
        # New code has `x=_years` (a pre-built list). The old pattern must be gone.
        assert "x=[yr.year]" not in src, (
            "Old per-year single-x Bar trace pattern still present — "
            "legend will be broken with 120 duplicate entries"
        )

    def test_pre_aggregated_years_list_present(self):
        """A pre-built years list (_years) must be used for the x-axis of Bar traces."""
        src = self._get_planner_source()
        # New code builds _years = [yr.year for yr in conv_window] then uses x=_years
        assert "_years" in src, "Pre-aggregated _years list not found in planner source"
        assert "x=_years" in src, "Bar trace must use x=_years (pre-aggregated)"

    def test_six_segment_definitions_present(self):
        """Exactly 6 income-segment tuples must be defined (_segments list)."""
        src = self._get_planner_source()
        assert "_segments" in src, "_segments list not found in planner source"
        # Verify all 6 segment names appear
        for seg_name in ("Options", "Taxable RMD", "Taxable SS", "Your Conv", "Sp Conv", "Room (12%)"):
            assert seg_name in src, f"Segment '{seg_name}' missing from planner bracket chart"

    def test_showlegend_true_unconditional(self):
        """showlegend=True must appear in the Bar trace (no conditional per year-0 check)."""
        src = self._get_planner_source()
        assert "showlegend=True" in src, "showlegend=True not found — legend entries may be missing"
        # Old code had: showlegend=(yr == conv_window[0])
        assert "conv_window[0]" not in src or "showlegend=(yr == conv_window[0])" not in src, (
            "Old conditional showlegend pattern still present"
        )
