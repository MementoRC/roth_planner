"""Redesign PR 1b: unsaved manual-entry drafts (pure helpers)."""

from models.ytd_income import ManualMark, YTDSnapshot
from views.ytd_drafts import (
    commit_drafts,
    describe_keys,
    has_unsaved,
    put_draft,
    reconcile_drafts,
)


def _saved() -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={"you": {"wages_ytd": 100_000.0}, "spouse": {"wages_ytd": 70_000.0}},
        interest_ytd=1_234.56,
        crypto_income_ytd=10.257,
    )


def test_put_draft_records_the_shown_base_once():
    drafts = put_draft(None, "household.interest_ytd", 2_000.0, _saved())
    assert drafts == {"household.interest_ytd": {"value": 2_000.0, "base": 1_234.0}}
    again = put_draft(drafts, "household.interest_ytd", 3_000.0, YTDSnapshot(interest_ytd=9.0))
    assert again["household.interest_ytd"] == {"value": 3_000.0, "base": 1_234.0}


def test_reconcile_drops_unchanged_and_keeps_edits():
    ytd = _saved()
    drafts = {}
    for key, value in [
        ("you.wages_ytd", 100_000.0),  # unchanged
        ("household.interest_ytd", 1_234.0),  # int widget showed int(1234.56)
        ("household.crypto_income_ytd", 10.26),  # float widget showed round(.., 2)
        ("spouse.wages_ytd", 1.0),  # edited
    ]:
        drafts = put_draft(drafts, key, value, ytd)
    live, stale = reconcile_drafts(drafts, ytd)
    assert live == {"spouse.wages_ytd": {"value": 1.0, "base": 70_000.0}}
    assert stale == []


def test_reconcile_discards_a_draft_whose_saved_value_an_import_changed():
    drafts = put_draft(None, "spouse.wages_ytd", 1.0, _saved())
    imported = _saved()
    imported.set_owned("spouse", wages_ytd=75_000.0)
    live, stale = reconcile_drafts(drafts, imported)
    assert live == {}
    assert stale == ["spouse.wages_ytd"]


def test_reconcile_ignores_malformed_entries():
    live, stale = reconcile_drafts(
        {"nonsense": {"value": 1.0, "base": 0.0}, "you.wages_ytd": 5}, _saved()
    )
    assert (live, stale) == ({}, [])


def test_has_unsaved():
    assert has_unsaved(None, _saved()) is False
    assert has_unsaved(put_draft(None, "you.wages_ytd", 100_000.0, _saved()), _saved()) is False
    assert has_unsaved(put_draft(None, "you.wages_ytd", 1.0, _saved()), _saved()) is True
    assert has_unsaved(put_draft(None, "you.wages_ytd", 1.0, YTDSnapshot()), None) is True


def test_describe_keys_lists_person_then_field_in_form_order():
    assert describe_keys(
        ["household.interest_ytd", "spouse.federal_withholding_ytd", "you.wages_ytd"]
    ) == ["You: Wages", "Spouse: Federal tax withheld", "Household: Interest"]


def test_commit_uses_each_persons_meta_and_the_current_persons_for_household():
    ytd = _saved()
    drafts = put_draft(None, "you.wages_ytd", 1.0, ytd)
    drafts = put_draft(drafts, "spouse.wages_ytd", 2.0, ytd)
    drafts = put_draft(drafts, "household.interest_ytd", 3.0, ytd)
    meta = {
        "you": {"as_of": "2026-09-01", "note": "mine"},
        "spouse": {"as_of": "2026-09-15", "note": "theirs"},
    }
    out = commit_drafts(ytd, drafts, meta, current_owner="spouse", today="2026-10-03")
    assert out.by_owner["you"]["wages_ytd"] == 1.0
    assert out.by_owner["spouse"]["wages_ytd"] == 2.0
    assert out.interest_ytd == 3.0
    assert out.manual_marks == {
        "you.wages_ytd": ManualMark(1.0, "2026-09-01", "mine"),
        "spouse.wages_ytd": ManualMark(2.0, "2026-09-15", "theirs"),
        "household.interest_ytd": ManualMark(3.0, "2026-09-15", "theirs"),
    }


def test_commit_without_meta_uses_today_and_no_note():
    drafts = put_draft(None, "you.wages_ytd", 1.0, _saved())
    out = commit_drafts(_saved(), drafts, {}, current_owner="you", today="2026-10-03")
    assert out.manual_marks["you.wages_ytd"] == ManualMark(1.0, "2026-10-03", "")


def test_commit_attributes_a_legacy_snapshot_to_the_current_person():
    legacy = YTDSnapshot(wages_ytd=90_000.0)
    drafts = put_draft(None, "spouse.wages_ytd", 5.0, legacy.attribute_unowned("you"))
    out = commit_drafts(legacy, drafts, {}, current_owner="you", today="2026-10-03")
    assert out.by_owner["you"]["wages_ytd"] == 90_000.0
    assert out.by_owner["spouse"]["wages_ytd"] == 5.0


def test_commit_does_not_mutate_its_input():
    saved = _saved()
    commit_drafts(
        saved,
        put_draft(None, "you.wages_ytd", 1.0, saved),
        {},
        current_owner="you",
        today="2026-10-03",
    )
    assert saved == _saved()
