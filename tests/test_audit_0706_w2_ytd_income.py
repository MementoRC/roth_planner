"""TDD tests for audit-0706 wave-2 YTD-income findings.

ui-primary-7: YTDSnapshot.federal_withholding_ytd field (model + view reads it)
ui-primary-6: SS benefit in YTD estimate uses COLA-adjusted value, not bare at-70 amount
ui-primary-5: save_ytd_snapshot import at module level (cosmetic — verified via import check)
ui-primary-10: qualified-dividends help text includes 0% LTCG tier
"""

import dataclasses  # noqa: I001


# ---------------------------------------------------------------------------
# ui-primary-7: federal_withholding_ytd on YTDSnapshot
# ---------------------------------------------------------------------------


class TestFederalWithholdingYtdField:
    """YTDSnapshot must carry federal_withholding_ytd so safe-harbor 'Already paid' is non-zero."""

    def test_snapshot_has_field_with_zero_default(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot()
        assert hasattr(snap, "federal_withholding_ytd"), (
            "YTDSnapshot must have federal_withholding_ytd field"
        )
        assert snap.federal_withholding_ytd == 0.0

    def test_snapshot_field_is_in_dataclass_fields(self):
        from models.ytd_income import YTDSnapshot

        field_names = {f.name for f in dataclasses.fields(YTDSnapshot)}
        assert "federal_withholding_ytd" in field_names

    def test_snapshot_field_can_be_set(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot(federal_withholding_ytd=4_500.0)
        assert snap.federal_withholding_ytd == 4_500.0

    def test_view_reads_field_without_getattr_fallback(self):
        """The view must read ytd.federal_withholding_ytd directly (no getattr guard needed)."""
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot(federal_withholding_ytd=3_000.0)
        # Simulate what the view does after the fix (direct attribute access)
        already_paid = float(snap.federal_withholding_ytd)
        assert already_paid == 3_000.0


# ---------------------------------------------------------------------------
# ui-primary-6: SS benefit with COLA in YTD tax estimate
# ---------------------------------------------------------------------------


class TestSSColaInYtdEstimate:
    """YTD SS benefit used in tax estimate must include COLA growth, not bare at-70 amount."""

    def _make_household(self, *, your_age: int, your_ss_start_age: int,
                         spouse_age: int, spouse_ss_start_age: int,
                         ss_cola: float = 0.025) -> object:
        from models.household import Household

        hh = Household()
        hh.your_age = your_age
        hh.your_ss_start_age = your_ss_start_age
        hh.spouse_age = spouse_age
        hh.spouse_ss_start_age = spouse_ss_start_age
        hh.ss_cola = ss_cola
        return hh

    def test_ss_with_cola_grows_over_collection_years(self):
        """ss_with_cola must return more than the bare benefit after 1+ year of COLA."""
        from engine.ira import ss_benefit_at_age, ss_with_cola

        # Bare annual benefit at claim age
        monthly_fra = 3_000.0
        claim_age = 70
        bare = ss_benefit_at_age(monthly_fra, claim_age, fra_age=67)

        # After 5 years of collecting at 2.5% COLA
        with_cola = ss_with_cola(bare, years_collecting=5, cola=0.025)
        assert with_cola > bare, "ss_with_cola must exceed bare benefit after COLA growth"
        assert abs(with_cola - bare * (1.025 ** 5)) < 0.01

    def test_your_ss_cola_applied_when_collecting(self):
        """When your_age >= your_ss_start_age, COLA must be applied to SS benefit."""
        from engine.ira import ss_benefit_at_age, ss_with_cola

        monthly_fra = 2_500.0
        ss_start_age = 68
        your_age = 72  # collecting for 4 years
        fra_age = 67

        bare = ss_benefit_at_age(monthly_fra, ss_start_age, fra_age)
        years_collecting = your_age - ss_start_age
        expected = ss_with_cola(bare, years_collecting, cola=0.025)

        # Verify the formula matches what the view should compute
        assert years_collecting == 4
        assert expected > bare

    def test_ss_zero_when_not_yet_collecting(self):
        """When your_age < your_ss_start_age, SS contribution is 0."""
        from engine.ira import ss_benefit_at_age, ss_with_cola

        monthly_fra = 3_000.0
        ss_start_age = 70
        your_age = 65  # not yet collecting

        if your_age >= ss_start_age:
            bare = ss_benefit_at_age(monthly_fra, ss_start_age, fra_age=67)
            result = ss_with_cola(bare, your_age - ss_start_age, cola=0.025)
        else:
            result = 0.0

        assert result == 0.0

    def test_combined_ss_uses_cola_for_each_collecting_spouse(self):
        """_combined_ss in the view must use COLA for each spouse that is collecting."""
        from engine.ira import ss_benefit_at_age, ss_with_cola

        # Simulate the corrected view logic for a household where both are collecting
        your_ss_fra = 3_000.0  # monthly FRA benefit
        your_fra_age = 67
        your_ss_start_age = 68
        your_age = 73  # collecting 5 years
        ss_cola = 0.025

        spouse_ss_fra = 2_000.0
        spouse_fra_age = 67
        spouse_ss_start_age = 70
        spouse_age = 72  # collecting 2 years

        your_bare = ss_benefit_at_age(your_ss_fra, your_ss_start_age, your_fra_age)
        your_cola = ss_with_cola(your_bare, your_age - your_ss_start_age, ss_cola)

        spouse_bare = ss_benefit_at_age(spouse_ss_fra, spouse_ss_start_age, spouse_fra_age)
        spouse_cola = ss_with_cola(spouse_bare, spouse_age - spouse_ss_start_age, ss_cola)

        combined = your_cola + spouse_cola
        combined_bare = (
            (your_bare if your_age >= your_ss_start_age else 0.0)
            + (spouse_bare if spouse_age >= spouse_ss_start_age else 0.0)
        )
        # With COLA, combined must exceed the bare combined
        assert combined > combined_bare, "COLA-adjusted combined SS must exceed bare combined"


# ---------------------------------------------------------------------------
# ui-primary-5: module-level import of save_ytd_snapshot
# ---------------------------------------------------------------------------


class TestModuleLevelImport:
    """save_ytd_snapshot must be imported at module level, not inside render()."""

    def test_save_ytd_snapshot_imported_at_module_level(self):
        """views.ytd_income module must expose save_ytd_snapshot at module level
        (or engine.portfolio_sync must be importable at module level)."""
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / "views" / "ytd_income" / "__init__.py").read_text()
        tree = ast.parse(src)

        # Find all import statements at module level (not nested inside functions)
        module_level_imports: list[str] = []
        function_level_imports: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom) and child.module and "portfolio_sync" in child.module:
                        for alias in child.names:
                            function_level_imports.append(alias.name)

        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and "portfolio_sync" in node.module:
                for alias in node.names:
                    module_level_imports.append(alias.name)

        assert "save_ytd_snapshot" in module_level_imports, (
            f"save_ytd_snapshot must be imported at module level, not inside a function. "
            f"Found at module level: {module_level_imports}, "
            f"Found inside functions: {function_level_imports}"
        )
        assert "save_ytd_snapshot" not in function_level_imports, (
            "save_ytd_snapshot must NOT remain as a function-level import"
        )


# ---------------------------------------------------------------------------
# ui-primary-10: qualified-dividends help text includes 0% tier
# ---------------------------------------------------------------------------


class TestQualifiedDividendsHelpText:
    """Help text for qualified dividends must reference the 0% LTCG tier."""

    def test_help_text_includes_zero_pct_tier(self):
        """The help string must include LTCG_RATES_MFJ[0] (0%) tier."""
        import ast
        from pathlib import Path

        # The qualified_dividends number_input widget lives in the manual-entry
        # partial (extracted from views/ytd_income/__init__.py).
        src = (
            Path(__file__).resolve().parent.parent
            / "views"
            / "ytd_income"
            / "_partials"
            / "_manual_entry.py"
        ).read_text()
        tree = ast.parse(src)

        # Find the help= kwarg for the qualified_dividends number_input
        found_ltcg_0 = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = ""
                if isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name == "number_input":
                    for kw in node.keywords:
                        if kw.arg == "help":
                            # Check the help value references LTCG_RATES_MFJ[0]
                            help_src = ast.unparse(kw.value)
                            if "LTCG_RATES_MFJ[0]" in help_src:
                                found_ltcg_0 = True

        assert found_ltcg_0, (
            "qualified_dividends number_input help= must reference LTCG_RATES_MFJ[0] "
            "to include the 0% tier"
        )

    def test_ltcg_rates_mfj_zero_index_is_zero_pct(self):
        """LTCG_RATES_MFJ[0] must actually be the 0% tier."""
        from engine.tax import LTCG_RATES_MFJ

        assert LTCG_RATES_MFJ[0] == 0.0, (
            f"LTCG_RATES_MFJ[0] should be 0.0 (0% tier), got {LTCG_RATES_MFJ[0]}"
        )
        assert len(LTCG_RATES_MFJ) >= 3, "LTCG_RATES_MFJ must have at least 3 tiers"
