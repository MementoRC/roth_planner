"""Per-owner store for non-ledger YTD fields (issue #496, bridge v5 plan A1-A3).

The four owned household fields (wages, federal withholding, qualified
dividends, NQO exercise) become DERIVED SUMS of ``YTDSnapshot.by_owner``
whenever that store is non-empty. An empty store is the legacy
"unattributed" shape and behaves exactly as before.
"""

from __future__ import annotations

import pytest

from models.ytd_income import OWNED_YTD_FIELDS, YTD_OWNERS, YTDSnapshot


def _attributed() -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={
            "you": {
                "wages_ytd": 100_000.0,
                "federal_withholding_ytd": 7_700.0,
                "qualified_dividends_ytd": 7.0,
                "nqo_exercise_ytd": 0.0,
            },
            "spouse": {
                "wages_ytd": 77_000.0,
                "federal_withholding_ytd": 20_000.0,
                "qualified_dividends_ytd": 4_000.0,
                "nqo_exercise_ytd": 50_000.0,
            },
        }
    )


class TestYTDByOwnerModel:
    def test_owned_fields_exclude_ira_conversions(self):
        # R1: conversions are already per-person by field name.
        assert OWNED_YTD_FIELDS == (
            "wages_ytd",
            "federal_withholding_ytd",
            "qualified_dividends_ytd",
            "nqo_exercise_ytd",
        )
        assert YTD_OWNERS == ("you", "spouse")

    def test_empty_by_owner_is_legacy_and_totals_are_stored(self):
        ytd = YTDSnapshot(wages_ytd=100_000)
        assert ytd.wages_ytd == 100_000
        assert ytd.by_owner == {}

    def test_by_owner_totals_are_derived_sums(self):
        ytd = _attributed()
        assert ytd.wages_ytd == 177_000
        assert ytd.federal_withholding_ytd == 27_700
        assert ytd.qualified_dividends_ytd == 4_007
        assert ytd.nqo_exercise_ytd == 50_000

    def test_by_owner_overrides_conflicting_stored_total(self):
        ytd = YTDSnapshot(
            wages_ytd=1.0,
            by_owner={"you": {"wages_ytd": 100_000.0}, "spouse": {"wages_ytd": 77_000.0}},
        )
        assert ytd.wages_ytd == 177_000

    def test_attribute_unowned_moves_totals_into_one_slot(self):
        legacy = YTDSnapshot(
            wages_ytd=100_000,
            federal_withholding_ytd=9_000,
            qualified_dividends_ytd=500,
            nqo_exercise_ytd=12_000,
        )
        attributed = legacy.attribute_unowned("you")
        assert attributed.by_owner == {
            "you": {
                "wages_ytd": 100_000.0,
                "federal_withholding_ytd": 9_000.0,
                "qualified_dividends_ytd": 500.0,
                "nqo_exercise_ytd": 12_000.0,
            }
        }
        for f in OWNED_YTD_FIELDS:
            assert getattr(attributed, f) == getattr(legacy, f)
        # original untouched
        assert legacy.by_owner == {}
        # second call is a no-op
        assert attributed.attribute_unowned("spouse") == attributed

    def test_replace_owner_values_is_idempotent(self):
        base = _attributed()
        incoming = {"wages_ytd": 90_000.0, "nqo_exercise_ytd": 1_000.0}
        once = base.replace_owner_values("spouse", incoming)
        twice = once.replace_owner_values("spouse", incoming)
        assert once == twice
        assert once.wages_ytd == 190_000
        # replace, never add: fields absent from the incoming values become 0
        assert once.by_owner["spouse"]["federal_withholding_ytd"] == 0.0
        assert once.federal_withholding_ytd == 7_700

    def test_replace_owner_values_leaves_other_owner_untouched(self):
        base = _attributed()
        after = base.replace_owner_values("spouse", {"wages_ytd": 1.0})
        assert after.by_owner["you"] == base.by_owner["you"]
        # the original object is not mutated either
        assert base.by_owner["spouse"]["wages_ytd"] == 77_000.0

    def test_replace_owner_values_on_unattributed_snapshot_raises(self):
        with pytest.raises(ValueError, match="attribute_unowned"):
            YTDSnapshot(wages_ytd=5.0).replace_owner_values("spouse", {"wages_ytd": 1.0})

    def test_overlay_owned_field_without_owner_raises_when_attributed(self):
        with pytest.raises(ValueError, match="owner"):
            _attributed().overlay(wages_ytd=1.0)

    def test_overlay_unowned_fields_unchanged_behaviour(self):
        ytd = _attributed()
        after = ytd.overlay(snapshot_date="2026-09-30", interest_ytd=12.0)
        assert after.snapshot_date == "2026-09-30"
        assert after.interest_ytd == 12.0
        assert after.by_owner == ytd.by_owner
        assert after.wages_ytd == 177_000

    def test_overlay_on_legacy_snapshot_is_plain_replace(self):
        after = YTDSnapshot(wages_ytd=10.0).overlay(wages_ytd=20.0)
        assert after.wages_ytd == 20.0
        assert after.by_owner == {}

    def test_by_owner_rejects_unknown_owner_key(self):
        with pytest.raises(ValueError, match="household"):
            YTDSnapshot(by_owner={"household": {"wages_ytd": 1.0}})

    def test_by_owner_rejects_unknown_field_name(self):
        with pytest.raises(ValueError, match="ira_conversions_ytd"):
            YTDSnapshot(by_owner={"you": {"ira_conversions_ytd": 1.0}})

    def test_set_owned_in_place_updates_slot_and_totals(self):
        ytd = _attributed()
        ytd.set_owned("you", nqo_exercise_ytd=3_000.0)
        assert ytd.by_owner["you"]["nqo_exercise_ytd"] == 3_000.0
        assert ytd.nqo_exercise_ytd == 53_000

    def test_set_owned_on_legacy_snapshot_assigns_total(self):
        ytd = YTDSnapshot(nqo_exercise_ytd=5.0)
        ytd.set_owned("you", nqo_exercise_ytd=3_000.0)
        assert ytd.nqo_exercise_ytd == 3_000.0
        assert ytd.by_owner == {}

    def test_set_owned_does_not_leak_into_a_replaced_copy(self):
        ytd = _attributed()
        copy = ytd.overlay(interest_ytd=1.0)
        ytd.set_owned("you", wages_ytd=0.0)
        assert copy.by_owner["you"]["wages_ytd"] == 100_000.0


class TestYTDByOwnerWriters:
    def _exercises(self, spread: float):
        from engine.portfolio_sync.shapes import OptionExercisesSnapshot

        return OptionExercisesSnapshot(server_available=True, total_spread=spread, by_grant_id={})

    def test_apply_option_exercises_writes_owner_slot_when_attributed(self):
        from engine.portfolio_sync.exercises import apply_option_exercises

        ytd = YTDSnapshot(
            by_owner={"you": {"nqo_exercise_ytd": 0.0}, "spouse": {"nqo_exercise_ytd": 0.0}}
        )
        apply_option_exercises(ytd, self._exercises(24_000.0), None, owner="you")  # type: ignore[arg-type]
        assert ytd.by_owner["you"]["nqo_exercise_ytd"] == 24_000.0
        assert ytd.by_owner["spouse"]["nqo_exercise_ytd"] == 0.0
        assert ytd.nqo_exercise_ytd == 24_000.0

    def test_apply_option_exercises_legacy_snapshot_unchanged(self):
        from engine.portfolio_sync.exercises import apply_option_exercises

        ytd = YTDSnapshot(nqo_exercise_ytd=5.0)
        apply_option_exercises(ytd, self._exercises(24_000.0), None)  # type: ignore[arg-type]
        assert ytd.nqo_exercise_ytd == 24_000.0
        assert ytd.by_owner == {}

    def test_apply_ubs_exercise_income_writes_owner_slot_when_attributed(self):
        from datetime import date

        from engine.ubs_activity_csv import federal_withholding
        from engine.ubs_exercise_ytd import apply_ubs_exercise_income
        from tests.test_ubs_exercise_ytd import _make_order

        order = _make_order("FA1", date(2026, 9, 1), bargain_element=10_000.0, fees=5.0)
        ytd = YTDSnapshot(
            by_owner={
                "you": {"nqo_exercise_ytd": 0.0, "federal_withholding_ytd": 100.0},
                "spouse": {"nqo_exercise_ytd": 7.0, "federal_withholding_ytd": 900.0},
            }
        )
        apply_ubs_exercise_income(ytd, [order], owner="you")
        assert ytd.by_owner["you"]["nqo_exercise_ytd"] == order.bargain_element
        # Redesign PR 1b: UBS withholding is a separate component ADDED to the base.
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == 100.0
        assert ytd.ubs_withholding_by_owner == {"you": federal_withholding(order)}
        assert ytd.by_owner["spouse"] == {"nqo_exercise_ytd": 7.0, "federal_withholding_ytd": 900.0}
        assert ytd.federal_withholding_ytd == 100.0 + federal_withholding(order) + 900.0

    def test_no_direct_assignment_to_owned_ytd_fields(self):
        """Only models/ytd_income.py may assign an owned field directly -- any
        other writer would bypass by_owner and be silently recomputed away."""
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        offenders: list[str] = []
        for top in ("engine", "views"):
            for path in (root / top).rglob("*.py"):
                tree = ast.parse(path.read_text(), filename=str(path))
                for node in ast.walk(tree):
                    targets: list[ast.expr] = []
                    if isinstance(node, ast.Assign):
                        targets = list(node.targets)
                    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                        targets = [node.target]
                    for t in targets:
                        if isinstance(t, ast.Attribute) and t.attr in OWNED_YTD_FIELDS:
                            offenders.append(f"{path.relative_to(root)}:{node.lineno} .{t.attr}")
        assert offenders == []

    @pytest.mark.parametrize(
        ("state", "expected"),
        [({}, "you"), ({"instance_owner": None}, "you"), ({"instance_owner": "spouse"}, "spouse")],
    )
    def test_operator_slot(self, monkeypatch, state, expected):
        import views._shared as shared

        monkeypatch.setattr(shared.st, "session_state", dict(state))
        monkeypatch.setattr(shared, "load_instance_owner", lambda: None)
        assert shared.operator_slot() == expected

    def test_operator_slot_defaults_to_you_on_corrupt_identity(self, monkeypatch):
        import views._shared as shared
        from engine.instance_identity import CorruptInstanceOwnerError

        def _corrupt():
            raise CorruptInstanceOwnerError("x", ValueError("bad"))

        monkeypatch.setattr(shared.st, "session_state", {})
        monkeypatch.setattr(shared, "load_instance_owner", _corrupt)
        assert shared.operator_slot() == "you"


class TestYTDByOwnerSerialization:
    def test_ytd_dict_round_trip_preserves_by_owner(self):
        from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict

        original = _attributed()
        restored = ytd_from_dict(ytd_to_dict(original))
        assert restored == original
        assert restored.by_owner == original.by_owner
        assert restored.wages_ytd == 177_000

    def test_legacy_snapshot_serialises_without_by_owner_key(self):
        from engine.portfolio_sync.ytd import ytd_to_dict

        # Unattributed caches stay byte-identical to the pre-#496 format.
        assert "by_owner" not in ytd_to_dict(YTDSnapshot(wages_ytd=5.0))

    def test_legacy_cache_without_by_owner_loads_unattributed(self):
        from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict

        legacy = YTDSnapshot(wages_ytd=100_000, federal_withholding_ytd=9_000)
        data = ytd_to_dict(legacy)
        restored = ytd_from_dict(data)
        assert restored.by_owner == {}  # R5: no silent migration on load
        assert ytd_to_dict(restored) == data

    def test_malformed_by_owner_entries_are_dropped_not_fatal(self):
        from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict

        data = ytd_to_dict(YTDSnapshot(interest_ytd=42.0))
        data["by_owner"] = {
            "you": {"wages_ytd": "100000", "nqo_exercise_ytd": "not-a-number", "bogus": 1.0},
            "spouse": "not-a-dict",
            "household": {"wages_ytd": 5.0},
        }
        restored = ytd_from_dict(data)
        assert restored.by_owner == {"you": {"wages_ytd": 100_000.0}}
        assert restored.wages_ytd == 100_000.0
        assert restored.interest_ytd == 42.0

    def test_non_dict_by_owner_is_dropped(self):
        from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict

        data = ytd_to_dict(YTDSnapshot(wages_ytd=7.0))
        data["by_owner"] = ["garbage"]
        restored = ytd_from_dict(data)
        assert restored.by_owner == {}
        assert restored.wages_ytd == 7.0
