"""Regression test for audit-0720 finding M7.

_grant_id_substring_match's score = max(len(norm), len(raw_norm)) is constant
when raw_norm is a single custodian raw grant_number containing MULTIPLE known
household grant_ids as substrings, so it silently picks whichever household
grant happens first in dict-iteration order instead of a genuine longest
match, with no warning. When two-or-more distinct household grant_ids
genuinely tie, the ambiguity must be surfaced via exercises.warnings and NOT
silently attributed to either grant.
"""

from __future__ import annotations

from engine.portfolio_sync.exercises import apply_option_exercises
from engine.portfolio_sync.shapes import OptionExercisesSnapshot
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot


def _hh_with_grants(grants: list[StockGrant]) -> Household:
    hh = Household()
    hh.grants = grants
    return hh


class TestM7AmbiguousSubstringMatchWarns:
    def test_two_equal_length_grant_ids_both_substrings_warns_not_silently_attributed(
        self,
    ) -> None:
        grant_a = StockGrant(
            year=2019, strike=104.0, shares=650, expiry_year=2029, grant_id="ABG"
        )
        grant_b = StockGrant(
            year=2020, strike=130.0, shares=400, expiry_year=2030, grant_id="CDE"
        )
        hh = _hh_with_grants([grant_a, grant_b])
        exercises = OptionExercisesSnapshot(
            server_available=True, by_grant_id={"XABGYCDEZ": 75_000.0}
        )
        ytd = YTDSnapshot()

        apply_option_exercises(ytd, exercises, hh)

        assert exercises.warnings, "ambiguous substring match must produce a warning"
        assert exercises.by_grant_id.get("ABG", 0.0) != 75_000.0, (
            "must not silently attribute the full amount to grant 'ABG'"
        )
        assert exercises.by_grant_id.get("CDE", 0.0) != 75_000.0, (
            "must not silently attribute the full amount to grant 'CDE'"
        )

    def test_order_flip_does_not_change_ambiguous_outcome(self) -> None:
        """Flipping grant order must not change which grant silently 'wins' —
        under the fix, neither wins; both trigger a warning either way.
        """
        grant_a = StockGrant(
            year=2019, strike=104.0, shares=650, expiry_year=2029, grant_id="ABG"
        )
        grant_b = StockGrant(
            year=2020, strike=130.0, shares=400, expiry_year=2030, grant_id="CDE"
        )

        hh_forward = _hh_with_grants([grant_a, grant_b])
        exercises_forward = OptionExercisesSnapshot(
            server_available=True, by_grant_id={"XABGYCDEZ": 75_000.0}
        )
        apply_option_exercises(YTDSnapshot(), exercises_forward, hh_forward)

        hh_reversed = _hh_with_grants([grant_b, grant_a])
        exercises_reversed = OptionExercisesSnapshot(
            server_available=True, by_grant_id={"XABGYCDEZ": 75_000.0}
        )
        apply_option_exercises(YTDSnapshot(), exercises_reversed, hh_reversed)

        assert exercises_forward.by_grant_id == exercises_reversed.by_grant_id
        assert bool(exercises_forward.warnings) == bool(exercises_reversed.warnings)
