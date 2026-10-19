"""The data-bridge import previews what a file declares before anything is written.

Plan ``docs/superpowers/plans/2026-09-30-bridge-v5-envelope.md`` task B4, spec
``docs/superpowers/specs/2026-09-20-import-data-ownership-design.md`` §5 stage 4:

- the caption states what the FILE declares (who exported it, which slots it
  carries), not a guess from this machine's identity;
- each slot lists what it would overwrite, and a slot captured before the
  copy this planner holds is flagged;
- a v5 slot can be deselected; a legacy v4 file, which cannot declare whose
  data it holds, gets an absolute slot picker (Spouse by default).

The preview renders in the SAME script run as Apply, above it, so the
one-click ``set uploader -> click apply_uploads -> run`` harness used across
the bridge suites keeps working without a second click.
"""

from __future__ import annotations

import json

import pytest

from engine.bridge_bundle import build_bundle, build_bundle_v5

UPLOAD_NAME = "roth_bridge.enc"


@pytest.fixture
def bridge_env(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Isolate the import from disk. Plaintext payloads need no key."""
    import streamlit as st_mod

    import config.loader as loader_mod
    import views.setup.data_bridge as mod

    env: dict = {"snapshot": None, "ledger": {}, "ytd": None, "persisted": {}, "saved": {}}
    monkeypatch.setattr(mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(mod, "_resolve_privkey_bytes", lambda: None)
    monkeypatch.setattr(mod, "load_snapshot", lambda: env["snapshot"])
    monkeypatch.setattr(
        mod, "save_snapshot", lambda snap, **kwargs: env["saved"].__setitem__("snapshot", snap)
    )
    monkeypatch.setattr(mod, "_load_pdf_ledger", lambda: env["ledger"])
    monkeypatch.setattr(
        mod, "_save_pdf_ledger", lambda ledger: env["saved"].__setitem__("ledger", ledger)
    )
    monkeypatch.setattr(mod, "load_ytd_snapshot", lambda: env["ytd"])
    monkeypatch.setattr(
        mod, "save_ytd_snapshot", lambda snap: env["saved"].__setitem__("ytd", snap)
    )
    monkeypatch.setattr(loader_mod, "load_raw_user_defaults", lambda: dict(env["persisted"]))
    monkeypatch.setattr(st_mod, "rerun", lambda: None)
    return env


def _render(owner: str | None = None, seed: dict | None = None) -> None:
    import streamlit as st

    from views.setup.data_bridge import _handle_personal_uploads

    if owner is not None:
        st.session_state["instance_owner"] = owner
    for key, value in (seed or {}).items():
        st.session_state.setdefault(key, value)
    _handle_personal_uploads()


def _app(owner: str | None = "you", seed: dict | None = None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_render, kwargs={"owner": owner, "seed": seed})
    at.run()
    assert not at.exception
    return at


def _upload(at, bundle: dict) -> None:
    payload = json.dumps(bundle).encode("utf-8")
    uploader = next(w for w in at.file_uploader if w.key == "bundle_upload")
    uploader.set_value((UPLOAD_NAME, payload, "application/octet-stream"))
    at.run()
    assert not at.exception


def _texts(elements) -> list[str]:
    return [e.value for e in elements]


def _apply_button(at):
    return next(b for b in at.button if b.key == "apply_uploads")


def _v5(
    *,
    exporter: str = "spouse",
    scalars: dict | None = None,
    captured: dict | None = None,
    now: str = "2026-09-30T10:00:00",
) -> dict:
    return build_bundle_v5(
        setup_scalars=scalars or {},
        snapshot=None,
        ledger={"koinly": {}, "brokerage": {}},
        ytd=None,
        grants=None,
        exporter=exporter,
        slot_captured_at=captured,
        now=now,
    )


def _two_slot_from_spouse(**kwargs) -> dict:
    return _v5(exporter="spouse", scalars={"your_age": 62, "spouse_age": 56}, **kwargs)


def _v4() -> dict:
    return build_bundle({"your_age": 62}, None, {}, owner="you")


class TestPreviewCaption:
    def test_preview_caption_states_what_file_declares(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _two_slot_from_spouse())
        captions = _texts(at.caption)
        expected = "This file was exported by **Spouse** and contains: Your data, Spouse's data"
        assert any(expected in c for c in captions), captions

    def test_caption_does_not_depend_on_this_instance_identity(self, bridge_env: dict) -> None:
        """The same file reads the same on either person's planner."""
        expected = "This file was exported by **Spouse** and contains: Your data, Spouse's data"
        for owner in ("you", "spouse", None):
            at = _app(owner=owner)
            _upload(at, _two_slot_from_spouse())
            assert any(expected in c for c in _texts(at.caption)), (owner, _texts(at.caption))

    def test_v4_caption_says_the_file_cannot_declare_a_slot(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _v4())
        captions = " ".join(_texts(at.caption))
        assert "older (v4) file" in captions, captions


class TestPreviewOverwrites:
    def test_preview_lists_overwrites_per_slot(self, bridge_env: dict) -> None:
        at = _app(seed={"your_age": 61, "spouse_age": 56})
        _upload(at, _two_slot_from_spouse())
        body = "\n".join(_texts(at.markdown))
        assert "`your_age`: 61 → 62" in body, body
        # spouse_age already matches, so it is not an overwrite.
        assert "`spouse_age`" not in body, body

    def test_preview_shows_plan_warnings(self, bridge_env: dict) -> None:
        """A slot with no records keeps the local records -- the preview says so."""
        at = _app()
        _upload(at, _two_slot_from_spouse())
        captions = " ".join(_texts(at.caption))
        assert "The you slot carries no accounts or ledger rows" in captions, captions


class TestPreviewStaleness:
    def test_preview_flags_stale_slot(self, bridge_env: dict) -> None:
        bridge_env["persisted"] = {"slot_captured_at": {"you": "2026-09-30T12:00:00"}}
        at = _app()
        _upload(at, _two_slot_from_spouse(captured={"you": "2026-09-01T09:00:00"}))
        warnings = " ".join(_texts(at.warning))
        assert "Your data" in warnings, warnings
        assert "older" in warnings, warnings

    def test_fresh_slot_is_not_flagged(self, bridge_env: dict) -> None:
        bridge_env["persisted"] = {"slot_captured_at": {"you": "2026-09-01T09:00:00"}}
        at = _app()
        _upload(at, _two_slot_from_spouse(captured={"you": "2026-09-30T09:00:00"}))
        assert not any("older" in w for w in _texts(at.warning)), _texts(at.warning)

    def test_missing_timestamp_is_unknown_not_stale(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _two_slot_from_spouse())
        assert not any("older" in w for w in _texts(at.warning)), _texts(at.warning)
        assert any(
            "Freshness unknown — one or both sides have no capture time to compare." in c
            for c in _texts(at.caption)
        ), _texts(at.caption)


class TestSlotControls:
    def test_non_exporter_slot_unticked_by_default(self, bridge_env: dict) -> None:
        """Only the exporter's own slot is ticked; the other is the sender's copy.

        A real export always carries the exporter's copy of the OTHER person too,
        which may be stale. Ticking it by default would let one Apply click
        overwrite the receiver's own figures -- something a v4 import never did.
        """
        at = _app()
        _upload(at, _two_slot_from_spouse())
        assert at.checkbox(key="import_slot_spouse").value is True
        assert at.checkbox(key="import_slot_you").value is False
        captions = " ".join(_texts(at.caption))
        assert "a copy of Your data held by the exporter, not the original" in captions, captions
        # No possessive built from the exporter label ("You's copy" read wrongly).
        assert "'s copy" not in captions, captions

    def test_exporter_slot_gets_no_opt_in_caption(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _v5(exporter="spouse", scalars={"spouse_age": 56}))
        assert at.checkbox(key="import_slot_spouse").value is True
        assert not any("copy of" in c for c in _texts(at.caption)), _texts(at.caption)

    def test_absent_slot_gets_no_checkbox(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _v5(exporter="spouse", scalars={"spouse_age": 56}))
        keys = {c.key for c in at.checkbox}
        assert "import_slot_spouse" in keys
        assert "import_slot_you" not in keys

    def test_new_file_over_old_resets_slot_defaults(self, bridge_env: dict) -> None:
        """audit-1008 F-33: dropping a second file onto the uploader must not
        inherit the first file's ticks. Keyed checkboxes ignore a changed
        ``value=``, so the stash has to clear them when the upload changes."""
        at = _app(seed={"your_age": 61, "spouse_age": 50})
        _upload(at, _v5(exporter="you", scalars={"your_age": 60, "spouse_age": 40}))
        assert at.checkbox(key="import_slot_you").value is True
        assert at.checkbox(key="import_slot_spouse").value is False

        _upload(at, _two_slot_from_spouse())
        assert at.checkbox(key="import_slot_spouse").value is True
        assert at.checkbox(key="import_slot_you").value is False

        _click_apply(at)
        pending = at.session_state["_pending_defaults"]
        assert pending["spouse_age"] == 56
        assert "your_age" not in pending

    def test_rerun_of_same_file_keeps_user_ticks(self, bridge_env: dict) -> None:
        """The reset fires only on a NEW upload, never on an ordinary rerun."""
        at = _app()
        _upload(at, _two_slot_from_spouse())
        at.checkbox(key="import_slot_you").check().run()
        at.run()
        assert at.checkbox(key="import_slot_you").value is True

    def test_v4_picker_defaults_to_spouse(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _v4())
        picker = at.radio(key="import_v4_slot")
        assert picker.value == "spouse"
        assert picker.index == 0

    def test_v4_picker_offers_restoring_own_backup(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _v4())
        options = at.radio(key="import_v4_slot").options
        assert options == ["Spouse's slot", "Your slot — restore my own backup"]

    def test_v4_picker_absent_for_v5(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _two_slot_from_spouse())
        assert not any(r.key == "import_v4_slot" for r in at.radio)

    def test_no_pc_role_widget(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _v4())
        radio_keys = {r.key for r in at.radio}
        assert "pc_role" not in radio_keys
        assert "import_whose_data" not in radio_keys


def _click_apply(at) -> None:
    _apply_button(at).set_value(True)
    at.run()
    assert not at.exception


class TestApplyHonoursThePreview:
    def test_deselect_you_slot_applies_spouse_only(self, bridge_env: dict) -> None:
        at = _app(seed={"your_age": 61, "spouse_age": 50})
        _upload(at, _two_slot_from_spouse())
        at.checkbox(key="import_slot_you").uncheck().run()
        _click_apply(at)

        pending = at.session_state["_pending_defaults"]
        assert pending["spouse_age"] == 56
        assert "your_age" not in pending, "the deselected slot must be left untouched"
        assert any("(spouse)" in s for s in _texts(at.success)), _texts(at.success)

    def test_default_apply_leaves_receivers_own_scalars_untouched(self, bridge_env: dict) -> None:
        """One Apply click on a spouse's export fills Spouse and leaves Your data alone."""
        at = _app(seed={"your_age": 61, "spouse_age": 50})
        _upload(at, _two_slot_from_spouse())
        _click_apply(at)

        pending = at.session_state["_pending_defaults"]
        assert pending["spouse_age"] == 56
        assert "your_age" not in pending, "the sender's copy of you must not land by default"
        assert any("(spouse)" in s for s in _texts(at.success)), _texts(at.success)

    def test_ticking_non_exporter_slot_restores_it(self, bridge_env: dict) -> None:
        """Opting in to the other slot restores both people in one pass."""
        at = _app(seed={"your_age": 61, "spouse_age": 50})
        _upload(at, _two_slot_from_spouse())
        at.checkbox(key="import_slot_you").check().run()
        _click_apply(at)

        pending = at.session_state["_pending_defaults"]
        assert pending["your_age"] == 62
        assert pending["spouse_age"] == 56
        assert any("(you + spouse)" in s for s in _texts(at.success)), _texts(at.success)

    def test_nothing_selected_disables_apply(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _two_slot_from_spouse())
        at.checkbox(key="import_slot_you").uncheck().run()
        at.checkbox(key="import_slot_spouse").uncheck().run()
        assert _apply_button(at).disabled is True
        assert any("Select at least one slot" in c for c in _texts(at.caption))

    def test_apply_records_slot_capture_time_for_the_next_staleness_check(
        self, bridge_env: dict
    ) -> None:
        at = _app()
        _upload(at, _two_slot_from_spouse(now="2026-09-30T10:00:00"))
        _click_apply(at)
        assert at.session_state["_slot_captured_at"]["spouse"] == "2026-09-30T10:00:00"

        # Re-uploading an OLDER copy of the same person is now flagged.
        _upload(at, _two_slot_from_spouse(now="2026-09-01T10:00:00"))
        assert any("Spouse's data" in w and "older" in w for w in _texts(at.warning))


class TestRefusals:
    def test_unknown_version_refused_with_message(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, {"format_version": 9})
        errors = _texts(at.error)
        assert any("Invalid" in e and UPLOAD_NAME in e and "9" in e for e in errors), errors
        assert _apply_button(at).disabled is True

    def test_malformed_v5_refused_before_apply(self, bridge_env: dict) -> None:
        bundle = _two_slot_from_spouse()
        bundle["slots"]["martian"] = bundle["slots"]["you"]
        at = _app()
        _upload(at, bundle)
        assert any("Invalid" in e for e in _texts(at.error)), _texts(at.error)
        assert _apply_button(at).disabled is True

    def test_valid_upload_leaves_apply_enabled(self, bridge_env: dict) -> None:
        at = _app()
        _upload(at, _two_slot_from_spouse())
        assert not _texts(at.error)
        assert _apply_button(at).disabled is False
