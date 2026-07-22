"""Regression tests for audit-0721 W7 findings C32, C33.

C32: views/comparator.py used two different baseline-selection rules for
     the same scenario set — the Summary Comparison table used
     scenarios[0], while the Cumulative Net Benefit chart independently
     searched for a scenario whose name contains "No Conv" (falling back to
     index 0). Fixed by extracting a shared `_baseline_index()` helper used
     by both.

C33: views/dashboard.py's Key Age Milestones loop computed
     `nb_idx = age - hh.your_age` with no lower-bound guard. When
     hh.your_age is already past a milestone age (e.g. your_age=76 with a
     milestone at 75), nb_idx went negative and Python negative indexing
     silently read a wrong (late-projection) net_benefit value. Fixed by
     skipping milestones whose age is below hh.your_age.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from streamlit.testing.v1 import AppTest

from views.comparator import _baseline_index

# ---------------------------------------------------------------------------
# C32 — views/comparator.py: unified baseline-selection rule
# ---------------------------------------------------------------------------


@dataclass
class _FakeScenario:
    """Minimal stand-in exposing only the `.name` attribute _baseline_index reads."""

    name: str


class TestC32BaselineIndex:
    def test_returns_no_conv_index_when_present(self) -> None:
        scenarios = [
            _FakeScenario("Fill to 12%"),
            _FakeScenario("No Conversion"),
            _FakeScenario("Fill to 22%"),
        ]
        assert _baseline_index(scenarios) == 1

    def test_falls_back_to_zero_when_no_conv_absent(self) -> None:
        scenarios = [
            _FakeScenario("Fill to 12%"),
            _FakeScenario("Fill to 22%"),
        ]
        assert _baseline_index(scenarios) == 0

    def test_matches_first_no_conv_when_multiple_candidates(self) -> None:
        scenarios = [
            _FakeScenario("Fill to 12%"),
            _FakeScenario("No Conv A"),
            _FakeScenario("No Conv B"),
        ]
        assert _baseline_index(scenarios) == 1


# ---------------------------------------------------------------------------
# C33 — views/dashboard.py: skip milestones before hh.your_age
# ---------------------------------------------------------------------------


class TestC33DashboardMilestoneGuard:
    def test_milestone_before_your_age_is_skipped_not_negative_indexed(self) -> None:
        def _render() -> None:
            from models.household import Household
            from views.dashboard import render

            # birth_year = 2026 - 76 = 1950 -> default_rmd_age resolves to 75
            # (outside the 1951-1959 -> 73 cohort), so your_age (76) is
            # already past the first milestone age (75) — the case C33 flags.
            hh = Household(
                your_age=76,
                spouse_age=74,
                base_year=2026,
                filing_status="Single",
                your_ira=1_700_000.0,
                spouse_ira=0.0,
            )
            render(hh)

        at = AppTest.from_function(_render)
        at.run()
        assert not at.exception, f"dashboard page crashed: {at.exception}"

        headers = [m.value for m in at.markdown if m.value.startswith("**Age ")]
        # The rmd_start_age (75) milestone is before your_age (76) -> skipped.
        assert "**Age 75**" not in headers
        # Later milestones (80, 85, 90, 95) are still in-projection -> rendered.
        for age in (80, 85, 90, 95):
            assert f"**Age {age}**" in headers, headers

    def test_all_milestones_after_your_age_render_normally(self) -> None:
        """Sanity check: the common case (your_age well before RMDs) is unaffected."""

        def _render() -> None:
            from models.household import Household
            from views.dashboard import render

            hh = Household(your_age=61, spouse_age=55, base_year=2026)
            render(hh)

        at = AppTest.from_function(_render)
        at.run()
        assert not at.exception, f"dashboard page crashed: {at.exception}"

        headers = [m.value for m in at.markdown if m.value.startswith("**Age ")]
        # your_age=61 is well before any RMD-derived milestone -> all 5 render.
        assert len(headers) == 5, headers


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
