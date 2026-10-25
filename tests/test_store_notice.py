"""views.store_notice: the storage-unavailable and JSON-import banners."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

import views.store_notice as notice_mod
from engine.store import StoreUnavailableError
from engine.store.migrate_json import MigrationResult


def _page() -> None:
    import streamlit as st

    from views.store_notice import render_store_notice

    render_store_notice()
    st.write("page body")


def _texts(elements) -> list[str]:
    return [e.value for e in elements]


def test_nothing_is_shown_without_an_import(monkeypatch):
    monkeypatch.setattr(notice_mod, "migration_notice", lambda: None)
    at = AppTest.from_function(_page)
    at.run()
    assert not at.exception
    assert not at.success
    assert not at.error
    assert "page body" in _texts(at.markdown)


def test_a_successful_import_is_announced_once_per_session(monkeypatch):
    result = MigrationResult(
        imported=(".a.json", ".b.json"), backup_dir=Path("/synthetic/.json_backup/2026")
    )
    monkeypatch.setattr(notice_mod, "migration_notice", lambda: result)
    at = AppTest.from_function(_page)
    at.run()
    assert "Imported 2 stores" in _texts(at.success)[0]
    assert "/synthetic/.json_backup/2026" in _texts(at.success)[0]
    at.run()
    assert not at.success


def test_files_left_behind_are_named(monkeypatch):
    result = MigrationResult(
        imported=(".a.json",), backup_dir=Path("/synthetic/b"), unmoved=(".a.json",)
    )
    monkeypatch.setattr(notice_mod, "migration_notice", lambda: result)
    at = AppTest.from_function(_page)
    at.run()
    assert ".a.json" in _texts(at.warning)[0]


def test_a_failed_import_is_shown_on_every_run(monkeypatch):
    result = MigrationResult(
        failed_file=".bad.json", error="JSONDecodeError: x", read_only=("bad",)
    )
    monkeypatch.setattr(notice_mod, "migration_notice", lambda: result)
    at = AppTest.from_function(_page)
    at.run()
    assert ".bad.json" in _texts(at.error)[0]
    at.run()
    assert ".bad.json" in _texts(at.error)[0]
    assert "page body" in _texts(at.markdown)


def test_unavailable_storage_stops_the_page(monkeypatch):
    def unavailable():
        raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")

    monkeypatch.setattr(notice_mod, "get_store", unavailable)
    at = AppTest.from_function(_page)
    at.run()
    assert "database is locked" in _texts(at.error)[0]
    assert "page body" not in _texts(at.markdown)
