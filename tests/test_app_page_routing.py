"""app.py routes on page ids and keeps the Backup-only keypair guard (redesign PR 3a)."""

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod


@pytest.fixture(autouse=True)
def _isolated_app(clean_command_center_caches, monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _run(page: str) -> AppTest:
    at = AppTest.from_file("app.py", default_timeout=30)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = page
    at.session_state["_generated_pub_b64"] = "pub"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_generated_pubkey_survives_on_the_backup_page():
    at = _run("backup")
    assert at.session_state["nav_page"] == "backup"
    assert at.session_state["_generated_pub_b64"] == "pub"


def test_generated_pubkey_is_cleared_off_the_backup_page():
    at = _run("overview")
    assert "_generated_pub_b64" not in at.session_state
