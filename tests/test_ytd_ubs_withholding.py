"""Redesign PR 1b: UBS option-exercise withholding is ADDED to a person's other
withholding as its own component; a re-scan replaces only that component."""

from __future__ import annotations

import math
from datetime import date

import pytest

import views.ytd_income._partials._sync_scan as sync_scan_mod
from engine.ubs_activity_csv import federal_withholding
from engine.ubs_exercise_ytd import apply_ubs_exercise_income, split_legacy_ubs_withholding
from models.ytd_income import ManualMark, YTDSnapshot
from tests.test_ubs_exercise_ytd import _make_order

A = _make_order("FA1", date(2026, 3, 1), bargain_element=10_000.37, fees=5.0)
B = _make_order("FA2", date(2026, 8, 1), bargain_element=20_000.0, fees=5.0)
WA, WB = federal_withholding(A), federal_withholding(B)
TOTALS = {
    "interest_ytd": 0.0,
    "tax_exempt_interest_ytd": 0.0,
    "ordinary_dividends_ytd": 0.0,
    "stcg_ytd": 0.0,
    "ltcg_ytd": 0.0,
}
EMPTY_LEDGER = {"koinly": {}, "brokerage": {}}


def _paycheck(base: float = 5_000.0, spouse: float = 900.0) -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={
            "you": {"federal_withholding_ytd": base},
            "spouse": {"federal_withholding_ytd": spouse},
        }
    )


class TestComponentModel:
    def test_household_total_is_base_plus_component(self):
        ytd = _paycheck()
        ytd.set_ubs_withholding("you", 1_000.0)
        assert ytd.owner_value("you", "federal_withholding_ytd") == 5_000.0
        assert ytd.withholding_total("you") == 6_000.0
        assert ytd.federal_withholding_ytd == 6_900.0

    def test_setting_the_component_twice_replaces_it(self):
        ytd = _paycheck()
        ytd.set_ubs_withholding("you", 1_000.0)
        ytd.set_ubs_withholding("you", 1_000.0)
        assert ytd.federal_withholding_ytd == 6_900.0

    def test_component_attributes_a_legacy_snapshot(self):
        ytd = YTDSnapshot(federal_withholding_ytd=5_000.0)
        ytd.set_ubs_withholding("you", 1_000.0)
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == 5_000.0
        assert ytd.federal_withholding_ytd == 6_000.0

    def test_component_requires_an_attributed_snapshot(self):
        with pytest.raises(ValueError, match="attributed"):
            YTDSnapshot(ubs_withholding_by_owner={"you": 1.0})

    def test_component_survives_replace(self):
        from dataclasses import replace

        ytd = _paycheck()
        ytd.set_ubs_withholding("you", 1_000.0)
        assert replace(ytd, interest_ytd=1.0).federal_withholding_ytd == 6_900.0


class TestApplyIsAdditiveAndIdempotent:
    def test_ubs_adds_to_paycheck_withholding(self):
        ytd = _paycheck()
        apply_ubs_exercise_income(ytd, [A], owner="you")
        assert ytd.withholding_total("you") == pytest.approx(5_000.0 + WA)
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == 5_000.0

    def test_same_csv_twice_does_not_double(self):
        ytd = _paycheck()
        apply_ubs_exercise_income(ytd, [A], owner="you")
        apply_ubs_exercise_income(ytd, [A], owner="you")
        assert ytd.withholding_total("you") == pytest.approx(5_000.0 + WA)

    def test_nqo_exercise_keeps_replace_semantics(self):
        ytd = _paycheck()
        apply_ubs_exercise_income(ytd, [A], owner="you")
        apply_ubs_exercise_income(ytd, [A], owner="you")
        assert ytd.by_owner["you"]["nqo_exercise_ytd"] == pytest.approx(A.bargain_element)
        apply_ubs_exercise_income(ytd, [B], owner="you")
        assert ytd.by_owner["you"]["nqo_exercise_ytd"] == pytest.approx(B.bargain_element)


class TestLegacyMigration:
    def test_exact_pre_1b_value_moves_into_the_component(self):
        ytd = _paycheck(base=WA)
        assert split_legacy_ubs_withholding(ytd, [A], [A], owner="you") == []
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == 0.0
        assert ytd.ubs_withholding_by_owner == {"you": pytest.approx(WA)}
        assert ytd.withholding_total("you") == pytest.approx(WA)

    def test_value_truncated_to_whole_dollars_on_a_legacy_snapshot(self):
        # Pre-1b form: value=int(total), written back as float(int).
        ytd = YTDSnapshot(federal_withholding_ytd=float(math.floor(WA)))
        split_legacy_ubs_withholding(ytd, [A], [A], owner="you")
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == 0.0
        assert ytd.ubs_withholding_by_owner == {"you": pytest.approx(WA)}

    def test_value_left_by_the_old_floored_overlay_with_fractional_others(self):
        # Old overlay: slot = int(total) - others.
        spouse = 900.75
        ytd = _paycheck(base=math.floor(WA + spouse) - spouse, spouse=spouse)
        split_legacy_ubs_withholding(ytd, [A], [A], owner="you")
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == 0.0
        assert ytd.withholding_total("spouse") == spouse

    def test_matches_the_sum_of_all_stored_orders_too(self):
        ytd = _paycheck(base=WA + WB)
        split_legacy_ubs_withholding(ytd, [A], [A, B], owner="you")
        assert ytd.ubs_withholding_by_owner == {"you": pytest.approx(WA + WB)}

    def test_runs_once(self):
        ytd = _paycheck(base=WA)
        split_legacy_ubs_withholding(ytd, [A], [A], owner="you")
        before = ytd.by_owner["you"]["federal_withholding_ytd"], dict(ytd.ubs_withholding_by_owner)
        assert split_legacy_ubs_withholding(ytd, [A], [A], owner="you") == []
        assert (
            ytd.by_owner["you"]["federal_withholding_ytd"],
            ytd.ubs_withholding_by_owner,
        ) == before

    def test_no_orders_is_a_no_op(self):
        ytd = _paycheck(base=WA)
        assert split_legacy_ubs_withholding(ytd, [], [], owner="you") == []
        assert ytd.ubs_withholding_by_owner == {}

    def test_a_hand_entered_base_is_never_migrated(self):
        ytd = _paycheck(base=WA)
        ytd.manual_marks["you.federal_withholding_ytd"] = ManualMark(WA, "2026-09-30")
        split_legacy_ubs_withholding(ytd, [A], [A], owner="you")
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == pytest.approx(WA)
        assert ytd.ubs_withholding_by_owner == {}

    def test_an_unrelated_base_is_kept_and_the_addition_is_flagged(self):
        ytd = _paycheck(base=5_000.0)
        warnings = split_legacy_ubs_withholding(ytd, [A], [A], owner="you")
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == 5_000.0
        assert any("ADDED" in w and "5,000.00" in w for w in warnings)

    def test_upload_of_a_new_order_after_a_pre_1b_apply_does_not_double(self, monkeypatch):
        monkeypatch.setattr(sync_scan_mod, "_load_ubs_orders_safe", lambda: [A, B])
        monkeypatch.setattr(sync_scan_mod, "operator_slot", lambda: "you")
        ytd = _paycheck(base=math.floor(WA))  # pre-1b: truncated withholding of the stored [A]
        sync_scan_mod._apply_ubs_correction(dict(TOTALS), EMPTY_LEDGER, ytd, applied_orders=[A])
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == 0.0
        assert ytd.withholding_total("you") == pytest.approx(WA + WB)
