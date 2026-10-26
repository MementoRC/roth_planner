"""Data Bridge tab — V2 keypair UX, personal uploads, personal exports."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

import streamlit as st

from config.loader import save_user_defaults
from config.scalar_registry import SCALAR_REGISTRY
from engine.bridge_bundle import (
    BundleV5,
    apply_bundle,
    classify_bundle,
    parse_v5,
    read_bundle_ytd,
    read_format_version,
)
from engine.bridge_import import (
    ImportPlan,
    ImportResult,
    LocalState,
    apply_import,
    commit_import,
    plan_import,
)
from engine.data_bridge_browser import (
    is_pyodide,
)
from engine.data_bridge_keys import (
    PRIVKEY_ENV,
    PUBKEY_ENV,
    decode_keymaterial,
    load_privkey,
    load_pubkey,
)
from engine.data_sources.committed import (
    CorruptCommittedCacheError,
    load_committed,
    save_committed,
)
from engine.data_sources.orchestrator import BUNDLE_SCALARS_KEY, stamp_bundle_scalars
from engine.data_sources.record import discard_pdf_balance_candidates, record_magi_candidates
from engine.demo_mode import is_demo_mode
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner
from engine.pdf_ledger import load_ledger as _load_pdf_ledger
from engine.pdf_ledger import save_ledger as _save_pdf_ledger
from engine.portfolio_sync import PortfolioSnapshot, load_ytd_snapshot, save_ytd_snapshot
from engine.portfolio_sync.portfolio import (
    CorruptPortfolioCacheError,
    EmptySnapshotWriteRefusedError,
    load_snapshot,
    save_snapshot,
)
from models.household import Household
from models.sourced import Source
from models.ytd_income import YTDSnapshot
from views._shared import auto_deselect_manual_entry, operator_slot
from views._store_transaction import store_transaction

from ._partials._import_preview import SLOT_ORDER, render_import_preview, render_v4_slot_picker
from ._state import (
    _clear_personal_session_state,
    _portfolio_snapshot_from_dict,
    _user_defaults_from_session,
    exit_demo_mode,
)


def _resolved_pubkey() -> bytes | None:
    """Resolve V2 public key for encryption.

    Order: session-state pasted private key (browser paste flow, derived to
    its public key) FIRST, then env/dotfile (:func:`load_pubkey`) as
    fallback. Returns ``None`` if no key is available from any source.

    Encrypt and decrypt must resolve from the same source, or a session that
    pastes a key will seal a file it cannot open with that same pasted key.
    The session-pasted key is the more explicit, more recent user intent,
    and it is the only key available on the public Pyodide site (no
    dotfiles in the browser). This ordering deliberately mirrors
    :func:`_resolve_privkey_bytes` — keep the two in lockstep.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import derive_pubkey

    priv_b64 = st.session_state.get("data_bridge_privkey_b64")
    if priv_b64:
        try:
            priv_raw = decode_keymaterial(priv_b64)
        except ValueError:
            pass  # fall through to disk rather than failing outright
        else:
            return derive_pubkey(priv_raw)
    return load_pubkey()


def _pubkey_source_label() -> str:
    """Which key source _resolved_pubkey() will use — for honest UI captions."""
    priv_b64 = st.session_state.get("data_bridge_privkey_b64")
    if priv_b64:
        try:
            decode_keymaterial(priv_b64)
        except ValueError:
            pass
        else:
            return "session key"
    return "local key file" if load_pubkey() is not None else "none"


def _resolve_privkey_bytes() -> bytes | None:
    """Resolve V2 private key for decryption.

    Order: session-state pasted key, then disk dotfile/env via
    :func:`load_privkey`. Returns ``None`` if no key is available.

    This ordering deliberately mirrors :func:`_resolved_pubkey` — keep the
    two in lockstep.
    """
    priv_b64 = st.session_state.get("data_bridge_privkey_b64")
    if priv_b64:
        try:
            return decode_keymaterial(priv_b64)
        except ValueError:
            pass
    return load_privkey()


def _privkey_status() -> tuple[bytes | None, str | None]:
    """Resolve the decryption key without letting a bad key escape as a traceback.

    Returns ``(key, None)`` when a key resolves, ``(None, None)`` when none is
    configured, and ``(None, reason)`` when the environment variable holds
    something that is not key material. ``load_privkey`` decodes that variable
    unguarded (``engine/data_bridge_keys.py::_try_load``); a pasted session key
    is already validated on Save and a bad key FILE is only warned about, so
    the env var is the one source that raises. The reason never echoes the
    variable's value.
    """
    try:
        return _resolve_privkey_bytes(), None
    except ValueError:
        return None, (
            f"The private key in the `{PRIVKEY_ENV}` environment variable is not a "
            "valid 32-byte key (base64 or hex), so this encrypted file cannot be "
            "opened. Fix or unset that variable, or paste your key in "
            "**\U0001f511 V2 private key** above."
        )


def _pubkey_status() -> tuple[bytes | None, str | None]:
    """Resolve the export key without letting a bad key escape as a traceback.

    The export-side twin of :func:`_privkey_status`: ``(key, None)`` when a key
    resolves, ``(None, None)`` when none is configured, and ``(None, reason)``
    when ``ROTH_PLANNER_DATA_BRIDGE_PUBKEY`` holds something that is not key
    material -- ``load_pubkey`` decodes that variable unguarded. A pasted
    session key wins in :func:`_resolved_pubkey` and never reaches the
    variable. The reason never echoes the variable's value.
    """
    try:
        return _resolved_pubkey(), None
    except ValueError:
        return None, (
            f"The public key in the `{PUBKEY_ENV}` environment variable is not a "
            "valid 32-byte key (base64 or hex), so this data cannot be sealed for "
            "export. Fix or unset that variable, or paste your private key in "
            "**\U0001f511 V2 private key** above."
        )


def _disk_pubkey_or_none() -> bytes | None:
    """``load_pubkey()`` for the key-mismatch warning only; a malformed env var is None.

    The warning compares a pasted session key against the on-disk one. A bad
    env var must not crash that comparison when the session key is what the
    export actually seals with.
    """
    try:
        return load_pubkey()
    except ValueError:
        return None


def _parse_recipient_pubkey(raw: str | None) -> tuple[bytes | None, str | None]:
    """Parse an optional third-party recipient public key for export sealing.

    Returns:
        ``(pubkey, None)`` for a valid 32-byte key (base64 or hex);
        ``(None, None)`` for blank/whitespace input (seal for yourself instead);
        ``(None, error_message)`` for malformed input.
    """
    if not raw or not raw.strip():
        return None, None
    try:
        return decode_keymaterial(raw), None
    except ValueError as e:
        return None, f"Invalid recipient public key: {e}"


def _handle_keypair_generation() -> None:
    """Generate a data-bridge keypair in-session (no CLI needed).

    The public Pyodide site has no shell, so ``pixi run
    gen-data-bridge-keypair`` is unavailable to a recipient. This widget
    generates an X25519 keypair in the browser: the public key is shown for
    sharing with whoever will encrypt data for you, and the private key is
    shown once for you to save (and optionally loaded into this session so
    you can immediately decrypt an upload). Neither key is written to disk or
    ``localStorage`` — copy them before clearing or reloading.
    """
    with st.expander("\U0001f195 Generate a data-bridge keypair"):
        st.caption(
            "No install needed. Generate a keypair here, send the **public** key "
            "to whoever is encrypting data for you, and keep the **private** key "
            "secret. Neither key is saved — copy them somewhere safe now."
        )
        if st.button("Generate keypair", key="gen_keypair"):
            # Deferred: nacl import kept function-local (module convention)
            from engine.data_bridge_crypto import generate_keypair

            pub, priv = generate_keypair()
            st.session_state["_generated_pub_b64"] = base64.b64encode(pub).decode("ascii")
            st.session_state["_generated_priv_b64"] = base64.b64encode(priv).decode("ascii")

        pub_b64 = st.session_state.get("_generated_pub_b64")
        priv_b64 = st.session_state.get("_generated_priv_b64")
        if not (pub_b64 and priv_b64):
            return

        st.markdown("**Public key** — share this with the sender:")
        st.code(pub_b64, language=None)
        st.markdown("**Private key** — keep this secret:")
        st.code(priv_b64, language=None)
        st.warning(
            "⚠️ Not saved to disk or localStorage. Copy the private key "
            "somewhere safe now — it is gone as soon as you click Clear, "
            "navigate to another page, or reload."
        )
        st.download_button(
            label="⬇️ data-bridge.priv",
            data=priv_b64 + "\n",
            file_name="data-bridge.priv",
            mime="text/plain",
            key="download_gen_priv",
        )
        col_a, col_b = st.columns(2)
        if col_a.button("Use this key for decryption now", key="use_gen_key"):
            st.session_state["data_bridge_privkey_b64"] = priv_b64
            st.session_state.pop("_generated_pub_b64", None)
            st.session_state.pop("_generated_priv_b64", None)
            st.success("Private key loaded for this session.")
            st.rerun()
        if col_b.button("Clear", key="clear_gen_key"):
            st.session_state.pop("_generated_pub_b64", None)
            st.session_state.pop("_generated_priv_b64", None)
            st.rerun()


def _handle_v2_privkey() -> None:
    """Widget for entering the V2 data-bridge private key.

    The key lives only in ``st.session_state`` under
    ``data_bridge_privkey_b64`` for the duration of the browser session; it
    is never persisted to ``localStorage`` or disk, so it must be re-pasted
    after a page reload.
    """

    stored_key = st.session_state.get("data_bridge_privkey_b64")
    has_key = stored_key is not None
    # Auto-expand on the public site when no key is set — user needs to act.
    expand = is_pyodide() and not has_key

    with st.expander("\U0001f511 V2 private key", expanded=expand):
        if has_key:
            # Visible confirmation that the key is actually SAVED, not merely
            # typed — see the unsaved-warning branch below for why this
            # distinction matters. Never show the key itself.
            st.caption("\U0001f510 Private key saved for this session.")
            if st.button("Clear", key="clear_v2_privkey"):
                st.session_state.pop("data_bridge_privkey_b64", None)
                st.rerun()
            return
        st.caption(
            "Paste your data-bridge private key (base64). Required to decrypt "
            "uploaded `.json.enc` files and to encrypt exports on the public site."
        )
        key_input = st.text_input(
            "Private key (base64)",
            type="password",
            key="_v2_privkey_input",
            help="From `~/.finextract/data-bridge.priv` on your local host.",
        )
        # Saved-vs-unsaved must be visibly distinct: a password field holding
        # typed-but-unsaved text is otherwise indistinguishable from a saved
        # one (dots either way), so a user who pastes a key and never clicks
        # Save can believe it is already in effect — and is then told by the
        # export section, below, that no key is available. Compare WITHOUT
        # ever displaying the value, its length, or logging it — only
        # whether the stripped input differs from what is actually stored.
        if key_input.strip() and key_input.strip() != (stored_key or ""):
            st.caption("⚠️ Key entered but not saved yet — click Save to use it.")
        if st.button("Save", key="save_v2_privkey") and key_input:
            try:
                decode_keymaterial(key_input)
            except ValueError as e:
                st.error(f"Invalid key: {e}")
                return
            val = key_input.strip()
            # Security note (audit M13 — accepted limitation, won't-fix): the V2 private
            # key cannot be meaningfully zeroized in this Streamlit/Pyodide context. It is
            # held as an immutable Python str here in session_state and in the text_input
            # widget state for the session lifetime, and decoding yields immutable bytes —
            # none can be wiped in place (CPython may also copy/intern them). A partial
            # bytearray-wipe of the decoded 32-byte copy would protect it for only
            # microseconds while these str copies remain, and public-site (Pyodide) users
            # face XSS rather than process-memory risk. The meaningful mitigation in place
            # is that the key is intentionally NOT persisted to localStorage (see
            # engine/data_bridge_browser.py).
            st.session_state["data_bridge_privkey_b64"] = val
            st.success("Private key saved.")
            st.rerun()


def _this_instance_owner() -> str | None:
    """This instance's owner, or None when unset/corrupt.

    A corrupt file degrades to None (same "unset" treatment as a first-run
    install) rather than crashing the tab -- Command Center's identity gate
    is where the user fixes it (see app.py's _seed_session_state, Task 4).
    """
    try:
        return st.session_state.get("instance_owner") or load_instance_owner()
    except CorruptInstanceOwnerError:
        return None


# The import preview's per-file slot choices (views/setup/_partials/_import_preview.py).
_IMPORT_SLOT_WIDGET_KEYS: tuple[str, ...] = (
    "import_slot_you",
    "import_slot_spouse",
    "import_v4_slot",
)


def _stash_upload(uploaded: object) -> bytes | None:
    """Capture the uploaded bundle's bytes once, at upload time, and return them.

    Apply decrypts from this copy rather than re-reading the ``UploadedFile``
    handle. A handle that was already read by a failed Apply can sit at EOF, so
    a retry after the user saves the right key would decrypt empty bytes and
    report "payload is empty" when the real cause was the key (spec §5.1).

    Keyed by the upload's identity so a NEW file replaces the stash, while
    reruns of the same upload reuse it. A cleared uploader clears the stash.

    A new file also drops the preview's slot widgets (audit-1008 F-33). They
    are keyed, so Streamlit keeps the previous file's ticks and ignores the new
    file's ``value=`` default: dropping a spouse's export over one's own backup
    would otherwise import the stale "you" copy and skip the spouse's data.
    """
    if uploaded is None:
        st.session_state.pop("_bundle_upload_bytes", None)
        st.session_state.pop("_bundle_upload_id", None)
        st.session_state.pop("_bundle_prepared", None)
        return None
    upload_id = (
        f"{getattr(uploaded, 'file_id', '')}|{getattr(uploaded, 'name', '')}"
        f"|{getattr(uploaded, 'size', '')}"
    )
    if (
        st.session_state.get("_bundle_upload_id") != upload_id
        or "_bundle_upload_bytes" not in st.session_state
    ):
        if st.session_state.get("_bundle_upload_id") != upload_id:
            for key in _IMPORT_SLOT_WIDGET_KEYS:
                st.session_state.pop(key, None)
        st.session_state["_bundle_upload_bytes"] = uploaded.getvalue()  # type: ignore[attr-defined]
        st.session_state["_bundle_upload_id"] = upload_id
    return st.session_state["_bundle_upload_bytes"]


def _wrong_key_message(name: str) -> str:
    """A wrong key is not a malformed file, so it gets its own message."""
    return (
        f"Could not decrypt {name}: it was not encrypted for the private key "
        "configured here, or the file is damaged. Check you saved the right key, "
        "or ask the sender to re-export it for your public key."
    )


def _decode_upload(upload_bytes: bytes, privkey: bytes | None, name: str) -> dict:
    """Decrypt and JSON-decode an upload. Returns ``{"data", "error"}``, never raises.

    Every failure here is about the key or the file -- nothing of ours has run
    yet -- so each becomes a visible message rather than a traceback.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import (
        DataBridgeCryptoError,
        DecryptionFailedError,
        open_uploaded_payload,
    )

    try:
        plaintext = open_uploaded_payload(upload_bytes, privkey)
    except DecryptionFailedError:
        return {"data": None, "error": _wrong_key_message(name)}
    except (DataBridgeCryptoError, ValueError, TypeError) as e:
        return {"data": None, "error": f"Invalid {name}: {e}"}
    try:
        data = json.loads(plaintext.decode("utf-8"))
    except ValueError as e:  # JSONDecodeError and UnicodeDecodeError both subclass it
        return {"data": None, "error": f"Invalid {name}: {e}"}
    return {"data": data, "error": None}


def _prepared_upload(upload_bytes: bytes, privkey: bytes | None, name: str) -> dict:
    """Decode the stashed upload ONCE per (upload, key) and reuse it on every rerun.

    The preview needs the decoded file on every script run, and Apply needs it
    again; decrypting each time would be wasted work and would make one Apply
    attempt look like several. The cache is keyed by the upload's identity and
    a digest of the key -- never the key itself -- so saving a different key,
    or uploading a different file, decodes afresh.
    """
    key_id = hashlib.sha256(privkey).hexdigest() if privkey else "-"
    cache_id = f"{st.session_state.get('_bundle_upload_id')}|{key_id}"
    cached = st.session_state.get("_bundle_prepared")
    if isinstance(cached, dict) and cached.get("id") == cache_id:
        return cached
    prepared = {**_decode_upload(upload_bytes, privkey, name), "id": cache_id}
    st.session_state["_bundle_prepared"] = prepared
    return prepared


def _parse_payload(data: dict) -> tuple[str, BundleV5 | dict]:
    """Classify and structurally validate a decoded payload, before anything is applied.

    Raises for a malformed file, while "Invalid <file>" is still the honest
    message. Everything after this point is our own logic, and a failure there
    must surface as a defect, not be dressed up as a bad upload (audit-0823).
    """
    if classify_bundle(data) == "v5":
        return "v5", parse_v5(data)
    # The v4 structural contract: touch every section the apply phase reads.
    _portfolio_snapshot_from_dict({"accounts": data["sections"]["portfolio"]["accounts"]})
    _ = data["sections"]["setup_scalars"]
    return "v4", data


_SESSION_SCALAR_KEYS: frozenset[str] = frozenset(
    {spec.session_key for spec in SCALAR_REGISTRY}
    | {"_user_grant_strikes", "survivor", "_survivor_enabled", "inherited_iras"}
)


def _slot_captured_at() -> dict[str, str | None]:
    """When each slot's data was last captured here (plan R12); empty when unknown.

    Session first -- the public site has no persistent file -- then the value
    persisted in ``.user_defaults.json`` by the last import.
    """
    held = st.session_state.get("_slot_captured_at")
    if not isinstance(held, dict):
        from config import loader

        held = loader.load_raw_user_defaults().get("slot_captured_at")
    if not isinstance(held, dict):
        return {}
    return {k: v for k, v in held.items() if k in SLOT_ORDER and (v is None or isinstance(v, str))}


def _persist_slot_captured_at(new: dict, old: dict) -> None:
    if dict(new) == dict(old):
        return
    st.session_state["_slot_captured_at"] = dict(new)
    save_user_defaults({"slot_captured_at": dict(new)})


def _held_portfolio_state() -> tuple[PortfolioSnapshot | None, dict | None, YTDSnapshot | None]:
    """The portfolio snapshot, PDF ledger and YTD snapshot this planner holds.

    In demo mode (audit-1018 F-31) the on-disk caches are the parked PERSONAL
    data, so an export or import diff sees only what the demo session itself
    holds -- never the personal portfolio, ledger or YTD bundled in beside the
    demo scalars.

    Otherwise the in-session YTD snapshot is preferred (authoritative, and the
    ONLY thing that exists at all in Pyodide -- there is no persistent
    filesystem there, so ``load_ytd_snapshot()`` always misses on the public
    site), falling back to the on-disk cache for a local run where the YTD page
    hasn't been visited yet this session.
    """
    if is_demo_mode():
        return (
            st.session_state.get("portfolio_snapshot"),
            None,
            st.session_state.get("ytd_snapshot"),
        )
    return (
        load_snapshot(),
        _load_pdf_ledger(),
        st.session_state.get("ytd_snapshot") or load_ytd_snapshot(),
    )


def _local_state() -> LocalState:
    """Everything this planner holds before an import, for the planner to diff against."""
    from config import loader

    overrides = st.session_state.get("account_type_overrides")
    snapshot, ledger, ytd = _held_portfolio_state()
    return LocalState(
        scalars={k: st.session_state[k] for k in _SESSION_SCALAR_KEYS if k in st.session_state},
        persisted_scalars=loader.load_raw_user_defaults(),
        snapshot=snapshot,
        ledger=ledger or {},
        ytd=ytd,
        account_type_overrides=overrides if isinstance(overrides, dict) else {},
        slot_captured_at=_slot_captured_at(),
    )


@dataclass(frozen=True)
class _ReadyImport:
    """A decoded, validated, previewed upload -- what Apply acts on."""

    version: str
    bundle: BundleV5 | dict
    data: dict
    plan: ImportPlan
    selected: frozenset[str]
    local: LocalState
    operator: str


def _preview_upload(upload_bytes: bytes, privkey: bytes | None, name: str) -> _ReadyImport | None:
    """Decode, validate and preview the upload. None when it cannot be applied.

    Renders above Apply in the same script run, so what the user confirms is
    exactly what Apply will do.
    """
    prepared = _prepared_upload(upload_bytes, privkey, name)
    if prepared["error"]:
        st.error(prepared["error"])
        return None
    data = prepared["data"]
    if read_format_version(data) is None:
        st.warning(
            "This looks like an older export. Please re-export from the sender "
            "using the current version and upload the new roth_bridge.enc."
        )
        return None
    try:
        version, bundle = _parse_payload(data)
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        st.error(f"Invalid {name}: {e}")
        return None
    v4_target = render_v4_slot_picker() if version == "v4" else "spouse"
    operator = operator_slot()
    local = _local_state()
    plan = plan_import(bundle, local, v4_target=v4_target, operator=operator)
    selected = render_import_preview(plan)
    return _ReadyImport(version, bundle, data, plan, selected, local, operator)


def _settle_ytd(result: ImportResult, ready: _ReadyImport) -> list[str] | None:
    """Land the import's YTD in the session, then re-derive the ledger-derived fields.

    Returns ``_rederive_ytd_from_ledger``'s result: ``None`` when nothing was
    updated, else the kept-manual warnings for the caller to show after commit.

    v5: the result's YTD already holds each imported person's figures in
    their own slot (#496), so it becomes the session snapshot.

    v4: YTD is NEVER adopted from the sender (PR #497). The bundle's ytd
    section is household-wide from the sender's side, and the ledger
    re-derive below only restores the brokerage and crypto fields -- adopting
    the sender's object silently handed them every OTHER field (measured:
    wages 100,000 -> 77,000, nqo_exercise_ytd 50,000 -> 0). A receiver with no
    snapshot of its own gets a blank one, never the sender's.

    Either way the ledger-derived fields still combine both people.
    """
    if ready.version == "v5":
        if result.ytd is not None:
            st.session_state["ytd_snapshot"] = result.ytd
        ledger = ready.bundle.records.ledger if isinstance(ready.bundle, BundleV5) else {}
        ledger_has_data = any(ledger.get(section) for section in ("koinly", "brokerage"))
        incoming_ytd = result.ytd
    else:
        incoming_ytd = read_bundle_ytd(ready.data)
        ledger_slice = ready.data["sections"].get("ledger") or {}
        ledger_has_data = bool(ledger_slice.get("koinly") or ledger_slice.get("brokerage"))
    if "ytd_snapshot" not in st.session_state and (ledger_has_data or incoming_ytd is not None):
        # Satisfy _rederive_ytd_from_ledger's precondition: in Pyodide there is
        # no saved YTD snapshot to load, so without this seed the re-derive was
        # a permanent no-op in the browser.
        st.session_state["ytd_snapshot"] = load_ytd_snapshot() or YTDSnapshot()
    return _rederive_ytd_from_ledger(result.ledger)


def slots_with_replaced_accounts(plan: ImportPlan, selected: frozenset[str]) -> list[str]:
    """Selected slots whose accounts the import replaced or removed."""
    return [
        slot
        for slot, sp in plan.slots.items()
        if slot in selected and (sp.accounts_incoming or sp.accounts_removed)
    ]


def _stamp_bundle_scalars_in_baseline(scalar_updates: dict) -> None:
    """Label imported balances/SS/price ``Backup import`` in the committed baseline.

    With an unreadable baseline nothing is touched. With no baseline yet (first
    load) the values are staged for the migrating resolve, which labels them
    ``Backup import`` too (audit-1018 F-21, issue #43).
    """
    try:
        committed_json = load_committed()
        if committed_json is None:
            st.session_state[BUNDLE_SCALARS_KEY] = dict(scalar_updates)
            return
        if stamp_bundle_scalars(committed_json, scalar_updates, datetime.now()):
            save_committed(committed_json)
    except CorruptCommittedCacheError:
        return


def _apply_ready_import(ready: _ReadyImport, name: str) -> None:
    """Apply a previewed upload: one store transaction, then stage the session.

    Every store write -- the three caches (``commit_import``), the baseline
    stamp, the MAGI candidates, the superseded-candidate discard and the YTD
    re-derive -- runs in ONE store transaction (spec §2): a failure saves none
    of them and puts back the session keys they touched. Only after it commits
    are the next run's defaults staged, the slot timestamps written
    (``.user_defaults.json`` is a file, outside the store), the kept-manual
    notes shown and manual entry deselected (session bookkeeping a rollback
    could not undo). Any exception other
    than the two refusals below is OUR defect and propagates rather than being
    reported as a bad file (audit-0823); a store error reaches the page banner.
    """
    try:
        result = apply_import(
            ready.bundle,
            ready.plan,
            ready.local,
            selected=ready.selected,
            operator=ready.operator,
            apply_v4_bundle=apply_bundle,
        )
        with store_transaction(st.session_state, BUNDLE_SCALARS_KEY, "ytd_snapshot"):
            commit_import(
                result,
                save_snapshot=save_snapshot,
                save_ledger=_save_pdf_ledger,
                save_ytd=save_ytd_snapshot,
            )
            _stamp_bundle_scalars_in_baseline(result.scalar_updates)
            if result.magi_candidates:
                # Source.BUNDLE candidates for Command Center review, never a
                # full session_state replace (audit defect #2).
                record_magi_candidates(
                    result.magi_candidates, Source.BUNDLE, "Data bridge import", datetime.now()
                )
            # A statement-PDF balance candidate was computed from accounts this
            # import just replaced; leaving it would offer a superseded figure.
            discard_pdf_balance_candidates(slots_with_replaced_accounts(ready.plan, ready.selected))
            kept_warnings = _settle_ytd(result, ready)
    except EmptySnapshotWriteRefusedError:
        st.error(
            "Import refused: this bundle would remove every account from your "
            "saved portfolio, and nothing on disk was changed. Re-export from the "
            "sender with its portfolio section populated, or clear the portfolio "
            "deliberately from Portfolio Sync first."
        )
        return
    except CorruptPortfolioCacheError:
        st.error(
            "Import refused: the saved portfolio could not be read, so it was "
            "left untouched and nothing was imported."
        )
        return

    # Staged, not written: widgets holding these keys already exist in this
    # run, so they are applied by _drain_pending_defaults at the top of the
    # NEXT run (see _apply_user_defaults_to_session).
    pending = st.session_state.setdefault("_pending_defaults", {})
    pending.update(result.scalar_updates)
    if result.joint_conflicts:
        pending["_bundle_joint_conflicts"] = [dict(c) for c in result.joint_conflicts]
    if result.account_type_overrides != dict(ready.local.account_type_overrides):
        pending["account_type_overrides"] = result.account_type_overrides
    _persist_slot_captured_at(result.slot_captured_at, dict(ready.local.slot_captured_at))
    st.session_state["portfolio_snapshot"] = result.snapshot
    st.session_state.pop("_suppress_snapshot_autoload", None)
    manual_entry_kept_explicit = False
    if kept_warnings is not None:
        for warning in kept_warnings:
            st.warning(warning)
        manual_entry_kept_explicit = not auto_deselect_manual_entry(st.session_state)

    # Strike prices arrived but no grants to attach them to: Household.grants
    # is still the synthetic demo default_factory, which silently poisons every
    # option/NQO income figure -- say so loudly.
    v4_strikes = (
        ready.data["sections"]["setup_scalars"].get("grant_strikes")
        if ready.version == "v4"
        else None
    )
    strikes_present = bool(
        st.session_state.get("_user_grant_strikes")
        or result.scalar_updates.get("_user_grant_strikes")
        or v4_strikes
    )
    if strikes_present and not getattr(result.snapshot, "equity_grants", None):
        st.warning(
            "⚠️ Option strike prices were imported, but no stock-option grants came "
            "with them — option/NQO income is being computed from this app's "
            "built-in demo grants, not the real ones. Figures on the Conversion "
            "Plan and YTD pages will be wrong until the sender re-exports with a "
            "current version."
        )
    applied = " + ".join(s for s in SLOT_ORDER if s in ready.selected)
    st.success(f"Applied: {name} ({applied}). Rerunning…")
    if manual_entry_kept_explicit:
        st.caption(
            "Manual entry stayed ON — you turned it on yourself, so this import did "
            "not switch you back to synced-data display."
        )
    st.rerun()


def _render_joint_field_conflicts() -> None:
    """Surface joint household fields where this import disagreed with us.

    ``filing_status``, ``living_expenses``, the CPI/growth rates and the
    ACA/Medicare figures are shared by definition — the two people in a
    household cannot legitimately hold different values. When the receiver has
    already set one deliberately, the import keeps the receiver's value
    (``engine/upload_merge.py``) rather than letting whoever imported last win.
    That is the safe default, but a silent divergence is how both sides end up
    confidently wrong, so say it out loud and let the user reconcile.

    Read here rather than at the import site because the import stages its
    updates into ``_pending_defaults`` and immediately reruns; the values only
    reach session_state when ``_drain_pending_defaults`` runs at the top of the
    NEXT script run. Popped on display so it shows once.
    """
    conflicts = st.session_state.pop("_bundle_joint_conflicts", None)
    if not conflicts:
        return
    lines = "\n".join(
        f"- **{c['field']}** — yours: `{c['mine']}` · theirs: `{c['theirs']}`" for c in conflicts
    )
    st.warning(
        "These household-wide settings differ between the two planners. Your "
        "own values were kept — the import did not change them. They should "
        "match, so update whichever side is out of date:\n\n" + lines
    )


def _handle_personal_uploads() -> None:
    """Widget to import a sealed ``roth_bridge.enc`` bundle, previewed before Apply.

    For use in the deployed (stlite) demo where the visitor cannot put files
    next to the app. Local users can ignore this and just keep the on-disk
    caches in cwd.

    Accepts a V2 sealed ``.enc`` bundle (see ``engine.bridge_bundle``).
    Decryption requires the V2 private key configured in the "\U0001f511 V2
    private key" expander (or available on disk).

    A v5 file declares whose data each slot holds; the preview lists what each
    slot would change and lets the user deselect one. A legacy v4 file cannot
    declare a slot, so the user picks one (Spouse by default). Nothing is
    written until Apply (spec 2026-09-20 §5).
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import has_magic

    with st.expander("\U0001f513 Use my real data (this session)"):
        st.caption(
            "Upload your encrypted bundle for a personalized session. "
            "Values stay in this browser only; refresh = back to demo. "
            "`.enc` files require the private key configured above."
        )
        _render_joint_field_conflicts()
        bundle_file = st.file_uploader(
            "roth_bridge.enc (setup scalars + portfolio + PDF ledger)",
            type=["enc"],
            key="bundle_upload",
        )
        upload_bytes = _stash_upload(bundle_file)
        # Only an encrypted (magic-prefixed) payload needs a key; a plaintext one
        # is returned unchanged by open_uploaded_payload, so it must never be
        # blocked by -- or even resolve -- a key.
        encrypted = upload_bytes is not None and has_magic(upload_bytes)
        privkey, key_problem = _privkey_status() if encrypted else (None, None)
        if key_problem:
            st.error(key_problem)
        missing_key = encrypted and privkey is None and key_problem is None
        if missing_key:
            # Say so BEFORE Apply rather than letting it fail mid-flight.
            st.caption(
                "⚠️ Apply is unavailable: this file is encrypted and no private "
                "key is configured. Paste your key in **\U0001f511 V2 private key** "
                "above (or generate one)."
            )
        # Instance identity does NOT gate import: a v5 file declares its own
        # slots and a v4 file lands where the picker says, so nothing here
        # depends on who operates this copy. The gate made the panel unusable on
        # the public site, where identity does not persist (spec §5).
        ready: _ReadyImport | None = None
        if (
            bundle_file is not None
            and upload_bytes is not None
            and not key_problem
            and not missing_key
        ):
            ready = _preview_upload(upload_bytes, privkey, bundle_file.name)
        if ready is not None and not ready.selected:
            st.caption("Select at least one slot to import.")
        apply_disabled = bool(
            key_problem is not None
            or missing_key
            or (upload_bytes is not None and (ready is None or not ready.selected))
        )
        col_a, col_b = st.columns(2)
        apply_clicked = col_a.button(
            "Apply",
            key="apply_uploads",
            use_container_width=True,
            disabled=apply_disabled,
        )
        # A disabled button cannot be clicked in a browser, but its stale value
        # can still read True, so the handler honours the same condition.
        if apply_clicked and not apply_disabled and ready is not None and bundle_file is not None:
            _apply_ready_import(ready, bundle_file.name)
        if col_b.button("Reset to demo", key="reset_demo", use_container_width=True):
            _clear_personal_session_state()
            st.success("Reset to demo defaults.")
            st.rerun()
        if is_demo_mode():
            st.caption(
                "Demo mode is on: your saved data stays on disk, untouched, and is neither "
                "read nor written until you leave demo mode."
            )
            if st.button("Leave demo mode", key="leave_demo_mode", use_container_width=True):
                exit_demo_mode()
                st.rerun()


def _rederive_ytd_from_ledger(ledger: object) -> list[str] | None:
    """Re-derive brokerage + Koinly YTD fields onto the session snapshot after an import.

    Mirrors the exact field assignments in ``views/ytd_income.py`` (scan-folder
    handler): fresh overwrite (``=``, not ``+=``) so the re-derive is idempotent
    across repeated bundle imports. No-op if no YTD snapshot exists yet this
    session (nothing to overwrite onto).

    Returns ``None`` when nothing was updated (no snapshot), else the
    kept-manual warning strings (possibly empty). The caller shows the
    warnings and deselects manual entry
    (``views._shared.auto_deselect_manual_entry``) only after the import's
    transaction commits: ``ytd_manual_entry`` is widget-backed, so a
    rollback could not put it back.
    """
    from engine.pdf_ledger import (
        apply_koinly_totals,
        derive_brokerage_totals,
        latest_brokerage_period_end,
    )
    from engine.portfolio_sync.ytd import apply_brokerage_totals
    from models.ytd_income import describe_kept_manual

    snap = st.session_state.get("ytd_snapshot")
    if snap is None:
        return None

    brokerage_totals = derive_brokerage_totals(ledger, tax_year=snap.tax_year)  # type: ignore[arg-type]
    apply_brokerage_totals(snap, brokerage_totals)
    kept = snap.settle_manual_marks(
        as_of=latest_brokerage_period_end(ledger, tax_year=snap.tax_year)  # type: ignore[arg-type]
    )
    kept += apply_koinly_totals(snap, ledger)  # type: ignore[arg-type]
    snap.with_snapshot_date()
    st.session_state["ytd_snapshot"] = snap
    save_ytd_snapshot(snap)
    return describe_kept_manual(snap, kept)


def _handle_personal_exports() -> None:
    """Widget to download a single sealed data-bridge bundle (``roth_bridge.enc``).

    Requires a V2 data-bridge public key (see ``deploy/README.md``) — either
    your own (env/dotfile/session-derived) or a pasted third-party recipient
    key. There is no plaintext fallback: the consolidated bundle only ever
    leaves the browser sealed.

    A "Recipient public key" field lets you instead seal the export for a
    third party: paste their data-bridge PUBLIC key and the download is sealed
    so that only *their* private key can open it. This takes priority over your
    own key and needs no key of your own, enabling send-only transmission to
    another planner (e.g. via encrypted email).
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.bridge_bundle import build_bundle_v5
    from engine.data_bridge_crypto import seal

    identity_set = bool(_this_instance_owner())

    with st.expander("📦 Export my data", expanded=False):
        # Optional: seal for a third-party recipient instead of yourself.
        recipient_raw = st.text_input(
            "Recipient public key (base64/hex) — optional",
            key="_export_recipient_pubkey",
            help=(
                "Paste a third party's data-bridge PUBLIC key to seal this export "
                "for them — only their private key can open it. Leave blank to "
                "encrypt for yourself. Public keys are safe to share over any channel."
            ),
        )
        recipient_pubkey, recipient_err = _parse_recipient_pubkey(recipient_raw)
        if recipient_err:
            st.error(recipient_err)

        sealing_for_third_party = recipient_pubkey is not None
        pubkey_problem: str | None = None
        if sealing_for_third_party:
            pubkey = recipient_pubkey
        else:
            pubkey, pubkey_problem = _pubkey_status()
        if pubkey_problem:
            st.error(pubkey_problem)
            st.download_button(
                label="⬇️ Download my encrypted data (.enc)",
                data=b"",
                file_name="roth_bridge.enc",
                mime="application/octet-stream",
                key="export_bundle",
                disabled=True,
            )
            return

        if not sealing_for_third_party:
            priv_b64 = st.session_state.get("data_bridge_privkey_b64")
            disk_pubkey = _disk_pubkey_or_none()
            if priv_b64 and disk_pubkey is not None:
                try:
                    from engine.data_bridge_crypto import derive_pubkey

                    session_pubkey = derive_pubkey(decode_keymaterial(priv_b64))
                except ValueError:
                    session_pubkey = None
                if session_pubkey is not None and session_pubkey != disk_pubkey:
                    st.warning(
                        "⚠️ Both a session-pasted key and a local key file are "
                        "present and differ — this export seals for the "
                        "**session key**; the local key file will NOT open it."
                    )

        if pubkey is not None:
            if sealing_for_third_party:
                st.caption(
                    "🔐 Sealing for the recipient's public key — only their private "
                    "key can open this file."
                )
            else:
                st.caption(
                    f"🔐 V2 encrypted export active — sealed with your {_pubkey_source_label()}."
                )
            if not identity_set:
                st.caption(
                    "Export is unavailable until this planner instance has an "
                    "owner — set it in **Command Center**, above."
                )
            export_owner: str | None = None
            # Naive local ISO, the same shape the import planner's staleness
            # check compares (a naive/aware mix would read as "unknown").
            export_now = datetime.now().isoformat(timespec="seconds")
            if identity_set:
                # Poisons-the-receiver defence: an export built from a
                # spouse-owned instance while identity is unset (or degraded
                # by a corrupt saved instance owner -- _this_instance_owner()
                # collapses both to None) would stamp "you" onto a bundle
                # that belongs to the other person, mislabelling every
                # account the RECEIVING instance imports it into. Skip the
                # build entirely when identity is unset rather than relying
                # solely on the button's disabled state, since
                # st.download_button's ``data=`` argument is evaluated
                # eagerly on every render regardless of disabled/clicked.
                scalars = _user_defaults_from_session()
                # Demo mode exports demo data only (audit-1018 F-31); see
                # _held_portfolio_state for the in-session-vs-disk rule.
                snapshot, ledger, ytd = _held_portfolio_state()
                # Carry the exporter's real stock-option/RSU grants along too --
                # without this, the recipient's Household.grants keeps its
                # synthetic Acme demo-grant default_factory forever (the
                # matching strike prices travel via setup_scalars["grant_strikes"]
                # but sit inert with nothing to attach to), so their option/NQO
                # income is computed off fake grants instead of the real ones.
                grants = getattr(snapshot, "equity_grants", None)
                # audit-1008 F-05: grants imported from the other person go back
                # out under THEIR slot, never as this exporter's own.
                grants_owner = getattr(snapshot, "equity_grants_owner", "") or None
                # The gate above (identity_set) makes this fallback
                # unreachable -- _this_instance_owner() cannot be falsy here.
                # Left in place as defence in depth only.
                export_owner = _this_instance_owner() or "you"
                # v5 (plan B6): the exporter's own slot is always filled; the
                # other person's slot rides along with whatever this planner
                # holds for them, stamped with the capture time persisted at
                # its last import (unknown -> None), so a receiver can tell a
                # stale copy of itself from a fresh one.
                bundle = build_bundle_v5(
                    setup_scalars=scalars,
                    snapshot=snapshot,
                    ledger=ledger,
                    ytd=ytd,
                    grants=grants,
                    grants_owner=grants_owner,
                    exporter=export_owner,
                    slot_captured_at=_slot_captured_at(),
                    now=export_now,
                )
                payload = json.dumps(bundle).encode("utf-8")
                export_data: bytes = seal(payload, pubkey)
            else:
                export_data = b""
            st.download_button(
                label="⬇️ Download my encrypted data (.enc)",
                data=export_data,
                file_name="roth_bridge.enc",
                mime="application/octet-stream",
                key="export_bundle",
                disabled=not identity_set,
                # Deliberately no on_click stamp of the local capture time: a
                # click cannot know whether the browser saved the file (on
                # stlite a failed fetch still runs the callback), and the bytes
                # come from an earlier render. Local capture times move only on
                # import; the FILE carries this render's time for its own slot.
            )
            return

        # No V2 key. No plaintext ever leaves the browser for the consolidated bundle.
        st.caption(
            "\U0001f512 No encrypted export available yet. "
            "Paste your private key in the '\U0001f511 V2 private key' widget above, "
            "or generate a keypair above, to enable encrypted export."
        )


def render_data_bridge_tab(hh: Household) -> None:
    """Extracted from setup.py render() — data_bridge tab body."""
    _handle_keypair_generation()
    _handle_v2_privkey()
    _handle_personal_uploads()
    _handle_personal_exports()
