"""Audit-1101 follow-ups #96, #97, #98.

#96: since W7 the Koinly ledger holds one slot per owner per tax year, so a
     bundle exported by this build carries per-year Koinly slots that a build
     reading only the flat one-slot shape takes as zero crypto income. The
     export is now ``format_version`` 6: builds that read 1-5 refuse it (their
     classifier refuses any version it does not know), while this build still
     reads 1-5. A version newer than this build reads is refused with a
     "newer version" message.
#97: a partial grants/shares fetch failure kept the saved values, but said so
     only in the one-shot "Synced:" flash. The snapshot now records which were
     kept (``kept_saved``), and the grants table and the shares section show a
     persistent caption until a later sync fetches them.
#98: a legacy ``.user_defaults.py`` beside ``.user_defaults.json`` kept
     supplying any key the JSON lacked, so a deliberately removed setting came
     back. The first save now writes every legacy key into the JSON and, once
     the JSON is confirmed to hold them all, renames the ``.py`` to
     ``.user_defaults.py.migrated`` so it is never read again.

All figures are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from config import loader
from engine.bridge_bundle import (
    BUNDLE_FORMAT_VERSION_V5,
    BUNDLE_FORMAT_VERSION_V6,
    UnsupportedBundleVersionError,
    build_bundle_v5,
    classify_bundle,
    parse_v5,
)
from engine.pdf_ledger import derive_koinly_totals
from engine.portfolio_sync.portfolio import kept_saved_note
from engine.portfolio_sync.shapes import PortfolioSnapshot

NOW = "2026-11-02T12:00:00+00:00"


# ---------------------------------------------------------------------------
# #96 -- bundle format version
# ---------------------------------------------------------------------------


def _per_year_ledger() -> dict:
    return {
        "koinly": {
            "you": {
                "2025": {"tax_year": 2025, "stcg": 100.0, "ltcg": 0.0, "income": 1.0},
                "2026": {"tax_year": 2026, "stcg": 300.0, "ltcg": 50.0, "income": 7.0},
            }
        },
        "brokerage": {},
    }


def _build(ledger: dict) -> dict:
    return build_bundle_v5(
        setup_scalars={"your_age": 60},
        snapshot=None,
        ledger=ledger,
        ytd=None,
        grants=None,
        exporter="you",
        slot_captured_at={},
        now=NOW,
    )


class TestIssue96BundleFormatVersion:
    def test_export_is_format_6(self):
        assert BUNDLE_FORMAT_VERSION_V5 == 5
        assert BUNDLE_FORMAT_VERSION_V6 == 6
        assert _build(_per_year_ledger())["format_version"] == 6

    def test_per_year_koinly_round_trips(self):
        parsed = parse_v5(_build(_per_year_ledger()))
        totals = derive_koinly_totals(parsed.records.ledger, tax_year=2026)
        assert totals == {"stcg": 300.0, "ltcg": 50.0, "income": 7.0}

    def test_format_5_bundle_with_flat_koinly_slot_still_reads(self):
        raw = _build({"koinly": {}, "brokerage": {}})
        raw["format_version"] = 5
        raw["records"]["ledger"]["koinly"] = {
            "you": {"tax_year": 2026, "stcg": 40.0, "ltcg": 4.0, "income": 2.0}
        }
        assert classify_bundle(raw) == "v5"
        parsed = parse_v5(raw)
        totals = derive_koinly_totals(parsed.records.ledger, tax_year=2026)
        assert totals == {"stcg": 40.0, "ltcg": 4.0, "income": 2.0}

    @pytest.mark.parametrize("version", [7, 12])
    def test_newer_version_refused_as_newer(self, version):
        with pytest.raises(UnsupportedBundleVersionError, match="newer version") as exc:
            classify_bundle({"format_version": version})
        assert str(version) in str(exc.value)

    def test_a_reader_of_1_to_5_refuses_the_export(self):
        # What every build before this one does: classify_bundle accepted
        # exactly {1, 2, 3, 4, 5} and refused anything else.
        older_build_reads = {1, 2, 3, 4, 5}
        assert _build(_per_year_ledger())["format_version"] not in older_build_reads


# ---------------------------------------------------------------------------
# #97 -- persistent caption for kept (stale) grants/shares
# ---------------------------------------------------------------------------


class _Resp:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return {}


def _sync_with(monkeypatch: pytest.MonkeyPatch, awards, shares) -> PortfolioSnapshot:
    from engine.portfolio_sync import portfolio as portfolio_module
    from engine.portfolio_sync.shapes import HoldingsSnapshot

    monkeypatch.setattr(portfolio_module, "_get", lambda *_a, **_kw: _Resp())
    monkeypatch.setattr(
        portfolio_module,
        "fetch_holdings_snapshot",
        lambda: HoldingsSnapshot(
            server_available=True,
            rows=[{"account": "Brokerage One", "symbol": "AAA", "market_value": 10.0}],
            as_of="2026-10-06",
            as_of_source="sync_time",
        ),
    )
    monkeypatch.setattr(portfolio_module, "fetch_equity_awards", lambda: awards)
    monkeypatch.setattr(portfolio_module, "fetch_shares", lambda: shares)
    return portfolio_module.fetch_portfolio()


class TestIssue97KeptSavedCaption:
    def test_partial_failure_records_what_was_kept(self, monkeypatch):
        assert _sync_with(monkeypatch, awards=None, shares=[]).kept_saved == ["grants"]
        assert _sync_with(monkeypatch, awards=[], shares=None).kept_saved == ["shares"]
        assert _sync_with(monkeypatch, awards=None, shares=None).kept_saved == [
            "grants",
            "shares",
        ]
        assert _sync_with(monkeypatch, awards=[], shares=[]).kept_saved == []

    def test_kept_saved_survives_save_and_load(self, monkeypatch):
        from engine.portfolio_sync.portfolio import load_snapshot, save_snapshot

        save_snapshot(_sync_with(monkeypatch, awards=None, shares=[]))
        assert load_snapshot().kept_saved == ["grants"]

        # A later sync that fetches both clears it.
        save_snapshot(_sync_with(monkeypatch, awards=[], shares=[]))
        assert load_snapshot().kept_saved == []

    def test_note_names_only_what_was_kept(self):
        snap = PortfolioSnapshot(kept_saved=["shares"])
        assert kept_saved_note(snap, "grants") is None
        note = kept_saved_note(snap, "shares")
        assert note is not None
        assert "shares" in note
        assert "sync again" in note.lower()
        assert kept_saved_note(None, "grants") is None

    def test_setup_grants_table_shows_caption(self):
        def app():
            import streamlit as st

            from engine.portfolio_sync.shapes import EquityGrant, PortfolioSnapshot
            from models.household import Household
            from views.setup._partials._options import render_options_partial

            st.session_state["portfolio_snapshot"] = PortfolioSnapshot(
                equity_grants=[EquityGrant("G-1", "NQO", "2021-02-01", 10, 10, 100.0)],
                kept_saved=["grants"],
            )
            render_options_partial(Household(), st)

        at = AppTest.from_function(app).run()
        assert not at.exception
        captions = [c.value for c in at.caption]
        assert any("grants" in c and "sync again" in c.lower() for c in captions), captions

    def test_portfolio_shares_section_shows_caption(self):
        def app():
            from engine.portfolio_sync.shapes import PortfolioSnapshot
            from views.portfolio import render_stock_shares

            render_stock_shares(
                PortfolioSnapshot(
                    stock_shares_held=25, stock_shares_value=500.0, kept_saved=["shares"]
                )
            )

        at = AppTest.from_function(app).run()
        assert not at.exception
        captions = [c.value for c in at.caption]
        assert any("shares" in c and "sync again" in c.lower() for c in captions), captions

    def test_no_caption_after_a_full_fetch(self):
        def app():
            from engine.portfolio_sync.shapes import PortfolioSnapshot
            from views.portfolio import render_stock_shares

            render_stock_shares(PortfolioSnapshot(stock_shares_held=25, stock_shares_value=500.0))

        at = AppTest.from_function(app).run()
        assert not any("sync again" in c.value.lower() for c in at.caption)


# ---------------------------------------------------------------------------
# #98 -- retire the legacy .user_defaults.py after a full migration
# ---------------------------------------------------------------------------


@pytest.fixture
def legacy_py(monkeypatch) -> Path:
    """A trusted legacy ``.user_defaults.py`` beside the (redirected) JSON file, in the cwd."""
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
    folder = loader._USER_DEFAULTS_PATH.parent
    monkeypatch.chdir(folder)
    py = folder / ".user_defaults.py"
    py.write_text(
        "OVERRIDES = {'your_age': 70, 'grant_strikes': {'2020': 11.0}, 'employer_name': 'Synth'}\n"
    )
    py.chmod(0o600)
    return py


def _json() -> dict:
    return json.loads(loader._USER_DEFAULTS_PATH.read_text())


class TestIssue98RetireLegacyPy:
    def test_full_migration_writes_every_key_then_renames(self, legacy_py):
        loader.save_user_defaults({"your_age": 71})

        assert not legacy_py.exists()
        assert legacy_py.with_name(".user_defaults.py.migrated").is_file()
        saved = _json()
        assert saved["your_age"] == 71, "the saved value wins over the legacy one"
        assert saved["grant_strikes"] == {"2020": 11.0}
        assert saved["employer_name"] == "Synth"
        after = loader.load_defaults()
        assert after["grant_strikes"] == {"2020": 11.0}
        assert after["employer_name"] == "Synth"

    def test_json_value_wins_over_legacy_during_migration(self, legacy_py):
        loader._USER_DEFAULTS_PATH.write_text(json.dumps({"employer_name": "Newer"}))
        loader.save_user_defaults({"your_age": 71})
        assert _json()["employer_name"] == "Newer"

    def test_removed_setting_does_not_come_back(self, legacy_py):
        loader.save_user_defaults({"your_age": 71})
        data = _json()
        del data["employer_name"]
        loader._USER_DEFAULTS_PATH.write_text(json.dumps(data))

        assert "employer_name" not in loader.load_raw_user_defaults()

    def test_failed_write_keeps_the_py(self, legacy_py, monkeypatch):
        def fail(self, *_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", fail)
        loader.save_user_defaults({"your_age": 71})
        monkeypatch.undo()

        assert legacy_py.is_file()
        assert not legacy_py.with_name(".user_defaults.py.migrated").exists()

    def test_incomplete_migration_keeps_the_py(self, legacy_py, monkeypatch):
        real = loader._load_overrides_from_json

        def drops_a_key(path):
            data = dict(real(path))
            data.pop("employer_name", None)
            return data

        monkeypatch.setattr(loader, "_load_overrides_from_json", drops_a_key)
        loader.save_user_defaults({"your_age": 71})

        assert legacy_py.is_file()

    def test_untrusted_py_is_neither_read_nor_renamed(self, legacy_py):
        legacy_py.chmod(0o666)
        loader.save_user_defaults({"your_age": 71})

        assert legacy_py.is_file()
        assert "employer_name" not in _json()

    def test_existing_migrated_copy_is_not_overwritten(self, legacy_py):
        old = legacy_py.with_name(".user_defaults.py.migrated")
        old.write_text("OVERRIDES = {}\n")

        loader.save_user_defaults({"your_age": 71})

        assert not legacy_py.exists()
        assert old.read_text() == "OVERRIDES = {}\n"
        assert legacy_py.with_name(".user_defaults.py.migrated-2").is_file()

    def test_py_outside_the_json_folder_is_never_renamed(self, legacy_py, tmp_path, monkeypatch):
        # The read path is cwd-relative; the rename only touches a .py in the
        # same folder as the JSON being written (so a test redirecting the JSON
        # can never rename a developer's real file in the cwd).
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        py = elsewhere / ".user_defaults.py"
        py.write_text("OVERRIDES = {'employer_name': 'Synth'}\n")
        py.chmod(0o600)

        loader.save_user_defaults({"your_age": 71})

        assert py.is_file()

    def test_ignore_flag_never_renames(self, legacy_py, monkeypatch):
        monkeypatch.setenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", "1")
        loader.save_user_defaults({"your_age": 71})
        assert legacy_py.is_file()

    def test_demo_mode_never_renames(self, legacy_py, monkeypatch):
        monkeypatch.setattr(loader, "demo_for_run", lambda: True)
        loader.save_user_defaults({"your_age": 71})
        assert legacy_py.is_file()
