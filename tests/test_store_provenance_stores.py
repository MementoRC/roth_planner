"""Each provenance and mapping store reads and writes its namespace (PR 3).

All values are synthetic.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import engine.store as store_pkg
from engine.account_attribution import (
    CorruptAccountAttributionError,
    delete_account_override,
    load_account_overrides,
    save_account_override,
)
from engine.account_link import NEW, load_link_map, save_link_map
from engine.data_sources.candidate_store import CandidateStore, CorruptCandidateStoreError
from engine.data_sources.choices import ChoiceMap, CorruptChoiceMapError, TrustChoice
from engine.data_sources.committed import (
    CorruptCommittedCacheError,
    load_committed,
    save_committed,
)
from engine.data_sources.record import discard_pdf_balance_candidates, record_ira_balance_candidate
from engine.instance_identity import (
    CorruptInstanceOwnerError,
    load_instance_owner,
    save_instance_owner,
)
from engine.pdf_owner import load_owner_map, save_owner_map
from engine.portfolio_sync.shapes import SSABenefitEstimate, SSASnapshot
from engine.portfolio_sync.social_security import save_ssa_snapshot
from engine.store import SINGLE_KEY, CorruptStoreError, Store, StoreUnavailableError, set_store
from engine.store.backend import RoutedBackend
from engine.store.json_readonly import JsonReadOnlyBackend
from engine.store.legacy_files import registered
from engine.store.migrate_json import MigrationResult
from engine.store.namespaces import (
    ACCOUNT_ATTRIBUTION,
    CANDIDATE_STORE,
    COMMITTED,
    INSTANCE_OWNER,
    IRA_ACCOUNT_LINKS,
    PDF_OWNER_MAP,
    TRUST_CHOICES,
)
from engine.store.sqlite_backend import MEMORY, SqliteBackend
from engine.tax_return_pdf import Form1040Record, save_pdf_tax_records
from models.sourced import Provenance, Source, SourcedValue

_T0 = datetime(2026, 1, 1, 12, 0, 0)


def _baseline(value: float) -> dict:
    return {"your_ira": SourcedValue(value, Provenance(Source.MANUAL, _T0)).to_json()}


class TestCommittedStore:
    def test_round_trip(self, store):
        save_committed(_baseline(1_000.0))
        assert store.get(COMMITTED) == _baseline(1_000.0)
        assert load_committed() == _baseline(1_000.0)

    def test_absent_reads_none(self):
        assert load_committed() is None

    def test_corrupt_row_raises_and_refuses_a_save(self, store, corrupt_row):
        payload = corrupt_row(COMMITTED)
        with pytest.raises(CorruptCommittedCacheError, match="corrupt store row"):
            load_committed()
        with pytest.raises(CorruptCommittedCacheError, match="corrupt store row"):
            save_committed(_baseline(1.0))
        assert store.backend.get(COMMITTED, SINGLE_KEY).payload == payload

    def test_wrong_shape_is_corrupt_and_kept(self, store):
        store.put(COMMITTED, [1, 2])
        with pytest.raises(CorruptCommittedCacheError, match="JSON object"):
            load_committed()
        with pytest.raises(CorruptCommittedCacheError, match="JSON object"):
            save_committed(_baseline(1.0))
        assert store.get(COMMITTED) == [1, 2]


class TestCandidateStore:
    def test_round_trip(self, store):
        cands = CandidateStore()
        cands.record_candidate("your_ira", 1_000.0, Provenance(Source.MANUAL, _T0))
        cands.save()
        assert set(store.get(CANDIDATE_STORE)) == {"your_ira"}
        loaded = CandidateStore.load()
        assert loaded.candidates_for("your_ira")[0].value == 1_000.0
        assert loaded.load_corrupt is False

    def test_absent_is_empty_and_not_corrupt(self):
        loaded = CandidateStore.load()
        assert loaded.field_keys() == []
        assert loaded.load_corrupt is False

    def test_corrupt_row_loads_empty_flagged_and_refuses_a_save(self, store, corrupt_row):
        payload = corrupt_row(CANDIDATE_STORE)
        loaded = CandidateStore.load()
        assert loaded.field_keys() == []
        assert loaded.load_corrupt is True
        with pytest.raises(CorruptCandidateStoreError, match="corrupt store row"):
            loaded.save()
        assert store.backend.get(CANDIDATE_STORE, SINGLE_KEY).payload == payload

    def test_wrong_shape_is_corrupt_and_kept(self, store):
        store.put(CANDIDATE_STORE, [1, 2])
        assert CandidateStore.load().load_corrupt is True
        with pytest.raises(CorruptCandidateStoreError, match="JSON object"):
            CandidateStore().save()
        assert store.get(CANDIDATE_STORE) == [1, 2]

    def test_record_helpers_use_the_saved_store(self, store):
        assert record_ira_balance_candidate("your_ira", 1_000.4, "synthetic", _T0)
        [cand] = CandidateStore.load().candidates_for("your_ira")
        assert cand.value == 1_000.0
        assert discard_pdf_balance_candidates(["you"]) == 1
        assert CandidateStore.load().candidates_for("your_ira") == []


class TestTrustChoicesStore:
    def test_round_trip(self, store):
        cm = ChoiceMap()
        cm.set_choice("your_ira", Source.PDF, _T0)
        cm.save()
        assert set(store.get(TRUST_CHOICES)) == {"your_ira"}
        assert ChoiceMap.load().get("your_ira") == TrustChoice(Source.PDF, _T0)

    def test_absent_is_empty_and_not_corrupt(self):
        assert ChoiceMap.load().load_corrupt is False

    def test_corrupt_row_loads_empty_flagged_and_refuses_a_save(self, store, corrupt_row):
        payload = corrupt_row(TRUST_CHOICES)
        loaded = ChoiceMap.load()
        assert loaded.get("your_ira") is None
        assert loaded.load_corrupt is True
        with pytest.raises(CorruptChoiceMapError, match="corrupt store row"):
            loaded.save()
        assert store.backend.get(TRUST_CHOICES, SINGLE_KEY).payload == payload

    def test_unknown_source_is_corrupt_and_kept(self, store):
        wrong = {"your_ira": {"source": "NOT_A_SOURCE", "locked_at": "2026-01-01T00:00:00"}}
        store.put(TRUST_CHOICES, wrong)
        assert ChoiceMap.load().load_corrupt is True
        with pytest.raises(CorruptChoiceMapError, match="NOT_A_SOURCE"):
            ChoiceMap().save()
        assert store.get(TRUST_CHOICES) == wrong


class TestInstanceOwnerStore:
    def test_round_trip_keeps_the_versioned_document(self, store):
        save_instance_owner("spouse")
        assert store.get(INSTANCE_OWNER) == {"version": 1, "instance_owner": "spouse"}
        assert load_instance_owner() == "spouse"

    def test_absent_is_unset(self):
        assert load_instance_owner() is None

    def test_corrupt_row_raises_and_refuses_a_save(self, store, corrupt_row):
        payload = corrupt_row(INSTANCE_OWNER)
        with pytest.raises(CorruptInstanceOwnerError, match="corrupt store row"):
            load_instance_owner()
        with pytest.raises(CorruptInstanceOwnerError, match="corrupt store row"):
            save_instance_owner("you")
        assert store.backend.get(INSTANCE_OWNER, SINGLE_KEY).payload == payload

    def test_an_invalid_owner_value_is_corrupt(self, store):
        store.put(INSTANCE_OWNER, {"version": 1, "instance_owner": "household"})
        with pytest.raises(CorruptInstanceOwnerError, match="invalid instance_owner"):
            load_instance_owner()


class TestAccountAttributionStore:
    def test_round_trip_keeps_the_versioned_document(self, store):
        save_account_override("synthbroker", "ACCT-1", "spouse")
        assert store.get(ACCOUNT_ATTRIBUTION) == {
            "version": 1,
            "overrides": {"synthbroker|ACCT-1": "spouse"},
        }
        assert load_account_overrides() == {("synthbroker", "ACCT-1"): "spouse"}
        delete_account_override("synthbroker", "ACCT-1")
        assert load_account_overrides() == {}

    def test_corrupt_row_reads_empty_and_refuses_both_writes(self, store, corrupt_row):
        payload = corrupt_row(ACCOUNT_ATTRIBUTION)
        assert load_account_overrides() == {}
        with pytest.raises(CorruptAccountAttributionError, match="corrupt store row"):
            save_account_override("synthbroker", "ACCT-1", "you")
        with pytest.raises(CorruptAccountAttributionError, match="corrupt store row"):
            delete_account_override("synthbroker", "ACCT-1")
        assert store.backend.get(ACCOUNT_ATTRIBUTION, SINGLE_KEY).payload == payload


class TestOwnerMapAndLinkMapStores:
    def test_owner_map_round_trip(self, store):
        save_owner_map({"owner a": "you"})
        assert store.get(PDF_OWNER_MAP) == {"owner a": "you"}
        assert load_owner_map() == {"owner a": "you"}

    def test_link_map_round_trip_saves_account_keys_as_lists(self, store):
        link_map = {
            "synthbroker|X1234": ("trad_ira", "you", "IRA X1234"),
            "synthbroker|X5678": NEW,
        }
        save_link_map(link_map)
        assert store.get(IRA_ACCOUNT_LINKS) == {
            "synthbroker|X1234": ["trad_ira", "you", "IRA X1234"],
            "synthbroker|X5678": "NEW",
        }
        assert load_link_map() == link_map

    def test_corrupt_rows_read_empty_and_refuse_a_save(self, store, corrupt_row):
        owner_payload = corrupt_row(PDF_OWNER_MAP)
        link_payload = corrupt_row(IRA_ACCOUNT_LINKS)
        assert load_owner_map() == {}
        assert load_link_map() == {}
        with pytest.raises(CorruptStoreError, match="pdf_owner_map"):
            save_owner_map({})
        with pytest.raises(CorruptStoreError, match="ira_account_links"):
            save_link_map({})
        assert store.backend.get(PDF_OWNER_MAP, SINGLE_KEY).payload == owner_payload
        assert store.backend.get(IRA_ACCOUNT_LINKS, SINGLE_KEY).payload == link_payload


_APP = Path(__file__).resolve().parent.parent / "app.py"
_PR3_STORES = (
    ACCOUNT_ATTRIBUTION,
    CANDIDATE_STORE,
    COMMITTED,
    INSTANCE_OWNER,
    IRA_ACCOUNT_LINKS,
    PDF_OWNER_MAP,
    TRUST_CHOICES,
)


def _ssa_snapshot(amount: float) -> SSASnapshot:
    return SSASnapshot(
        estimates=[SSABenefitEstimate(67, "2034-01-01", "retirement", amount)],
        server_available=True,
    )


def _form_1040(agi: float) -> dict[int, Form1040Record]:
    return {
        2024: Form1040Record(
            tax_year=2024,
            agi=agi,
            tax_exempt_interest=0.0,
            taxable_ss=0.0,
            qualified_dividends=0.0,
            ordinary_dividends=0.0,
            feie=0.0,
            magi=agi,
            filing_status=None,
            captured_at="2026-01-01T00:00:00",
        )
    }


@pytest.fixture
def store_refusing_saves(tmp_path):
    """A store whose PR 3 namespaces read JSON (none here) and refuse every save."""
    entries = [e for e in registered() if e.namespace in _PR3_STORES]
    read_only = JsonReadOnlyBackend(tmp_path, entries, reason="importing .x.json failed")
    store = Store(RoutedBackend(SqliteBackend(MEMORY), dict.fromkeys(_PR3_STORES, read_only)))
    set_store(store)
    yield store
    store.backend.close()


@pytest.fixture
def store_saving_disabled(store_refusing_saves, monkeypatch):
    """What a failed PR 3 import leaves: the import banner, and saves refused."""
    monkeypatch.setattr(
        store_pkg,
        "_migration_result",
        MigrationResult(failed_file=".x.json", error="JSONDecodeError: x", read_only=_PR3_STORES),
    )
    return store_refusing_saves


def _storage_banners(at: AppTest) -> list[str]:
    errors = [e.value for e in at.error]
    return [e for e in errors if "Storage unavailable" in e or "Saved data" in e]


def _no_storage_banner(at: AppTest) -> bool:
    return not _storage_banners(at)


class TestRenderWhileSavingIsDisabled:
    @pytest.mark.parametrize("page", ["review", "import", "overview"])
    def test_page_renders_with_only_the_import_banner(self, store_saving_disabled, page):
        at = AppTest.from_file(str(_APP), default_timeout=60)
        at.session_state["_suppress_snapshot_autoload"] = True
        at.session_state["nav_page"] = page
        at.run()
        assert not at.exception
        assert any("Could not import .x.json" in e.value for e in at.error)
        assert _no_storage_banner(at)

    def test_review_page_still_offers_sync_everything(self, store_saving_disabled):
        at = AppTest.from_file(str(_APP), default_timeout=60)
        at.session_state["_suppress_snapshot_autoload"] = True
        at.session_state["nav_page"] = "review"
        at.run()
        assert not at.exception
        assert at.button(key="sync_everything_btn") is not None

    @pytest.mark.parametrize("cached", ["ssa", "form_1040"])
    def test_boot_candidate_recording_is_skipped_quietly(
        self, store_saving_disabled, monkeypatch, cached
    ):
        """A cached SSA snapshot / 1040 record makes app.py record a candidate at boot."""
        import engine.data_sources.record as record_mod

        refused = _spy_refusals(
            monkeypatch, record_mod, ("record_ss_fra_candidate", "record_magi_candidates")
        )
        if cached == "ssa":
            save_ssa_snapshot(_ssa_snapshot(1_000.0), owner="you")
            expected = "record_ss_fra_candidate"
        else:
            save_pdf_tax_records(_form_1040(50_000.0))
            expected = "record_magi_candidates"

        at = AppTest.from_file(str(_APP), default_timeout=60)
        at.session_state["_suppress_snapshot_autoload"] = True
        at.session_state["your_fra_age"] = 67
        at.run()

        # The real recording ran, was refused, and app.py swallowed the refusal.
        assert expected in refused, "the boot-time candidate recording was not refused"
        assert not at.exception
        assert _no_storage_banner(at)

    def test_other_refused_saves_show_one_storage_banner(self, store_refusing_saves, monkeypatch):
        """No failed import: a refused save is not quiet, but boot and resolve share one banner."""
        import engine.data_sources.committed as committed_mod
        import engine.data_sources.record as record_mod

        refused = _spy_refusals(monkeypatch, record_mod, ("record_magi_candidates",))
        _spy_refusals(monkeypatch, committed_mod, ("save_committed",), refused)
        save_pdf_tax_records(_form_1040(50_000.0))
        at = AppTest.from_file(str(_APP), default_timeout=60)
        at.session_state["_suppress_snapshot_autoload"] = True
        at.session_state["nav_page"] = "overview"
        at.run()
        assert not at.exception
        assert "record_magi_candidates" in refused, "the boot-time refusal did not fire"
        assert "save_committed" in refused, "the resolve-time refusal did not fire"
        banners = _storage_banners(at)
        assert len(banners) == 1
        assert "saving is disabled" in banners[0]


_CORRUPT_CANDIDATE_WARNING = "Your candidate store is unreadable"


class TestCorruptCandidateStoreAtBoot:
    @pytest.mark.parametrize("cached", ["ssa", "form_1040"])
    def test_boot_recording_warns_once_instead_of_crashing(self, store, corrupt_row, cached):
        """A cached SSA snapshot / 1040 record records a candidate onto the corrupt row."""
        payload = corrupt_row(CANDIDATE_STORE)
        if cached == "ssa":
            save_ssa_snapshot(_ssa_snapshot(1_000.0), owner="you")
        else:
            save_pdf_tax_records(_form_1040(50_000.0))

        at = AppTest.from_file(str(_APP), default_timeout=60)
        at.session_state["_suppress_snapshot_autoload"] = True
        at.session_state["your_fra_age"] = 67
        at.session_state["nav_page"] = "overview"
        at.run()

        assert not at.exception, [e.value for e in at.exception]
        warnings = [w.value for w in at.warning if _CORRUPT_CANDIDATE_WARNING in w.value]
        assert len(warnings) == 1
        assert store.backend.get(CANDIDATE_STORE, SINGLE_KEY).payload == payload


def _lock_store_after_page_body(monkeypatch, *, page_raises: bool = False) -> list[str]:
    """The overview page body runs, then the pending-review recompute finds the store locked."""
    import engine.data_sources.committed as committed_mod
    import views.dashboard as dashboard_mod

    armed: list[str] = []
    real_load = committed_mod.load_committed

    def page(*_args, **_kwargs):
        armed.append("page")
        if page_raises:
            raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")

    def load_committed_spy():
        if armed:
            armed.append("recompute")
            raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")
        return real_load()

    monkeypatch.setattr(dashboard_mod, "render", page)
    monkeypatch.setattr(committed_mod, "load_committed", load_committed_spy)
    return armed


def _run_overview() -> AppTest:
    at = AppTest.from_file(str(_APP), default_timeout=60)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "overview"
    at.run()
    return at


class TestPendingRecomputeRefusal:
    def test_unavailable_store_shows_one_storage_banner(self, monkeypatch):
        armed = _lock_store_after_page_body(monkeypatch)
        at = _run_overview()
        assert not at.exception, [e.value for e in at.exception]
        assert "recompute" in armed, "the pending recompute never met the locked store"
        banners = _storage_banners(at)
        assert len(banners) == 1
        assert "database is locked" in banners[0]

    def test_a_page_banner_is_not_repeated_by_the_recompute(self, monkeypatch):
        armed = _lock_store_after_page_body(monkeypatch, page_raises=True)
        at = _run_overview()
        assert not at.exception, [e.value for e in at.exception]
        assert "recompute" in armed
        assert len(_storage_banners(at)) == 1

    def test_quiet_while_saving_is_disabled(self, store_saving_disabled, monkeypatch):
        armed = _lock_store_after_page_body(monkeypatch)
        at = _run_overview()
        assert not at.exception, [e.value for e in at.exception]
        assert "recompute" in armed
        assert _no_storage_banner(at)


def _spy_refusals(
    monkeypatch, module, names: tuple[str, ...], refused: list[str] | None = None
) -> list[str]:
    """Wrap *names* in *module*; *refused* collects each name whose real call was refused."""
    refused = [] if refused is None else refused
    for name in names:
        real = getattr(module, name)

        def spy(*args, _real=real, _name=name, **kwargs):
            try:
                return _real(*args, **kwargs)
            except StoreUnavailableError:
                refused.append(_name)
                raise

        monkeypatch.setattr(module, name, spy)
    return refused
