"""Redesign PR 1b: hand-entry provenance round-trips through the YTD cache dict."""

from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict
from models.ytd_income import ManualMark, YTDSnapshot


def _marked() -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={"spouse": {"wages_ytd": 123_456.0}},
        interest_ytd=900.0,
        manual_marks={
            "spouse.wages_ytd": ManualMark(123_456.0, "2026-09-30", "paystub"),
            "household.interest_ytd": ManualMark(900.0, "2026-09-15", ""),
        },
    )


def test_round_trip_preserves_marks():
    assert ytd_from_dict(ytd_to_dict(_marked())) == _marked()


def test_snapshot_without_marks_emits_no_key():
    assert "manual_marks" not in ytd_to_dict(YTDSnapshot(wages_ytd=1.0))


def test_legacy_cache_without_marks_loads_empty():
    assert ytd_from_dict(ytd_to_dict(YTDSnapshot(wages_ytd=1.0))).manual_marks == {}


def test_malformed_marks_are_dropped_not_fatal():
    data = ytd_to_dict(_marked())
    data["manual_marks"]["you.interest_ytd"] = {"value": 1.0, "as_of": "2026-09-30"}
    data["manual_marks"]["household.ltcg_ytd"] = {"value": "x", "as_of": "2026-09-30"}
    data["manual_marks"]["household.stcg_ytd"] = "garbage"
    loaded = ytd_from_dict(data)
    assert set(loaded.manual_marks) == {"spouse.wages_ytd", "household.interest_ytd"}
    assert loaded.wages_ytd == 123_456.0


def test_non_dict_marks_section_is_ignored():
    data = ytd_to_dict(_marked())
    data["manual_marks"] = ["not", "a", "dict"]
    assert ytd_from_dict(data).manual_marks == {}


def test_component_round_trips():
    ytd = YTDSnapshot(
        by_owner={"spouse": {"federal_withholding_ytd": 5_000.0}},
        ubs_withholding_by_owner={"spouse": 1_000.0},
    )
    data = ytd_to_dict(ytd)
    assert data["ubs_withholding_by_owner"] == {"spouse": 1_000.0}
    restored = ytd_from_dict(data)
    assert restored == ytd
    assert restored.federal_withholding_ytd == 6_000.0
    assert "ubs_withholding_by_owner" not in ytd_to_dict(YTDSnapshot(wages_ytd=1.0))


def test_malformed_component_entries_are_dropped():
    data = ytd_to_dict(YTDSnapshot(by_owner={"you": {"wages_ytd": 1.0}}))
    data["ubs_withholding_by_owner"] = {"spouse": "x", "household": 1.0, "you": True}
    assert ytd_from_dict(data).ubs_withholding_by_owner == {}


def test_component_without_by_owner_is_dropped_not_fatal():
    data = ytd_to_dict(YTDSnapshot(federal_withholding_ytd=7.0))
    data["ubs_withholding_by_owner"] = {"you": 1.0}
    loaded = ytd_from_dict(data)
    assert loaded.ubs_withholding_by_owner == {}
    assert loaded.federal_withholding_ytd == 7.0
