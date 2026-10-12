"""Redesign PR 1b: provenance of hand-entered YTD values (ManualMark)."""

from __future__ import annotations

import pytest

from models.ytd_income import (
    ManualMark,
    YTDSnapshot,
    describe_kept_manual,
    manual_key,
    manual_mark_from_dict,
    split_manual_key,
)

# Every way a manual-entry key can be wrong: unknown owner, a household field
# keyed by owner, an owned field keyed as household, or no "owner." prefix.
_BAD_KEY = (
    r"unknown YTD owner|not per-owner YTD fields|"
    r"not a household manual field|malformed manual-entry key"
)


class TestManualKeys:
    def test_owned_field_key_names_the_owner(self):
        assert manual_key("wages_ytd", "spouse") == "spouse.wages_ytd"

    def test_household_field_key(self):
        assert manual_key("interest_ytd") == "household.interest_ytd"

    @pytest.mark.parametrize(
        ("field_name", "owner"),
        [("interest_ytd", "you"), ("wages_ytd", None), ("wages_ytd", "household")],
    )
    def test_wrong_field_owner_pairing_raises(self, field_name, owner):
        with pytest.raises(ValueError, match=_BAD_KEY):
            manual_key(field_name, owner)

    def test_split_round_trips(self):
        assert split_manual_key("you.federal_withholding_ytd") == ("you", "federal_withholding_ytd")
        assert split_manual_key("household.ltcg_ytd") == ("household", "ltcg_ytd")

    @pytest.mark.parametrize("bad", ["wages_ytd", "you.interest_ytd", "neighbour.wages_ytd", ""])
    def test_split_rejects_malformed(self, bad):
        with pytest.raises(ValueError, match=_BAD_KEY):
            split_manual_key(bad)


class TestManualMarkFromDict:
    def test_parses_a_well_formed_entry(self):
        assert manual_mark_from_dict(
            {"value": 123456, "as_of": "2026-09-30", "note": "paystub"}
        ) == ManualMark(123456.0, "2026-09-30", "paystub")

    def test_truncates_a_timestamp_to_its_date_and_defaults_the_note(self):
        assert manual_mark_from_dict({"value": 1.5, "as_of": "2026-09-30T10:00:00"}) == ManualMark(
            1.5, "2026-09-30", ""
        )

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            [],
            {"value": True, "as_of": "2026-09-30"},
            {"value": "12", "as_of": "2026-09-30"},
            {"value": 12.0},
            {"value": 12.0, "as_of": "not-a-date"},
        ],
    )
    def test_malformed_entries_are_none(self, raw):
        assert manual_mark_from_dict(raw) is None


class TestSnapshotMarks:
    def test_default_is_empty(self):
        assert YTDSnapshot().manual_marks == {}

    def test_rejects_a_malformed_key(self):
        with pytest.raises(ValueError, match="not per-owner YTD fields"):
            YTDSnapshot(manual_marks={"you.interest_ytd": ManualMark(1.0, "2026-09-30")})

    def test_replace_copies_the_marks_dict(self):
        from dataclasses import replace

        original = YTDSnapshot(
            manual_marks={"household.interest_ytd": ManualMark(5.0, "2026-09-30")}
        )
        copy = replace(original)
        copy.manual_marks.clear()
        assert "household.interest_ytd" in original.manual_marks


def _two_people() -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={
            "you": {"wages_ytd": 100_000.0, "federal_withholding_ytd": 9_000.0},
            "spouse": {"wages_ytd": 70_000.0},
        },
        interest_ytd=500.0,
    )


class TestWithManualValues:
    def test_writes_only_the_named_owner_slot_and_marks_it(self):
        after = _two_people().with_manual_values(
            owner="spouse",
            owned={"wages_ytd": 123_456.0},
            household={},
            as_of="2026-09-30",
            note="paystub",
        )
        assert after.by_owner["spouse"]["wages_ytd"] == 123_456.0
        assert after.by_owner["you"]["wages_ytd"] == 100_000.0
        assert after.wages_ytd == 223_456.0
        assert after.manual_marks == {
            "spouse.wages_ytd": ManualMark(123_456.0, "2026-09-30", "paystub")
        }

    def test_a_value_below_the_other_person_is_kept_not_floored(self):
        after = _two_people().with_manual_values(
            owner="you", owned={"wages_ytd": 50_000.0}, household={}, as_of="2026-09-30"
        )
        assert after.by_owner["you"]["wages_ytd"] == 50_000.0
        assert after.wages_ytd == 120_000.0

    def test_unchanged_values_are_not_marked(self):
        after = _two_people().with_manual_values(
            owner="you",
            owned={"wages_ytd": 100_000.0, "federal_withholding_ytd": 9_000.0},
            household={"interest_ytd": 500.0},
            as_of="2026-09-30",
        )
        assert after.manual_marks == {}
        assert after == _two_people()

    def test_household_field_is_written_and_marked(self):
        after = _two_people().with_manual_values(
            owner="you", owned={}, household={"interest_ytd": 777.0}, as_of="2026-09-30"
        )
        assert after.interest_ytd == 777.0
        assert after.manual_marks["household.interest_ytd"] == ManualMark(777.0, "2026-09-30", "")

    def test_source_snapshot_is_not_mutated(self):
        before = _two_people()
        before.with_manual_values(
            owner="spouse",
            owned={"wages_ytd": 1.0},
            household={"interest_ytd": 1.0},
            as_of="2026-09-30",
        )
        assert before == _two_people()

    def test_owned_values_on_an_unattributed_snapshot_raise(self):
        with pytest.raises(ValueError, match="attribute_unowned"):
            YTDSnapshot(wages_ytd=5.0).with_manual_values(
                owner="you", owned={"wages_ytd": 1.0}, household={}, as_of="2026-09-30"
            )

    def test_unknown_household_field_raises(self):
        with pytest.raises(ValueError, match="wages_ytd"):
            _two_people().with_manual_values(
                owner="you", owned={}, household={"wages_ytd": 1.0}, as_of="2026-09-30"
            )

    def test_malformed_as_of_raises(self):
        with pytest.raises(ValueError, match="isoformat"):
            _two_people().with_manual_values(
                owner="you", owned={"wages_ytd": 1.0}, household={}, as_of="30/09/2026"
            )

    def test_owner_value_reads_a_slot_or_the_legacy_total(self):
        assert _two_people().owner_value("spouse", "federal_withholding_ytd") == 0.0
        assert YTDSnapshot(wages_ytd=42.0).owner_value("spouse", "wages_ytd") == 42.0


def _marked() -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={"you": {"federal_withholding_ytd": 9_000.0}, "spouse": {"wages_ytd": 70_000.0}},
        interest_ytd=900.0,
        manual_marks={
            "you.federal_withholding_ytd": ManualMark(9_000.0, "2026-09-30", "paystub"),
            "household.interest_ytd": ManualMark(900.0, "2026-09-30", ""),
        },
    )


class TestSettleManualMarks:
    def test_manual_value_reads_base_or_household(self):
        ytd = _marked()
        assert ytd.manual_value("you.federal_withholding_ytd") == 9_000.0
        assert ytd.manual_value("household.interest_ytd") == 900.0

    def test_untouched_marks_survive_any_import(self):
        ytd = _marked()
        assert ytd.settle_manual_marks(as_of="2026-12-31") == ()
        assert set(ytd.manual_marks) == {"you.federal_withholding_ytd", "household.interest_ytd"}

    def test_newer_hand_entry_is_restored_over_an_older_import(self):
        ytd = _marked()
        ytd.interest_ytd = 400.0
        assert ytd.settle_manual_marks(as_of="2026-09-15") == ("household.interest_ytd",)
        assert ytd.interest_ytd == 900.0
        assert "household.interest_ytd" in ytd.manual_marks

    def test_import_as_recent_as_the_hand_entry_wins_and_drops_the_mark(self):
        ytd = _marked()
        ytd.interest_ytd = 400.0
        assert ytd.settle_manual_marks(as_of="2026-09-30") == ()
        assert ytd.interest_ytd == 400.0
        assert "household.interest_ytd" not in ytd.manual_marks

    @pytest.mark.parametrize("as_of", [None, ""])
    def test_undated_import_wins(self, as_of):
        ytd = _marked()
        ytd.interest_ytd = 400.0
        assert ytd.settle_manual_marks(as_of=as_of) == ()
        assert ytd.interest_ytd == 400.0

    def test_per_owner_value_is_restored_into_its_slot(self):
        ytd = _marked()
        ytd.set_owned("you", federal_withholding_ytd=1_234.0)  # e.g. a v5 import
        assert ytd.settle_manual_marks(as_of="2026-08-01T12:00:00") == (
            "you.federal_withholding_ytd",
        )
        assert ytd.by_owner["you"]["federal_withholding_ytd"] == 9_000.0
        assert ytd.federal_withholding_ytd == 9_000.0

    def test_works_on_a_legacy_snapshot(self):
        ytd = YTDSnapshot(
            wages_ytd=10.0, manual_marks={"you.wages_ytd": ManualMark(10.0, "2026-09-30")}
        )
        ytd.wages_ytd = 3.0
        assert ytd.settle_manual_marks(as_of="2026-01-01") == ("you.wages_ytd",)
        assert ytd.wages_ytd == 10.0

    def test_describe_kept_manual_names_field_owner_value_and_date(self):
        assert describe_kept_manual(_marked(), ["you.federal_withholding_ytd"]) == [
            "Kept your hand-entered federal withholding (you) of 9,000.00 "
            "(as of 2026-09-30): the imported figure is older."
        ]


class TestOverlayNoLongerFloors:
    def test_owned_field_on_an_attributed_snapshot_raises(self):
        with pytest.raises(ValueError, match="with_manual_values"):
            _two_people().overlay(wages_ytd=180_000.0)
