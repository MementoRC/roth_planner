"""Tests for Roth eligibility 2026 constants."""

import pytest


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestRothEligibility2026Constants:
    """Regression tests pinning 2026 IRA phase-out and contribution constants.

    Source: IRS IR-2025-111 / Notice 2025-67 (Nov 13 2025).
    Pins exact boundaries so any future year-bump is a deliberate, visible change.
    """

    def test_2026_ira_deduction_mfj_active_phaseout(self):
        from views.roth_eligibility import TRAD_DEDUCTION_PHASEOUT

        lower, upper = TRAD_DEDUCTION_PHASEOUT["MFJ_active"]
        assert lower == 129_000
        assert upper == 149_000

    def test_2026_ira_deduction_single_active_phaseout(self):
        """Pins Single range to $10K wide — regression against the prior $20K bug."""
        from views.roth_eligibility import TRAD_DEDUCTION_PHASEOUT

        lower, upper = TRAD_DEDUCTION_PHASEOUT["Single"]
        assert lower == 81_000
        assert upper == 91_000
        assert upper - lower == 10_000  # statutory width

    def test_2026_roth_mfj_phaseout(self):
        from views.roth_eligibility import ROTH_PHASEOUT

        lower, upper = ROTH_PHASEOUT["MFJ"]
        assert lower == 242_000
        assert upper == 252_000

    def test_2026_roth_single_phaseout(self):
        from views.roth_eligibility import ROTH_PHASEOUT

        lower, upper = ROTH_PHASEOUT["Single"]
        assert lower == 153_000
        assert upper == 168_000

    def test_2026_ira_contribution_limit(self):
        from views.roth_eligibility import CATCHUP_50, CONTRIB_LIMIT

        assert CONTRIB_LIMIT == 7_500  # under-50 base
        assert CONTRIB_LIMIT + CATCHUP_50 == 8_600  # 50+ total


class TestPyodideGatingRothEligibility:
    """Verify the TurboTax sync block is gated behind is_pyodide() in roth_eligibility.py."""

    def test_fetch_tax_return_inside_pyodide_else_branch(self):
        """fetch_tax_return must appear AFTER the is_pyodide() guard in render() source."""
        import inspect

        import views.roth_eligibility as mod

        source = inspect.getsource(mod.render)
        guard_pos = source.find("is_pyodide()")
        fetch_pos = source.find("fetch_tax_return(")
        assert guard_pos != -1, "is_pyodide() guard not found in render()"
        assert fetch_pos != -1, "fetch_tax_return( call not found in render()"
        assert guard_pos < fetch_pos, (
            "fetch_tax_return() appears before is_pyodide() guard — "
            "sync block is not properly gated on Pyodide"
        )

    def test_sync_button_inside_pyodide_else_branch(self):
        """'Sync TurboTax Data' button must appear AFTER the is_pyodide() guard."""
        import inspect

        import views.roth_eligibility as mod

        source = inspect.getsource(mod.render)
        guard_pos = source.find("is_pyodide()")
        button_pos = source.find("Sync TurboTax Data")
        assert guard_pos != -1, "is_pyodide() guard not found in render()"
        assert button_pos != -1, "'Sync TurboTax Data' button not found in render()"
        assert guard_pos < button_pos, (
            "'Sync TurboTax Data' button appears before is_pyodide() guard — "
            "button is visible on Pyodide web build"
        )
