"""Redesign PR 1b: per-person hand-entry provenance in data-bridge bundles."""

import dataclasses

import pytest

from engine.bridge_bundle import parse_v5
from engine.bridge_import import plan_import
from models.ytd_income import ManualMark, YTDSnapshot
from tests.test_bridge_import_apply import _apply, _receiver, _spouse_bundle, _v4_bundle

# _spouse_bundle's exporter slot has captured_at == NOW == "2026-09-30T14:32:11+00:00".


def _sender(marks: dict | None = None) -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={"spouse": {"wages_ytd": 80_000.0, "federal_withholding_ytd": 5_000.0}},
        manual_marks=marks or {},
    )


def _local(mark_as_of: str):
    ytd = YTDSnapshot(
        by_owner={"you": {"wages_ytd": 100_000.0}, "spouse": {"wages_ytd": 70_000.0}},
        manual_marks={"spouse.wages_ytd": ManualMark(70_000.0, mark_as_of, "receiver typed")},
    )
    return dataclasses.replace(_receiver(), ytd=ytd)


def test_export_emits_manual_only_for_marked_fields():
    bundle = _spouse_bundle(
        ytd=_sender({"spouse.wages_ytd": ManualMark(80_000.0, "2026-09-29", "W-2 portal")})
    )
    assert bundle["slots"]["spouse"]["ytd"]["manual"] == {
        "wages_ytd": {"value": 80_000.0, "as_of": "2026-09-29", "note": "W-2 portal"}
    }


def test_export_without_marks_has_no_manual_key():
    assert "manual" not in _spouse_bundle(ytd=_sender())["slots"]["spouse"]["ytd"]


def test_household_marks_are_never_exported():
    sender = dataclasses.replace(
        _sender(),
        interest_ytd=900.0,
        manual_marks={"household.interest_ytd": ManualMark(900.0, "2026-09-29", "")},
    )
    assert "manual" not in _spouse_bundle(ytd=sender)["slots"]["spouse"]["ytd"]


def test_parse_reads_marks_and_drops_malformed_ones_with_a_warning():
    bundle = _spouse_bundle(
        ytd=_sender({"spouse.wages_ytd": ManualMark(80_000.0, "2026-09-29", "W-2 portal")})
    )
    bundle["slots"]["spouse"]["ytd"]["manual"]["interest_ytd"] = {
        "value": 1.0,
        "as_of": "2026-09-29",
    }
    bundle["slots"]["spouse"]["ytd"]["manual"]["federal_withholding_ytd"] = {"value": "x"}
    parsed = parse_v5(bundle)
    assert parsed.slots["spouse"].ytd.manual == {
        "wages_ytd": ManualMark(80_000.0, "2026-09-29", "W-2 portal")
    }
    assert any("interest_ytd" in w for w in parsed.warnings)
    assert any("federal_withholding_ytd" in w for w in parsed.warnings)


def test_non_object_manual_section_is_ignored_with_a_warning():
    bundle = _spouse_bundle(ytd=_sender())
    bundle["slots"]["spouse"]["ytd"]["manual"] = ["not", "an", "object"]
    parsed = parse_v5(bundle)
    assert parsed.slots["spouse"].ytd.manual == {}
    assert any("manual-entry provenance" in w for w in parsed.warnings)


def test_bundle_without_manual_key_parses_with_no_marks():
    assert parse_v5(_spouse_bundle(ytd=_sender())).slots["spouse"].ytd.manual == {}


def test_pre_1b_bundle_applies_exactly_as_before():
    # A bundle built the old way carries no "manual" key; the receiver holds no marks.
    bundle = _spouse_bundle(ytd=_sender())
    assert "manual" not in bundle["slots"]["spouse"]["ytd"]
    local = dataclasses.replace(
        _receiver(),
        ytd=YTDSnapshot(by_owner={"you": {"wages_ytd": 100_000.0}, "spouse": {"wages_ytd": 1.0}}),
    )
    result = _apply(bundle, local, selected={"spouse"})
    assert result.ytd.by_owner["spouse"]["wages_ytd"] == 80_000.0
    assert result.ytd.by_owner["spouse"]["federal_withholding_ytd"] == 5_000.0
    assert result.ytd.by_owner["you"]["wages_ytd"] == 100_000.0
    assert result.ytd.manual_marks == {}


def test_receiver_hand_entry_newer_than_bundle_survives():
    result = _apply(_spouse_bundle(ytd=_sender()), _local("2026-10-01"), selected={"spouse"})
    assert result.ytd.by_owner["spouse"]["wages_ytd"] == 70_000.0
    assert result.ytd.manual_marks["spouse.wages_ytd"].note == "receiver typed"


def test_preview_shows_the_value_the_apply_keeps():
    local = _local("2026-10-01")
    plan = plan_import(_spouse_bundle(ytd=_sender()), local, operator="you")
    writes = plan.slots["spouse"].ytd_writes
    assert "wages_ytd" not in writes
    assert writes["federal_withholding_ytd"] == (0.0, 5_000.0)


def test_bundle_as_recent_as_the_hand_entry_replaces_it():
    result = _apply(_spouse_bundle(ytd=_sender()), _local("2026-09-30"), selected={"spouse"})
    assert result.ytd.by_owner["spouse"]["wages_ytd"] == 80_000.0
    assert "spouse.wages_ytd" not in result.ytd.manual_marks


def test_sender_marks_are_adopted_when_their_values_win():
    sender = _sender({"spouse.wages_ytd": ManualMark(80_000.0, "2026-09-29", "W-2 portal")})
    result = _apply(_spouse_bundle(ytd=sender), _local("2026-09-01"), selected={"spouse"})
    assert result.ytd.manual_marks["spouse.wages_ytd"] == ManualMark(
        80_000.0, "2026-09-29", "W-2 portal"
    )


def test_sender_mark_that_does_not_match_the_carried_value_is_not_adopted():
    sender = _sender({"spouse.wages_ytd": ManualMark(79_000.0, "2026-09-29", "stale")})
    result = _apply(_spouse_bundle(ytd=sender), _local("2026-09-01"), selected={"spouse"})
    assert result.ytd.by_owner["spouse"]["wages_ytd"] == 80_000.0
    assert "spouse.wages_ytd" not in result.ytd.manual_marks


def test_future_dated_sender_mark_is_not_adopted():
    sender = _sender({"spouse.wages_ytd": ManualMark(80_000.0, "2026-12-01", "future")})
    result = _apply(_spouse_bundle(ytd=sender), _local("2026-09-01"), selected={"spouse"})
    assert result.ytd.by_owner["spouse"]["wages_ytd"] == 80_000.0
    assert "spouse.wages_ytd" not in result.ytd.manual_marks


def _undated(captured_at):
    bundle = _spouse_bundle(ytd=_sender())
    if captured_at is None:
        bundle["slots"]["spouse"].pop("captured_at", None)
    else:
        bundle["slots"]["spouse"]["captured_at"] = captured_at
    return bundle


@pytest.mark.parametrize("captured_at", [None, "", "1999/09/30"])
def test_undated_or_unparsable_slot_import_wins_over_a_hand_entry(captured_at):
    # Pre-1b behaviour: an import with no usable date replaces the hand entry.
    result = _apply(_undated(captured_at), _local("2026-10-01"), selected={"spouse"})
    assert result.ytd.by_owner["spouse"]["wages_ytd"] == 80_000.0
    assert "spouse.wages_ytd" not in result.ytd.manual_marks


def test_receiver_own_slot_marks_are_untouched_by_a_spouse_import():
    local = _local("2026-09-01")
    local.ytd.manual_marks["you.wages_ytd"] = ManualMark(100_000.0, "2026-09-01", "")
    result = _apply(_spouse_bundle(ytd=_sender()), local, selected={"spouse"})
    assert result.ytd.manual_marks["you.wages_ytd"] == ManualMark(100_000.0, "2026-09-01", "")


def test_v4_import_keeps_the_receivers_snapshot_and_marks():
    # PR #497: a v4 bundle's YTD is never adopted (engine/bridge_import.py:588-599).
    local = _local("2026-09-01")
    result = _apply(_v4_bundle(), local)
    assert result.ytd.manual_marks == local.ytd.manual_marks
    assert result.ytd.by_owner == local.ytd.by_owner


# --- UBS withholding component (redesign PR 1b, Task 9) ---


def _sender_split(ubs: float | None) -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={"spouse": {"wages_ytd": 80_000.0, "federal_withholding_ytd": 5_000.0}},
        ubs_withholding_by_owner={} if ubs is None else {"spouse": ubs},
    )


def test_export_carries_the_total_and_the_component():
    slot_ytd = _spouse_bundle(ytd=_sender_split(1_000.0))["slots"]["spouse"]["ytd"]
    assert slot_ytd["values"]["federal_withholding_ytd"] == 6_000.0
    assert slot_ytd["ubs_federal_withholding"] == 1_000.0
    unsplit = _spouse_bundle(ytd=_sender_split(None))["slots"]["spouse"]["ytd"]
    assert "ubs_federal_withholding" not in unsplit


def test_parse_splits_total_into_base_and_component():
    parsed = parse_v5(_spouse_bundle(ytd=_sender_split(1_000.0)))
    assert parsed.slots["spouse"].ytd.values["federal_withholding_ytd"] == 5_000.0
    assert parsed.slots["spouse"].ytd.ubs_federal_withholding == 1_000.0


def test_component_larger_than_total_is_ignored_with_a_warning():
    bundle = _spouse_bundle(ytd=_sender_split(1_000.0))
    bundle["slots"]["spouse"]["ytd"]["ubs_federal_withholding"] = 99_999.0
    parsed = parse_v5(bundle)
    assert parsed.slots["spouse"].ytd.ubs_federal_withholding is None
    assert parsed.slots["spouse"].ytd.values["federal_withholding_ytd"] == 6_000.0
    assert any("ubs_federal_withholding" in w for w in parsed.warnings)


def test_import_lands_base_and_component():
    result = _apply(
        _spouse_bundle(ytd=_sender_split(1_000.0)), _local("2026-09-01"), selected={"spouse"}
    )
    assert result.ytd.by_owner["spouse"]["federal_withholding_ytd"] == 5_000.0
    assert result.ytd.ubs_withholding_by_owner["spouse"] == 1_000.0
    assert result.ytd.withholding_total("spouse") == 6_000.0


def test_pre_1b_bundle_into_a_slot_with_a_component_keeps_it():
    # A pre-1b sender's total already includes UBS: keep the receiver's
    # component and take the rest as base, so the total equals the bundle's.
    local = _local("2026-09-01")
    local.ytd.set_ubs_withholding("spouse", 777.0)
    result = _apply(_spouse_bundle(ytd=_sender_split(None)), local, selected={"spouse"})
    assert result.ytd.ubs_withholding_by_owner["spouse"] == 777.0
    assert result.ytd.by_owner["spouse"]["federal_withholding_ytd"] == 4_223.0
    assert result.ytd.withholding_total("spouse") == 5_000.0


def test_identical_ubs_rescan_after_a_pre_1b_import_does_not_double():
    from datetime import date

    from engine.ubs_activity_csv import federal_withholding
    from engine.ubs_exercise_ytd import apply_ubs_exercise_income, split_legacy_ubs_withholding
    from tests.test_ubs_exercise_ytd import _make_order

    order = _make_order("FA1", date(2026, 3, 1), bargain_element=2_000.0, fees=5.0)
    local = _local("2026-09-01")
    local.ytd.set_ubs_withholding("spouse", federal_withholding(order))
    ytd = _apply(_spouse_bundle(ytd=_sender_split(None)), local, selected={"spouse"}).ytd
    split_legacy_ubs_withholding(ytd, [order], [order], owner="spouse")
    apply_ubs_exercise_income(ytd, [order], owner="spouse")
    assert ytd.withholding_total("spouse") == pytest.approx(5_000.0)


def test_pre_1b_bundle_total_below_the_component_keeps_it_and_warns():
    local = _local("2026-09-01")
    local.ytd.set_ubs_withholding("spouse", 7_000.0)
    bundle = _spouse_bundle(ytd=_sender_split(None))
    result = _apply(bundle, local, selected={"spouse"})
    assert result.ytd.ubs_withholding_by_owner["spouse"] == 7_000.0
    assert result.ytd.by_owner["spouse"]["federal_withholding_ytd"] == 0.0
    plan = plan_import(bundle, local, operator="you")
    assert any("UBS" in w and "spouse" in w for w in plan.warnings)


def test_pre_1b_bundle_into_a_slot_without_a_component_is_unchanged():
    result = _apply(
        _spouse_bundle(ytd=_sender_split(None)), _local("2026-09-01"), selected={"spouse"}
    )
    assert result.ytd.ubs_withholding_by_owner == {}
    assert result.ytd.by_owner["spouse"]["federal_withholding_ytd"] == 5_000.0


def test_preview_matches_apply_for_a_pre_1b_bundle_into_a_component_slot():
    local = _local("2026-09-01")
    local.ytd.set_ubs_withholding("spouse", 777.0)
    bundle = _spouse_bundle(ytd=_sender_split(None))
    plan = plan_import(bundle, local, operator="you")
    result = _apply(bundle, local, selected={"spouse"})
    assert plan.slots["spouse"].ytd_writes["federal_withholding_ytd"] == (
        777.0,
        result.ytd.withholding_total("spouse"),
    )


def test_preview_diff_reports_the_withholding_total():
    local = _local("2026-09-01")
    plan = plan_import(_spouse_bundle(ytd=_sender_split(1_000.0)), local, operator="you")
    assert plan.slots["spouse"].ytd_writes["federal_withholding_ytd"] == (0.0, 6_000.0)
