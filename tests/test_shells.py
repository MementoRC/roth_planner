"""Tests for ``views/shells/`` — the single surviving Domains shell.

Classic/Hub/Contextual/Wizard were retired when the UI shell was collapsed
to Domains-only; this module keeps the guarantees that still apply to the
one shell that remains:
  1. Smoke: the shell renders without exception for a demo household.
  2. Key-set parity: editing ``your_ira`` through the shell updates the
     expected ``session_state`` key (no silent fork of the data model —
     Owner decisions 4/5 in
     ``docs/superpowers/plans/2026-07-24-ui-shell-theme-toggle.md``).
  3. The "Import 1040 PDF" workflow parity fix (originally added for
     Domains/Hub post-Task-8) still renders and reuses Classic's old widget
     key.
  4. audit-0823 M2: the shell's autosave still reaches ``save_user_defaults``,
     respects ``_suppress_snapshot_autoload``, and carries session-edited
     values.

Each is exercised directly via ``AppTest.from_function`` with a small
self-contained session-state seed (not via the real ``app.py`` — mirrors the
original Task 8/9 rationale of testing the shell in isolation).

``AppTest.from_function`` extracts and execs only the target function's OWN
source text in a fresh namespace — it does NOT carry along this module's
other top-level names (a sibling helper function is invisible inside the
executed function, confirmed empirically). So the seed logic below lives
entirely INSIDE the one function passed to ``AppTest.from_function``.

The bundle export/import symmetry tests further down (Task 8 of the original
plan) exercise ``views/setup/data_bridge.py`` helpers directly and have no
dependency on the theme axis — they are unchanged by the shell collapse.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


def _render_shell(seed_1040_scanned: bool = False) -> None:
    """AppTest.from_function target: seed a minimal demo session_state, then
    render the Domains shell.

    Seeds the fields every partial reads without a ``.get()`` fallback
    (``your_ira``/``spouse_ira``/``your_ss_fra``/``spouse_ss_fra``/
    ``txn_price``/``growth_rate``/``living_expenses``), plus a few more for
    determinism — a trimmed, shell-test-scoped mirror of
    ``app.py:_seed_session_state`` (values sourced from
    ``config.defaults.DEFAULTS`` to stay in sync with the real app's demo
    numbers).

    ``seed_1040_scanned=True`` additionally seeds
    ``st.session_state["_pdf_1040_scanned"]`` with a fake scanned
    Form1040Record, exercising the "Import 1040 PDF" section's confirmation
    UI.
    """
    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from engine.tax_return_pdf import Form1040Record
    from models.household import Household
    from views.shells import render_setup

    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("txn_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    if seed_1040_scanned:
        st.session_state["_pdf_1040_scanned"] = {
            2024: Form1040Record(
                tax_year=2024,
                agi=280_000.0,
                tax_exempt_interest=1_000.0,
                taxable_ss=0.0,
                qualified_dividends=0.0,
                ordinary_dividends=0.0,
                feie=0.0,
                magi=281_000.0,
                filing_status=None,
                captured_at="2026-07-17T00:00:00+00:00",
            )
        }

    render_setup(Household())


def _run_shell(monkeypatch, seed_1040_scanned: bool = False) -> AppTest:
    """Run the Domains shell under ``AppTest``, neutralizing local-disk
    sources of non-determinism the same way
    ``tests/test_setup_shell_characterization.py``'s ``setup_app_test``
    fixture does (a developer's real V2 pubkey / PDF-tax cache must not leak
    into these tests).
    """
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_function(_render_shell, kwargs={"seed_1040_scanned": seed_1040_scanned})
    at.run()
    return at


def _number_input_by_label(at: AppTest, label: str):
    return next(w for w in at.number_input if w.label == label)


# --- Smoke test: the shell renders without exception ------------------------


def test_shell_renders_without_exception(clean_command_center_caches, monkeypatch) -> None:
    at = _run_shell(monkeypatch)
    assert not at.exception


# --- Key-set parity: editing touches the same session_state key -------------


def test_your_ira_edit_updates_same_session_state_key(
    clean_command_center_caches, monkeypatch
) -> None:
    """Setting ``your_ira`` through the shell must update the identical
    ``session_state["your_ira"]`` key — proves it doesn't fork the data
    model onto a differently-named key.
    """
    at = _run_shell(monkeypatch)
    assert not at.exception

    _number_input_by_label(at, "Your Trad IRA").set_value(999_000).run()

    assert not at.exception
    assert at.session_state["your_ira"] == 999_000


# --- 1040 PDF import section: parity fix -------------------------------------


def test_1040_import_section_renders_without_exception(
    clean_command_center_caches, monkeypatch
) -> None:
    """The "Import 1040 PDF" workflow (``_render_pdf_1040_import``) is
    reachable from the shell. With no scanned record pending, it should
    render its "scan on YTD Income" caption without exception.
    """
    at = _run_shell(monkeypatch)
    assert not at.exception


def test_1040_import_section_reuses_widget_key(clean_command_center_caches, monkeypatch) -> None:
    """With a scanned 1040 record pending, the confirmation selectbox must
    carry the EXACT SAME key Classic's copy of this widget used
    (``_pdf_1040_filing_status_2024``) — proving this section reuses the
    original widget key rather than minting a new one (plan Owner decision
    4: no session_state key renames/forks).
    """
    at = _run_shell(monkeypatch, seed_1040_scanned=True)
    assert not at.exception

    matches = [w for w in at.selectbox if w.key == "_pdf_1040_filing_status_2024"]
    assert len(matches) == 1, (
        f"expected exactly one selectbox with key '_pdf_1040_filing_status_2024', "
        f"found {len(matches)}"
    )


# --- Task 8: bundle export/import symmetry ---------------------------------


def test_export_stamps_owner_from_instance(monkeypatch) -> None:
    """Export from a spouse-set instance stamps owner="spouse" in the bundle,
    not the old hardcoded "you"."""
    from streamlit.testing.v1 import AppTest

    import engine.bridge_bundle as bridge_bundle_mod
    import engine.data_bridge_crypto as data_bridge_crypto_mod
    import views.setup.data_bridge as data_bridge_mod

    captured: dict[str, object] = {}

    def _fake_build_bundle(scalars, snapshot, ledger, *, owner="you", ytd=None, grants=None):
        captured["owner"] = owner
        return {"format_version": 4}

    # build_bundle/seal are imported INSIDE _handle_personal_exports (deferred
    # for Pyodide), so they must be patched on their defining modules.
    monkeypatch.setattr(bridge_bundle_mod, "build_bundle", _fake_build_bundle)
    monkeypatch.setattr(data_bridge_crypto_mod, "seal", lambda payload, pubkey: b"sealed")
    monkeypatch.setattr(data_bridge_mod, "_resolved_pubkey", lambda: b"\x00" * 32)
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
    monkeypatch.setattr(
        data_bridge_mod, "_load_pdf_ledger", lambda: {"koinly": {}, "brokerage": {}}
    )
    monkeypatch.setattr(data_bridge_mod, "load_ytd_snapshot", lambda: None)

    def _render() -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_exports

        st.session_state["instance_owner"] = "spouse"
        _handle_personal_exports()

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    assert captured["owner"] == "spouse"


def test_import_targets_the_other_person_with_no_radio(monkeypatch) -> None:
    """Import into a "you" instance targets "spouse" automatically; the
    "Whose data?" pc_role radio no longer renders."""
    from streamlit.testing.v1 import AppTest

    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)

    def _render() -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads, _import_target_owner

        st.session_state["instance_owner"] = "you"
        _handle_personal_uploads()
        # The derivation helper is asserted directly here as a focused unit
        # check (AppTest *can* populate the file_uploader and drive the real
        # Apply body -- see test_apply_uploads_end_to_end_imports_as_other_owner
        # below, which covers that end-to-end path instead). "Spouse" is the
        # radio's default choice, matching the un-driven widget state below.
        st.session_state["_test_target_owner"] = _import_target_owner("Spouse")

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    assert not any(w.key == "pc_role" for w in at.radio)
    assert at.session_state["_test_target_owner"] == "spouse"


def _run_uploads(monkeypatch, instance_owner: str | None):
    from streamlit.testing.v1 import AppTest

    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)

    def _render(owner: str | None = None) -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads

        if owner is not None:
            st.session_state["instance_owner"] = owner
        _handle_personal_uploads()

    at = AppTest.from_function(_render, kwargs={"owner": instance_owner})
    at.run()
    return at


def test_apply_uploads_disabled_when_instance_owner_unset(monkeypatch) -> None:
    at = _run_uploads(monkeypatch, None)

    assert not at.exception
    assert next(b for b in at.button if b.key == "apply_uploads").disabled is True


def test_apply_uploads_end_to_end_imports_as_other_owner(monkeypatch) -> None:
    """Supersedes the removed test_apply_uploads_enabled_when_instance_owner_set.

    That test was vacuous: it asserted ``disabled is False`` on the Apply
    button, but ``False`` is ALSO exactly what Streamlit produces when the
    ``disabled=`` kwarg is omitted entirely (the container-default trap) --
    it passed identically against code with the gate deleted (a mutation
    check confirmed 3/4, not 4/4). No assertion about the *enabled* state
    can distinguish ``disabled=not identity_set`` (with identity set) from a
    missing kwarg; they are behaviourally identical.
    ``test_apply_uploads_disabled_when_instance_owner_unset`` above remains
    the gate's only real guard and is unchanged.

    This test instead drives the real Apply path end to end: it populates
    the ``bundle_upload`` file_uploader with real bytes and clicks Apply,
    asserting the import ran as "spouse". This also newly covers the
    success message rendered at ``views/setup/data_bridge.py:381``
    (``st.success(f"Applied: {bundle_file.name} ({target_owner}). Rerunning…")``),
    which had never had test coverage in this repo before this test.
    """
    import json

    import streamlit as st_mod
    from streamlit.testing.v1 import AppTest

    import engine.data_bridge_crypto as data_bridge_crypto_mod
    import views.setup.data_bridge as data_bridge_mod

    minimal_bundle = {
        "format_version": 4,
        "sections": {
            "setup_scalars": {},
            "portfolio": {"accounts": []},
        },
    }
    payload_bytes = json.dumps(minimal_bundle).encode("utf-8")

    captured: dict[str, object] = {}

    def _fake_apply_bundle(target_owner, bundle, *, existing_snapshot, existing_ledger):
        captured["target_owner"] = target_owner
        return existing_snapshot, existing_ledger

    # open_uploaded_payload is imported INSIDE _handle_personal_uploads (deferred
    # for Pyodide), so it must be patched on its defining module -- same idiom
    # as `seal` in test_export_stamps_owner_from_instance above.
    monkeypatch.setattr(
        data_bridge_crypto_mod, "open_uploaded_payload", lambda raw, privkey: payload_bytes
    )
    monkeypatch.setattr(data_bridge_mod, "apply_bundle", _fake_apply_bundle)
    monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "save_snapshot", lambda snap, **kwargs: None)
    monkeypatch.setattr(data_bridge_mod, "_load_pdf_ledger", lambda: {})
    monkeypatch.setattr(data_bridge_mod, "_save_pdf_ledger", lambda ledger: None)
    monkeypatch.setattr(data_bridge_mod, "_resolve_privkey_bytes", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    # Neutralise st.rerun() at data_bridge.py:382 so the success element
    # (rendered immediately before it) survives to the end of this run
    # instead of being wiped by a fresh script execution.
    monkeypatch.setattr(st_mod, "rerun", lambda: None)

    def _render() -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads

        st.session_state["instance_owner"] = "you"
        _handle_personal_uploads()

    at = AppTest.from_function(_render)
    at.run()
    assert not at.exception

    uploader = next(w for w in at.file_uploader if w.key == "bundle_upload")
    uploader.set_value(("roth_bridge.enc", payload_bytes, "application/octet-stream"))
    apply_button = next(b for b in at.button if b.key == "apply_uploads")
    apply_button.set_value(True)
    at.run()

    assert not at.exception
    assert captured["target_owner"] == "spouse"

    success_texts = [s.value for s in at.success]
    assert any("roth_bridge.enc" in t and "spouse" in t for t in success_texts), success_texts


@pytest.mark.parametrize("whose", ["Spouse", "Me"])
def test_import_never_adopts_ytd_from_sender(monkeypatch, whose: str) -> None:
    """REGRESSION (PR #497 property): the receiver's own wages/withholding/etc.
    must never be overwritten by the sender's YTD figures, for EITHER
    "Whose data is in this file?" choice -- the whose-data radio changes
    which owner SLOT the import targets, not whether YTD crosses at all
    (views/setup/data_bridge.py:417-442)."""
    import json

    import streamlit as st_mod
    from streamlit.testing.v1 import AppTest

    import engine.data_bridge_crypto as data_bridge_crypto_mod
    import views.setup.data_bridge as data_bridge_mod

    sender_bundle = {
        "format_version": 4,
        "sections": {
            "setup_scalars": {},
            "portfolio": {"accounts": []},
            "ytd": {"wages_ytd": 100_000.0, "ira_conversions_ytd": 30_000.0},
        },
    }
    payload_bytes = json.dumps(sender_bundle).encode("utf-8")

    def _fake_apply_bundle(target_owner, bundle, *, existing_snapshot, existing_ledger):
        return existing_snapshot, existing_ledger

    monkeypatch.setattr(
        data_bridge_crypto_mod, "open_uploaded_payload", lambda raw, privkey: payload_bytes
    )
    monkeypatch.setattr(data_bridge_mod, "apply_bundle", _fake_apply_bundle)
    monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "save_snapshot", lambda snap, **kwargs: None)
    monkeypatch.setattr(data_bridge_mod, "_load_pdf_ledger", lambda: {})
    monkeypatch.setattr(data_bridge_mod, "_save_pdf_ledger", lambda ledger: None)
    monkeypatch.setattr(data_bridge_mod, "_resolve_privkey_bytes", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    # Pyodide-like: no persistent filesystem, so no receiver snapshot to load.
    monkeypatch.setattr(data_bridge_mod, "load_ytd_snapshot", lambda: None)
    monkeypatch.setattr(st_mod, "rerun", lambda: None)

    def _render() -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads

        st.session_state["instance_owner"] = "you"
        _handle_personal_uploads()

    at = AppTest.from_function(_render)
    at.run()
    uploader = next(w for w in at.file_uploader if w.key == "bundle_upload")
    uploader.set_value(("roth_bridge.enc", payload_bytes, "application/octet-stream"))
    if whose == "Me":
        at.radio(key="import_whose_data").set_value("Me")
    apply_button = next(b for b in at.button if b.key == "apply_uploads")
    apply_button.set_value(True)
    at.run()

    assert not at.exception
    ytd_snap = at.session_state["ytd_snapshot"]
    assert ytd_snap.wages_ytd != 100_000.0
    assert ytd_snap.ira_conversions_ytd != 30_000.0


def test_export_disabled_and_build_skipped_when_instance_owner_unset(monkeypatch) -> None:
    """Spec gap 1: the export control is gated on instance identity the same
    way scan/sync/import are gated (design spec:52, :127-131). An export
    built while identity is unset would poison the RECEIVING instance by
    stamping the ``or "you"`` fallback onto a bundle that may actually be
    the spouse's -- so this asserts BOTH that the download control is
    disabled AND that ``build_bundle`` is never called, since
    ``st.download_button``'s ``data=`` argument is evaluated eagerly on
    every render regardless of the ``disabled``/clicked state, and
    ``disabled=True`` alone would still let a mislabelled bundle be built.
    """
    from unittest.mock import MagicMock

    import streamlit as st_mod
    from streamlit.testing.v1 import AppTest

    import engine.bridge_bundle as bridge_bundle_mod
    import views.setup.data_bridge as data_bridge_mod

    # AppTest's element_tree parser has no case for the "download_button"
    # proto type (verified against streamlit/testing/v1/element_tree.py --
    # unlike st.button, st.download_button is not exposed via an
    # `at.download_button` accessor), so the disabled kwarg is asserted by
    # spying on st.download_button itself rather than walking the AppTest
    # element tree. `streamlit.download_button` is a plain module attribute
    # bound to `_main.download_button` (`streamlit/__init__.py:205`), and
    # `views/setup/data_bridge.py` does `import streamlit as st`, so
    # patching the streamlit module's attribute is visible there.
    download_button_spy = MagicMock(return_value=False)
    monkeypatch.setattr(st_mod, "download_button", download_button_spy)
    build_bundle_spy = MagicMock()
    monkeypatch.setattr(bridge_bundle_mod, "build_bundle", build_bundle_spy)
    monkeypatch.setattr(data_bridge_mod, "_resolved_pubkey", lambda: b"\x00" * 32)
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)

    def _render() -> None:
        from views.setup.data_bridge import _handle_personal_exports

        # instance_owner deliberately left unset -- this is the gate's guard.
        _handle_personal_exports()

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    download_button_spy.assert_called_once()
    call_kwargs = download_button_spy.call_args.kwargs
    assert call_kwargs["key"] == "export_bundle"
    assert call_kwargs["disabled"] is True
    build_bundle_spy.assert_not_called()


@pytest.mark.parametrize(
    ("instance_owner", "expected_label"),
    # The "spouse" case previously expected "Your data" here, which encoded
    # a pre-existing bug: the caption compared target_owner against the
    # literal "spouse" instead of deriving from the radio choice, inverting
    # the label on any non-"you" instance. Fixed in this PR (see
    # _import_target_label in views/setup/data_bridge.py) — the label is
    # operator-relative to the radio choice ("Spouse" -> "Spouse's data",
    # "Me" -> "Your data"), independent of which instance renders it.
    [("you", "Spouse's data"), ("spouse", "Spouse's data")],
)
def test_import_statement_names_concrete_target_owner(
    monkeypatch, instance_owner: str, expected_label: str
) -> None:
    """Spec gap 2: design spec:97 requires the "Whose data?" radio be
    replaced by "a statement of what will happen ('Importing as: Spouse's
    data') with no control" -- not a generic caption that never names WHICH
    person before Apply is clicked."""
    from streamlit.testing.v1 import AppTest

    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)

    def _render(owner: str) -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads

        st.session_state["instance_owner"] = owner
        _handle_personal_uploads()

    at = AppTest.from_function(_render, kwargs={"owner": instance_owner})
    at.run()

    assert not at.exception
    captions = [c.value for c in at.caption]
    assert any(f"Importing as: **{expected_label}**" in c for c in captions), captions


@pytest.mark.parametrize(
    ("instance_owner", "whose", "expected"),
    [
        ("you", "Spouse", "spouse"),  # unchanged historical inversion
        ("you", "Me", "you"),
        ("spouse", "Me", "spouse"),
        ("spouse", "Spouse", "you"),
    ],
)
def test_import_target_owner_resolution(
    monkeypatch, instance_owner: str, whose: str, expected: str
) -> None:
    """Unit-level regression guard for the whose-data -> target-owner
    mapping, independent of widget rendering. "Spouse" is the unchanged,
    historical inversion; "Me" targets this instance's own (un-inverted)
    identity, for restoring your own backup."""
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "_this_instance_owner", lambda owner=instance_owner: owner)
    assert data_bridge_mod._import_target_owner(whose) == expected


def test_import_whose_data_radio_defaults_to_spouse(monkeypatch) -> None:
    """The "Whose data is in this file?" radio must default to "Spouse"
    (index 0) when the user touches nothing, so an accidental future flip
    to "Me" (which would silently redirect every untouched import into the
    user's OWN slot) is caught."""
    from streamlit.testing.v1 import AppTest

    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)

    def _render() -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads

        st.session_state["instance_owner"] = "you"
        _handle_personal_uploads()

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    radio = at.radio(key="import_whose_data")
    assert radio.index == 0
    assert radio.value == "Spouse"


@pytest.mark.parametrize(
    ("instance_owner", "whose", "expected_label"),
    [
        ("you", "Spouse", "Spouse's data"),
        ("you", "Me", "Your data"),
        ("spouse", "Me", "Your data"),
        ("spouse", "Spouse", "Spouse's data"),
    ],
)
def test_import_caption_agrees_with_applied_target_for_both_choices(
    monkeypatch, instance_owner: str, whose: str, expected_label: str
) -> None:
    """The "Importing as" caption and the value Apply would use must come
    from the same resolution — this drives the radio to each choice and
    confirms the caption names the target that choice actually produces,
    for BOTH "Spouse" and "Me" (design spec §7(a))."""
    from streamlit.testing.v1 import AppTest

    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)

    def _render(owner: str) -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads

        st.session_state["instance_owner"] = owner
        _handle_personal_uploads()

    at = AppTest.from_function(_render, kwargs={"owner": instance_owner})
    at.run()
    at.radio(key="import_whose_data").set_value(whose).run()

    assert not at.exception
    captions = [c.value for c in at.caption]
    assert any(f"Importing as: **{expected_label}**" in c for c in captions), captions


def test_export_disabled_and_build_skipped_when_instance_owner_corrupt(monkeypatch) -> None:
    """Corrupt-identity companion to
    ``test_export_disabled_and_build_skipped_when_instance_owner_unset``: a
    genuinely corrupt ``.instance_owner.json`` must degrade through
    ``_this_instance_owner()``'s ``except CorruptInstanceOwnerError: return
    None`` handler to the exact same "unset" treatment -- export gated and
    ``build_bundle`` never called -- rather than crashing the tab or, worse,
    silently exporting under a guessed owner.
    """
    from unittest.mock import MagicMock

    import streamlit as st_mod
    from streamlit.testing.v1 import AppTest

    import engine.bridge_bundle as bridge_bundle_mod
    import engine.instance_identity as instance_identity_mod
    import views.setup.data_bridge as data_bridge_mod

    def _raise_corrupt() -> str | None:
        raise instance_identity_mod.CorruptInstanceOwnerError(
            instance_identity_mod.INSTANCE_OWNER_PATH, ValueError("bad json")
        )

    download_button_spy = MagicMock(return_value=False)
    monkeypatch.setattr(st_mod, "download_button", download_button_spy)
    build_bundle_spy = MagicMock()
    monkeypatch.setattr(bridge_bundle_mod, "build_bundle", build_bundle_spy)
    monkeypatch.setattr(data_bridge_mod, "_resolved_pubkey", lambda: b"\x00" * 32)
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "load_instance_owner", _raise_corrupt)

    def _render() -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_exports

        # instance_owner deliberately left unset in session_state --
        # _this_instance_owner() reads session_state FIRST and a seeded
        # value would short-circuit load_instance_owner(), making the
        # corrupt path (and this test) vacuous.
        assert "instance_owner" not in st.session_state
        _handle_personal_exports()

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    download_button_spy.assert_called_once()
    call_kwargs = download_button_spy.call_args.kwargs
    assert call_kwargs["key"] == "export_bundle"
    assert call_kwargs["disabled"] is True
    build_bundle_spy.assert_not_called()


def test_apply_uploads_disabled_and_no_importing_as_statement_when_corrupt(
    monkeypatch,
) -> None:
    """Corrupt-identity companion to
    ``test_apply_uploads_disabled_when_instance_owner_unset`` and
    ``test_import_statement_names_concrete_target_owner``: a genuinely
    corrupt ``.instance_owner.json`` must disable Apply AND suppress the
    "Importing as" statement entirely -- not render it naming a guessed
    target. ``_import_target_owner()`` falls back through ``or "you"`` and
    would confidently report "spouse" even on a corrupt file, so the
    statement must be gated on ``identity_set``, not derived independently.
    """
    from streamlit.testing.v1 import AppTest

    import engine.instance_identity as instance_identity_mod
    import views.setup.data_bridge as data_bridge_mod

    def _raise_corrupt() -> str | None:
        raise instance_identity_mod.CorruptInstanceOwnerError(
            instance_identity_mod.INSTANCE_OWNER_PATH, ValueError("bad json")
        )

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "load_instance_owner", _raise_corrupt)

    def _render() -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads

        # instance_owner deliberately left unset in session_state -- see
        # the corrupt-export test above for why a seeded value would make
        # this vacuous.
        assert "instance_owner" not in st.session_state
        _handle_personal_uploads()

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    assert next(b for b in at.button if b.key == "apply_uploads").disabled is True
    captions = [c.value for c in at.caption]
    assert not any("Importing as" in c for c in captions), captions


# --- audit-0823 M2: shell autosave parity -----------------------------------
#
# The Domains shell composes views/setup/_partials/ directly and never
# routed through views/setup/parameters.py:render_parameters_tab, so it
# never reached the save_user_defaults() autosave Classic/Contextual got for
# free (both now deleted). _render_shell()/_run_shell() above always seed
# _suppress_snapshot_autoload = True (needed to keep the OTHER shell tests
# from touching disk-autoload concerns), which would also suppress the
# autosave itself and make these tests vacuous -- so this section uses its
# own no-suppress seed/runner pair instead of reusing _run_shell.


def _render_shell_no_suppress() -> None:
    """Same seed as ``_render_shell`` above, minus ``_suppress_snapshot_autoload``
    -- needed so the real (non-suppressed) autosave path actually fires.
    """
    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from views.shells import render_setup

    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("txn_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    render_setup(Household())


def _run_shell_no_suppress(monkeypatch) -> AppTest:
    """``_run_shell``'s disk-source neutralization, paired with the
    no-suppress seed above instead of ``_render_shell``."""
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_function(_render_shell_no_suppress)
    at.run()
    return at


def _patch_state_autosave(monkeypatch):
    """Patch ``save_user_defaults`` at ``views.setup._state``'s point of use --
    the module the audit-0823/M2 fix binds the name into via
    ``autosave_user_defaults()``.
    """
    from unittest.mock import MagicMock

    import views.setup._state as state_mod

    spy = MagicMock()
    monkeypatch.setattr(state_mod, "save_user_defaults", spy, raising=False)
    return spy


def test_shell_autosave_reaches_save_user_defaults(
    clean_command_center_caches, monkeypatch
) -> None:
    """audit-0823 M2: the Domains shell must persist session edits.

    Without this, edits made through the shell vanish on restart.
    """
    spy = _patch_state_autosave(monkeypatch)
    at = _run_shell_no_suppress(monkeypatch)
    assert not at.exception
    assert spy.called, "Domains shell did not reach save_user_defaults"


def test_shell_autosave_suppressed_by_snapshot_autoload_guard(
    clean_command_center_caches, monkeypatch
) -> None:
    """``_suppress_snapshot_autoload=True`` must suppress the save -- the
    sentinel is session-wide, not shell-specific (app.py:108/122,
    views/setup/_state.py:212). Uses ``_run_shell`` (not the no-suppress
    variant), which already seeds the flag.
    """
    spy = _patch_state_autosave(monkeypatch)
    at = _run_shell(monkeypatch)
    assert not at.exception
    assert not spy.called, (
        "Domains shell called save_user_defaults despite _suppress_snapshot_autoload=True"
    )


def test_shell_autosave_payload_carries_session_edited_value(
    clean_command_center_caches, monkeypatch
) -> None:
    """Non-vacuous companion to test_shell_autosave_reaches_save_user_defaults:
    proves the ACTUAL payload dict reaching save_user_defaults carries a
    session-edited value, not merely that the function was called with
    something."""
    spy = _patch_state_autosave(monkeypatch)
    at = _run_shell_no_suppress(monkeypatch)
    assert not at.exception

    _number_input_by_label(at, "Your Trad IRA").set_value(999_000).run()
    assert not at.exception

    assert spy.called
    payload = spy.call_args.args[0]
    assert payload.get("your_ira") == 999_000
