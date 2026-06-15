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
