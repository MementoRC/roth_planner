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


class TestRothTablesIndexing:
    """Audit 0705 #5 — Roth per-year contribution/catch-up limits and phase-out ranges must
    CPI-index past 2026 (IRS indexes them: §219/§408A/§414(v)), not silently freeze at 2026."""

    def test_contrib_limit_exact_for_published_years(self):
        import pytest

        from views.roth_eligibility import contrib_limit_for_year

        assert contrib_limit_for_year(2025) == pytest.approx(7_000)
        assert contrib_limit_for_year(2026) == pytest.approx(7_500)

    def test_contrib_limit_indexes_past_2026(self):
        import pytest

        from views.roth_eligibility import contrib_limit_for_year

        v = contrib_limit_for_year(2031, cpi=0.025)
        assert v == pytest.approx(7_500 * 1.025**5)
        assert v > 7_500  # not frozen at the 2026 value

    def test_catchup_indexes_past_2026(self):
        import pytest

        from views.roth_eligibility import catchup_50_for_year

        v = catchup_50_for_year(2031, cpi=0.025)
        assert v == pytest.approx(1_100 * 1.025**5)
        assert v > 1_100

    def test_phaseout_exact_for_published_year(self):
        import pytest

        from views.roth_eligibility import roth_phaseout_for_year

        assert roth_phaseout_for_year(2026, "Single") == pytest.approx((153_000, 168_000))
        assert roth_phaseout_for_year(2026, "MFJ") == pytest.approx((242_000, 252_000))

    def test_phaseout_indexes_past_2026(self):
        import pytest

        from views.roth_eligibility import roth_phaseout_for_year

        low, high = roth_phaseout_for_year(2031, "MFJ", cpi=0.025)
        assert low == pytest.approx(242_000 * 1.025**5)
        assert high == pytest.approx(252_000 * 1.025**5)
        assert low > 242_000  # not frozen at the 2026 band
