"""views.store_notice: the storage-unavailable and JSON-import banners."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.dashboard as dashboard_mod
import views.setup.data_bridge as data_bridge_mod
import views.store_notice as notice_mod
from engine.store import CorruptStoreError, StoreUnavailableError
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


def test_a_corrupt_row_message_names_the_namespace_and_reason():
    exc = CorruptStoreError("ytd", "_", "schema_version 9 is newer than this app")
    assert notice_mod.store_error_message(exc) == (
        "Saved data for ytd could not be read: schema_version 9 is newer than this app. "
        "Nothing was saved."
    )


def test_an_unavailable_store_message_is_the_storage_banner():
    exc = StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")
    assert notice_mod.store_error_message(exc) == notice_mod.storage_unavailable_message(exc)


@pytest.fixture
def _isolated_app(clean_command_center_caches, monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _run_app() -> AppTest:
    at = AppTest.from_file("app.py", default_timeout=30)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "overview"
    at.run()
    return at


@pytest.mark.usefixtures("_isolated_app")
def test_a_corrupt_store_error_from_a_page_is_a_banner_not_a_traceback(monkeypatch):
    def refused_save(*_args, **_kwargs):
        raise CorruptStoreError("ytd", "_", "schema_version 9 is newer than this app")

    monkeypatch.setattr(dashboard_mod, "render", refused_save)
    at = _run_app()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Saved data for ytd could not be read" in t for t in _texts(at.error))


@pytest.mark.usefixtures("_isolated_app")
def test_an_unavailable_store_at_boot_is_one_banner_not_a_traceback(monkeypatch):
    def locked(*_args, **_kwargs):
        raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")

    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", locked)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", locked)
    at = _run_app()
    assert not at.exception, [e.value for e in at.exception]
    assert sum("database is locked" in t for t in _texts(at.error)) == 1
