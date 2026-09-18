"""A role-swapped bundle import must carry JOINT fields the receiver never set.

``build_user_defaults_session_updates(..., as_spouse=True)`` mapped only the 10
``your_*`` -> ``spouse_*`` pairs and returned, dropping every joint field:

    filing_status, living_expenses, cpi_assumption, growth_rate,
    stock_price_now, txn_price_growth_rate, aca_benchmark_premium_annual,
    aca_enhanced_subsidies_active, advance_aptc_annual,
    medicare_part_b_base_monthly, spouse_is_sole_beneficiary

These are shared household values that cannot legitimately differ between two
people. A receiver who never set them keeps ``config/defaults.py``'s synthetic
Acme demo values while believing they imported real data -- ``filing_status``
alone moves every tax bracket, ``living_expenses`` drives the withdrawal
waterfall, and the ACA/Medicare pair drives subsidy and surcharge math.

WHY THE PROBE READS THE PERSISTED FILE, NOT SESSION STATE
---------------------------------------------------------
"Take the sender's value when the receiver never set this" needs an observable
signal for "never set". Session-state absence cannot be that signal:
``_seed_session_state()`` (``app.py:30-97``, invoked at ``app.py:101``) runs at
module top level -- long before any import handler -- and seeds all 11 joint
keys via ``setdefault``. Eight are literals (``app.py:49,50,58,59,60,61,83,84``);
the other three arrive through the ``:44-46`` SCALAR_KEYS loop because
``DEFAULTS`` carries them. So at import time every joint key is always present.
A test built from a hand-made dict could omit keys and go green while the app
did nothing -- the "green BECAUSE of the bug" shape.

Value equality against the defaults is equally unsound: ``.user_defaults.json``
is written by the app itself (``config/loader.py:123-152``) whenever Setup data
changes, so on an established install "equals the default" means the user
deliberately set it. That rule would overwrite exactly the values chosen most
deliberately.

The raw persisted file is the only honest signal, and
``TestFiresUnderProductionSeeding`` below pins that it fires with every joint
key present in session state.
"""

from __future__ import annotations

import json

import pytest

from engine.upload_merge import JOINT_FIELDS, build_user_defaults_session_updates

# A sender payload carrying a value for every joint field, all distinguishable
# from any default so a leak or a miss is unambiguous.
SENDER_JOINT = {
    "filing_status": "Single",
    "living_expenses": 111_000,
    "cpi_assumption": 0.031,
    "growth_rate": 3.3,
    "stock_price_now": 333,
    "txn_price_growth_rate": 4.4,
    "aca_benchmark_premium_annual": 13_000,
    "aca_enhanced_subsidies_active": True,
    "advance_aptc_annual": 2_200,
    "medicare_part_b_base_monthly": 199.9,
    "spouse_is_sole_beneficiary": True,
}

# What the same keys look like in the receiver's persisted .user_defaults.json
# once they have deliberately set them on the Setup page.
RECEIVER_PERSISTED = {
    "filing_status": "MFJ",
    "living_expenses": 60_000,
    "cpi_assumption": 0.025,
    "growth_rate": 7.0,
    "stock_price_now": 100,
    "txn_price_growth_rate": 7.0,
    "aca_benchmark_premium_annual": None,
    "aca_enhanced_subsidies_active": False,
    "advance_aptc_annual": 0,
    "medicare_part_b_base_monthly": 185.0,
    "spouse_is_sole_beneficiary": False,
}

# The 10 per-person pairs that must keep working untouched.
SENDER_YOUR_FIELDS = {
    "your_age": 61,
    "your_ira": 1_700_000,
    "your_roth": 250_000,
    "your_ss_fra": 3_400,
    "your_ss_start_age": 70,
    "your_rmd_start_age": 75,
    "your_fra_age": 67,
    "your_aca": True,
    "your_defer_first_rmd": True,
    "your_has_workplace_plan": True,
}


def _session_key(file_key: str) -> str:
    """Mirror the one alias in the scalar schema (engine/upload_merge.py:93)."""
    return "txn_price" if file_key == "stock_price_now" else file_key


class TestFreshReceiverTakesSenderJointValues:
    """No persisted file -> the receiver never set these -> take the sender's."""

    @pytest.mark.parametrize("file_key", sorted(JOINT_FIELDS))
    def test_joint_field_crosses_when_receiver_has_no_persisted_value(self, file_key: str) -> None:
        updates = build_user_defaults_session_updates(
            dict(SENDER_JOINT), as_spouse=True, receiver_persisted={}
        )
        assert updates[_session_key(file_key)] == SENDER_JOINT[file_key]

    def test_no_conflicts_recorded_when_receiver_has_nothing(self) -> None:
        updates = build_user_defaults_session_updates(
            dict(SENDER_JOINT), as_spouse=True, receiver_persisted={}
        )
        assert "_bundle_joint_conflicts" not in updates

    def test_stock_price_now_lands_on_txn_price_not_its_file_key(self) -> None:
        updates = build_user_defaults_session_updates(
            dict(SENDER_JOINT), as_spouse=True, receiver_persisted={}
        )
        assert updates["txn_price"] == 333
        assert "stock_price_now" not in updates


class TestEstablishedReceiverKeepsOwnAndRecordsConflict:
    """A persisted value means a deliberate choice -- never overwrite it."""

    @pytest.mark.parametrize("file_key", sorted(JOINT_FIELDS))
    def test_receiver_value_is_not_overwritten(self, file_key: str) -> None:
        updates = build_user_defaults_session_updates(
            dict(SENDER_JOINT), as_spouse=True, receiver_persisted=dict(RECEIVER_PERSISTED)
        )
        assert _session_key(file_key) not in updates

    def test_every_disagreement_is_recorded(self) -> None:
        updates = build_user_defaults_session_updates(
            dict(SENDER_JOINT), as_spouse=True, receiver_persisted=dict(RECEIVER_PERSISTED)
        )
        conflicts = updates["_bundle_joint_conflicts"]
        assert {c["field"] for c in conflicts} == set(JOINT_FIELDS)

    def test_conflict_carries_both_values_for_display(self) -> None:
        updates = build_user_defaults_session_updates(
            dict(SENDER_JOINT), as_spouse=True, receiver_persisted=dict(RECEIVER_PERSISTED)
        )
        entry = next(c for c in updates["_bundle_joint_conflicts"] if c["field"] == "filing_status")
        assert entry["mine"] == "MFJ"
        assert entry["theirs"] == "Single"

    def test_agreeing_values_are_not_reported_as_conflicts(self) -> None:
        agreed = {**RECEIVER_PERSISTED, "filing_status": "Single"}
        updates = build_user_defaults_session_updates(
            dict(SENDER_JOINT), as_spouse=True, receiver_persisted=agreed
        )
        fields = {c["field"] for c in updates.get("_bundle_joint_conflicts", [])}
        assert "filing_status" not in fields

    def test_no_conflicts_key_when_everything_agrees(self) -> None:
        updates = build_user_defaults_session_updates(
            dict(SENDER_JOINT), as_spouse=True, receiver_persisted=dict(SENDER_JOINT)
        )
        assert "_bundle_joint_conflicts" not in updates


class TestBundleMissingKeyChangesNothing:
    def test_absent_from_bundle_is_never_written(self) -> None:
        updates = build_user_defaults_session_updates(
            {"filing_status": "Single"}, as_spouse=True, receiver_persisted={}
        )
        assert updates["filing_status"] == "Single"
        for other in JOINT_FIELDS:
            if other != "filing_status":
                assert _session_key(other) not in updates

    def test_absent_from_bundle_is_never_a_conflict(self) -> None:
        updates = build_user_defaults_session_updates(
            {}, as_spouse=True, receiver_persisted=dict(RECEIVER_PERSISTED)
        )
        assert "_bundle_joint_conflicts" not in updates


class TestExistingBehaviourIsUnchanged:
    """Guards on the two rules that must NOT move."""

    @pytest.mark.parametrize(
        ("file_key", "session_key"),
        [
            ("your_age", "spouse_age"),
            ("your_ira", "spouse_ira"),
            ("your_roth", "spouse_roth"),
            ("your_ss_fra", "spouse_ss_fra"),
            ("your_ss_start_age", "spouse_ss_start_age"),
            ("your_rmd_start_age", "spouse_rmd_start_age"),
            ("your_fra_age", "spouse_fra_age"),
            ("your_aca", "spouse_aca"),
            ("your_defer_first_rmd", "spouse_defer_first_rmd"),
            ("your_has_workplace_plan", "spouse_has_workplace_plan"),
        ],
    )
    def test_ten_your_to_spouse_mappings_still_work(self, file_key: str, session_key: str) -> None:
        updates = build_user_defaults_session_updates(
            dict(SENDER_YOUR_FIELDS), as_spouse=True, receiver_persisted={}
        )
        assert updates[session_key] == SENDER_YOUR_FIELDS[file_key]

    @pytest.mark.parametrize(
        "spouse_named_key",
        [
            "spouse_age",
            "spouse_ira",
            "spouse_roth",
            "spouse_ss_fra",
            "spouse_ss_start_age",
            "spouse_rmd_start_age",
            "spouse_fra_age",
            "spouse_aca",
            "spouse_defer_first_rmd",
            "spouse_has_workplace_plan",
        ],
    )
    def test_spouse_named_keys_are_still_dropped(self, spouse_named_key: str) -> None:
        """On the sender's side these hold THEIR view of their spouse -- i.e. the
        receiver. Cross-mapping them would write the receiver's own stale data
        back over itself, so they must stay dropped."""
        payload = {spouse_named_key: "SENTINEL"}
        updates = build_user_defaults_session_updates(
            payload, as_spouse=True, receiver_persisted={}
        )
        assert updates.get(spouse_named_key) != "SENTINEL"

    def test_omitting_receiver_persisted_preserves_the_old_contract(self) -> None:
        """Callers that have not been updated must behave exactly as before."""
        updates = build_user_defaults_session_updates(
            {**SENDER_JOINT, **SENDER_YOUR_FIELDS}, as_spouse=True
        )
        assert updates["spouse_age"] == 61
        for file_key in JOINT_FIELDS:
            assert _session_key(file_key) not in updates
        assert "_bundle_joint_conflicts" not in updates

    def test_non_spouse_path_is_untouched(self) -> None:
        updates = build_user_defaults_session_updates(
            dict(SENDER_JOINT), as_spouse=False, receiver_persisted={}
        )
        assert updates["filing_status"] == "Single"
        assert updates["txn_price"] == 333
        assert "_bundle_joint_conflicts" not in updates


class TestRawPersistedProbe:
    """config.loader.load_raw_user_defaults -- the file-presence signal."""

    def test_missing_file_reads_as_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config.loader as loader

        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        assert loader.load_raw_user_defaults() == {}

    def test_returns_raw_keys_without_the_defaults_overlay(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The whole point: load_defaults() merges DEFAULTS on top, which would
        make living_expenses / stock_price_now / spouse_is_sole_beneficiary look
        'present' on a brand-new install and kill the fresh-receiver branch."""
        import config.loader as loader

        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        path = tmp_path / ".user_defaults.json"
        path.write_text(json.dumps({"filing_status": "Single"}))
        monkeypatch.setattr(loader, "_USER_DEFAULTS_PATH", path)

        raw = loader.load_raw_user_defaults()
        assert raw == {"filing_status": "Single"}
        assert "living_expenses" not in raw
        assert "stock_price_now" not in raw

    def test_honours_the_test_isolation_flag(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Mirrors load_defaults(): when the suite asks for isolation from a
        developer's real override file, the raw probe must agree rather than
        reading a file load_defaults() is ignoring."""
        import config.loader as loader

        path = tmp_path / ".user_defaults.json"
        path.write_text(json.dumps({"filing_status": "Single"}))
        monkeypatch.setattr(loader, "_USER_DEFAULTS_PATH", path)
        monkeypatch.setenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", "1")

        assert loader.load_raw_user_defaults() == {}


class TestFiresUnderProductionSeeding:
    """The test that proves this is not a no-op in the running app.

    Every joint key is seeded into session_state before any import handler runs
    (app.py:101). This drives the REAL ``_handle_personal_uploads`` with that
    seeding in place and asserts the sender's joint values still cross -- which
    is exactly what a session-state-absence rule could never achieve.
    """

    @staticmethod
    def _sender_bundle() -> dict:
        from engine.bridge_bundle import build_bundle

        return build_bundle(
            {**SENDER_JOINT, **SENDER_YOUR_FIELDS},
            None,
            {"koinly": {}, "brokerage": {}},
            owner="you",
            ytd=None,
        )

    def _run(self, monkeypatch: pytest.MonkeyPatch, *, persisted: dict) -> dict:
        import streamlit as st_mod
        from streamlit.testing.v1 import AppTest

        import config.loader as loader_mod
        import engine.data_bridge_crypto as crypto_mod
        import views.setup.data_bridge as bridge_mod
        from engine.portfolio_sync import PortfolioSnapshot

        payload_bytes = json.dumps(self._sender_bundle()).encode("utf-8")

        monkeypatch.setattr(crypto_mod, "open_uploaded_payload", lambda raw, privkey: payload_bytes)
        monkeypatch.setattr(bridge_mod, "load_snapshot", lambda: PortfolioSnapshot())
        monkeypatch.setattr(bridge_mod, "save_snapshot", lambda snap, **kwargs: None)
        monkeypatch.setattr(bridge_mod, "_load_pdf_ledger", lambda: {})
        monkeypatch.setattr(bridge_mod, "_save_pdf_ledger", lambda ledger: None)
        monkeypatch.setattr(bridge_mod, "save_ytd_snapshot", lambda snap: None)
        monkeypatch.setattr(bridge_mod, "load_ytd_snapshot", lambda: None)
        monkeypatch.setattr(bridge_mod, "_resolve_privkey_bytes", lambda: None)
        monkeypatch.setattr(bridge_mod, "load_pubkey", lambda: None)
        monkeypatch.setattr(loader_mod, "load_raw_user_defaults", lambda: dict(persisted))
        monkeypatch.setattr(st_mod, "rerun", lambda: None)

        def _render(seeded=None) -> None:
            import streamlit as st

            from views.setup.data_bridge import _handle_personal_uploads

            st.session_state["instance_owner"] = "you"
            # Mirror app.py's _seed_session_state(): in production EVERY joint
            # key is already present here before any import runs.
            for key, value in (seeded or {}).items():
                st.session_state.setdefault(key, value)
            _handle_personal_uploads()

        seeded = {_session_key(k): v for k, v in RECEIVER_PERSISTED.items()}
        at = AppTest.from_function(_render, kwargs={"seeded": seeded})
        at.run()
        uploader = next(w for w in at.file_uploader if w.key == "bundle_upload")
        uploader.set_value(("roth_bridge.enc", payload_bytes, "application/octet-stream"))
        button = next(b for b in at.button if b.key == "apply_uploads")
        button.set_value(True)
        at.run()
        assert not at.exception
        return dict(at.session_state["_pending_defaults"])

    def test_fresh_receiver_gets_sender_joint_values_despite_seeded_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pending = self._run(monkeypatch, persisted={})
        assert pending["filing_status"] == "Single"
        assert pending["living_expenses"] == 111_000
        assert pending["txn_price"] == 333

    def test_established_receiver_keeps_own_values_and_gets_conflicts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pending = self._run(monkeypatch, persisted=dict(RECEIVER_PERSISTED))
        assert "filing_status" not in pending
        assert "living_expenses" not in pending
        conflicts = pending["_bundle_joint_conflicts"]
        assert {c["field"] for c in conflicts} == set(JOINT_FIELDS)


class TestConflictsAreVisible:
    """A conflict record nobody renders is worth nothing."""

    def _render_with(self, conflicts: list[dict] | None):
        from streamlit.testing.v1 import AppTest

        def _render(staged=None) -> None:
            import streamlit as st

            from views.setup.data_bridge import _render_joint_field_conflicts

            if staged is not None:
                st.session_state["_bundle_joint_conflicts"] = staged
            _render_joint_field_conflicts()

        at = AppTest.from_function(_render, kwargs={"staged": conflicts})
        at.run()
        assert not at.exception
        return at

    def test_warning_names_the_field_and_both_values(self) -> None:
        at = self._render_with([{"field": "filing_status", "mine": "MFJ", "theirs": "Single"}])
        body = "\n".join(w.value for w in at.warning)
        assert "filing_status" in body
        assert "MFJ" in body
        assert "Single" in body

    def test_nothing_is_rendered_when_there_are_no_conflicts(self) -> None:
        assert len(self._render_with(None).warning) == 0

    def test_warning_clears_after_being_shown_once(self) -> None:
        at = self._render_with([{"field": "growth_rate", "mine": 7.0, "theirs": 3.3}])
        assert "_bundle_joint_conflicts" not in at.session_state
