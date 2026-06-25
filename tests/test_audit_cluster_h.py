"""Regression tests for cluster H audit fixes (F32, F22, F56, F54).

All findings confirmed 5/5 by double-round adversarial verification
(wf_455d116a-7ce, 2026-06-24). Uses inspect.getsource() for static
assertions that avoid Streamlit runtime requirements.
"""

import inspect

# ---------------------------------------------------------------------------
# F32 — QCD age gate corrected to >= 70 (IRC §408(d)(8)(B): 70½ minimum)
# ---------------------------------------------------------------------------


class TestQcdAgeGateF32:
    """F32: QCD input render guard changed from >= 75 to >= 70."""

    def test_qcd_render_guard_uses_70_not_75(self):
        import views.planner as planner_mod

        src = inspect.getsource(planner_mod)
        # Old binding guard (wrong) must not appear
        assert "ya >= 75" not in src, "QCD render guard still uses >= 75; should be >= 70"
        assert "sa >= 75" not in src, "Spouse QCD render guard still uses >= 75; should be >= 70"

    def test_qcd_render_guard_uses_70(self):
        import views.planner as planner_mod

        src = inspect.getsource(planner_mod)
        assert "ya >= 70" in src, "QCD render guard should use ya >= 70"
        assert "sa >= 70" in src, "Spouse QCD render guard should use sa >= 70"

    def test_dead_qcd_flags_removed(self):
        import views.planner as planner_mod

        src = inspect.getsource(planner_mod)
        # The dead intermediate flags at 71 should be gone
        assert "qcd_ok = ya >= 71" not in src, "Dead qcd_ok = ya >= 71 still present"
        assert "sp_qcd_ok = sa >= 71" not in src, "Dead sp_qcd_ok = sa >= 71 still present"

    def test_irc_citation_present(self):
        import views.planner as planner_mod

        src = inspect.getsource(planner_mod)
        assert "408(d)(8)(B)" in src, "IRC §408(d)(8)(B) citation missing from QCD guard"


# ---------------------------------------------------------------------------
# F22 — ACA full-premium hline uses effective_benchmark not module constant
# ---------------------------------------------------------------------------


class TestAcaPremiumAnnotationF22:
    """F22: add_hline y= uses effective_benchmark scaled by num_on_aca."""

    def test_hline_does_not_hardcode_benchmark_annual(self):
        import views.aca_irmaa as aca_mod

        src = inspect.getsource(aca_mod)
        # The old pattern was add_hline(y=BENCHMARK_PREMIUM_ANNUAL, ...)
        # After fix, neither the y= nor annotation_text should use the raw constant.
        # We look for the pattern of passing it directly to add_hline.
        assert "y=BENCHMARK_PREMIUM_ANNUAL" not in src, (
            "add_hline still uses BENCHMARK_PREMIUM_ANNUAL as y-value directly"
        )

    def test_hline_uses_effective_benchmark(self):
        import views.aca_irmaa as aca_mod

        src = inspect.getsource(aca_mod)
        assert "effective_benchmark" in src, "effective_benchmark not computed in aca_irmaa view"
        assert "y=effective_benchmark" in src, "add_hline y= does not use effective_benchmark"

    def test_num_on_aca_computed_in_view(self):
        import views.aca_irmaa as aca_mod

        src = inspect.getsource(aca_mod)
        assert "num_on_aca" in src, "num_on_aca not computed in aca_irmaa view"


# ---------------------------------------------------------------------------
# F56 — ACA advisory checks both spouses for Medicare age
# ---------------------------------------------------------------------------


class TestAcaSpouseMedicareAdvisoryF56:
    """F56: not-enrolled branch checks your_age >= 65 OR spouse_age >= 65."""

    def test_advisory_checks_both_ages(self):
        import views.aca_irmaa as aca_mod

        src = inspect.getsource(aca_mod)
        # Old single check
        assert "if hh.your_age >= 65:" not in src, (
            "Advisory still only checks your_age >= 65 (missing spouse check)"
        )

    def test_advisory_includes_spouse_age_check(self):
        import views.aca_irmaa as aca_mod

        src = inspect.getsource(aca_mod)
        assert "hh.spouse_age >= 65" in src, "Advisory does not check hh.spouse_age >= 65"


# ---------------------------------------------------------------------------
# F54 — Comparator survivor note uses indexed bracket constants, not hardcoded
# ---------------------------------------------------------------------------


class TestComparatorBracketCeilingsF54:
    """F54: Survivor note bracket ceilings come from engine.tax, not hardcodes."""

    def test_no_hardcoded_50k_string(self):
        import views.comparator as comp_mod

        src = inspect.getsource(comp_mod)
        assert "$50K" not in src, "Hardcoded '$50K' still present in comparator.py"

    def test_no_hardcoded_101k_string(self):
        import views.comparator as comp_mod

        src = inspect.getsource(comp_mod)
        assert "$101K" not in src, "Hardcoded '$101K' still present in comparator.py"

    def test_brackets_imported_from_engine(self):
        import views.comparator as comp_mod

        src = inspect.getsource(comp_mod)
        assert "BRACKETS_SINGLE" in src, "BRACKETS_SINGLE not imported/used in comparator.py"
        assert "BRACKETS_MFJ" in src, "BRACKETS_MFJ not imported/used in comparator.py"

    def test_bracket_values_are_correct(self):
        from engine.tax import BRACKETS_MFJ, BRACKETS_SINGLE

        # Verify the constants have expected 12% bracket ceilings (sanity check)
        assert BRACKETS_SINGLE[1][0] == 50_400, (
            f"BRACKETS_SINGLE[1][0] unexpected: {BRACKETS_SINGLE[1][0]}"
        )
        assert BRACKETS_MFJ[1][0] == 100_800, f"BRACKETS_MFJ[1][0] unexpected: {BRACKETS_MFJ[1][0]}"
