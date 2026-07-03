"""Data Bridge tab — V2 keypair UX, personal uploads, personal exports."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import streamlit as st

from engine.data_bridge_browser import (
    is_pyodide,
)
from engine.data_bridge_keys import (
    decode_keymaterial,
    load_privkey,
    load_pubkey,
)
from engine.secure_io import read_pii_bytes
from models.household import Household

from ._state import (
    _apply_portfolio_snapshot,
    _apply_user_defaults_to_session,
    _clear_personal_session_state,
    _portfolio_snapshot_from_dict,
    _user_defaults_from_session,
)


def _resolved_pubkey() -> bytes | None:
    """Resolve V2 public key for encryption.

    Order: env/dotfile (:func:`load_pubkey`), then derive from the
    session-state private key (browser paste flow). Returns ``None`` if
    no key is available from any source.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import derive_pubkey

    pk = load_pubkey()
    if pk is not None:
        return pk
    priv_b64 = st.session_state.get("data_bridge_privkey_b64")
    if not priv_b64:
        return None
    try:
        priv_raw = decode_keymaterial(priv_b64)
    except ValueError:
        return None
    return derive_pubkey(priv_raw)


def _resolve_privkey_bytes() -> bytes | None:
    """Resolve V2 private key for decryption.

    Order: session-state pasted key, then disk dotfile/env via
    :func:`load_privkey`. Returns ``None`` if no key is available.
    """
    priv_b64 = st.session_state.get("data_bridge_privkey_b64")
    if priv_b64:
        try:
            return decode_keymaterial(priv_b64)
        except ValueError:
            pass
    return load_privkey()


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
            "somewhere safe now — after you click Clear or reload the page it is gone."
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

    has_key = "data_bridge_privkey_b64" in st.session_state
    # Auto-expand on the public site when no key is set — user needs to act.
    expand = is_pyodide() and not has_key

    with st.expander("\U0001f511 V2 private key", expanded=expand):
        if has_key:
            st.caption("\U0001f510 Private key loaded for this session.")
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


def _handle_personal_uploads() -> None:
    """Widget to inject personal defaults + portfolio snapshot from JSON uploads.

    For use in the deployed (stlite) demo where the visitor cannot put files
    next to the app. Local users can ignore this and just keep
    .user_defaults.json + .portfolio_cache.json in cwd.

    Accepts both V1 plaintext ``.json`` and V2 sealed ``.json.enc`` files.
    Encrypted uploads require the V2 private key configured in the
    "\U0001f511 V2 private key" expander (or available on disk).

    Each uploader has a per-file "Whose data?" toggle. "Me" applies the
    payload to the receiver's own slots (current behavior). "Spouse" treats
    the payload as the spouse's planner export from their own perspective,
    cross-maps ``your_*`` fields to the receiver's ``spouse_*`` slots, and
    merges portfolio accounts with ``owner="spouse"`` while preserving the
    receiver's own accounts, grants, and TXN holdings.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import (
        DataBridgeCryptoError,
        open_uploaded_payload,
    )

    with st.expander("\U0001f513 Use my real data (this session)"):
        st.caption(
            "Upload your local files for a personalized session. "
            "Values stay in this browser only; refresh = back to demo. "
            "V2 `.json.enc` files require the private key configured above. "
            'Use the "Whose data?" toggle when uploading your spouse\'s planner export.'
        )
        ud_role = st.radio(
            "Whose .user_defaults.json?",
            ["Me", "Spouse"],
            horizontal=True,
            key="ud_role",
        )
        ud_file = st.file_uploader(
            ".user_defaults.json[.enc] (ages, SS, grant strikes)",
            type=["json", "enc"],
            key="ud_upload",
        )
        pc_role = st.radio(
            "Whose .portfolio_cache.json?",
            ["Me", "Spouse"],
            horizontal=True,
            key="pc_role",
        )
        pc_file = st.file_uploader(
            ".portfolio_cache.json[.enc] (FinExtract holdings + grants)",
            type=["json", "enc"],
            key="pc_upload",
        )
        col_a, col_b = st.columns(2)
        if col_a.button("Apply", key="apply_uploads", width="stretch"):
            applied: list[str] = []
            privkey = _resolve_privkey_bytes()
            if ud_file is not None:
                try:
                    raw = ud_file.read()
                    plaintext = open_uploaded_payload(raw, privkey)
                    data = json.loads(plaintext.decode("utf-8"))
                    _apply_user_defaults_to_session(data, as_spouse=(ud_role == "Spouse"))
                    applied.append(f"{ud_file.name} ({ud_role.lower()})")
                except (
                    json.JSONDecodeError,
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                    DataBridgeCryptoError,
                ) as e:
                    st.error(f"Invalid {ud_file.name}: {e}")
            if pc_file is not None:
                try:
                    raw = pc_file.read()
                    plaintext = open_uploaded_payload(raw, privkey)
                    data = json.loads(plaintext.decode("utf-8"))
                    snap = _portfolio_snapshot_from_dict(data)
                    _apply_portfolio_snapshot(snap, as_spouse=(pc_role == "Spouse"))
                    applied.append(f"{pc_file.name} ({pc_role.lower()})")
                except (
                    json.JSONDecodeError,
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                    DataBridgeCryptoError,
                ) as e:
                    st.error(f"Invalid {pc_file.name}: {e}")
            if applied:
                st.success(f"Applied: {', '.join(applied)}. Rerunning…")
                st.rerun()
        if col_b.button("Reset to demo", key="reset_demo", width="stretch"):
            _clear_personal_session_state()
            st.success("Reset to demo defaults.")
            st.rerun()


def _handle_personal_exports() -> None:
    """Widget to download local data files for use on the public site.

    When a V2 data-bridge public key is configured (see ``deploy/README.md``),
    exports are sealed with ``crypto_box_seal`` and emitted as ``.json.enc``.
    Otherwise the V1 plaintext export is shown with a deprecation warning.

    A "Recipient public key" field lets you instead seal the export for a
    third party: paste their data-bridge PUBLIC key and the download is sealed
    so that only *their* private key can open it. This takes priority over your
    own key and needs no key of your own, enabling send-only transmission to
    another planner (e.g. via encrypted email).
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import seal

    with st.expander("📦 Export my data", expanded=False):
        defaults = _user_defaults_from_session()
        cache_path = Path(__file__).resolve().parent.parent.parent / ".portfolio_cache.json"

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
        pubkey = recipient_pubkey if sealing_for_third_party else _resolved_pubkey()

        if pubkey is not None:
            if sealing_for_third_party:
                st.caption(
                    "🔐 Sealing for the recipient's public key — only their private "
                    "key can open these files."
                )
            else:
                st.caption("🔐 V2 encrypted export active — files are sealed for your private key.")
            if defaults:
                payload = json.dumps(defaults, indent=2, default=str).encode("utf-8")
                st.download_button(
                    label="⬇️ .user_defaults.json.enc",
                    data=seal(payload, pubkey),
                    file_name=".user_defaults.json.enc",
                    mime="application/octet-stream",
                    key="export_user_defaults_enc",
                )
            else:
                st.caption("(Enter your numbers first to enable export.)")
            if cache_path.exists():
                try:
                    _cache_bytes = read_pii_bytes(cache_path)
                except OSError:
                    st.caption("(Portfolio cache could not be read safely — possible symlink; skipping.)")
                else:
                    st.download_button(
                        label="⬇️ .portfolio_cache.json.enc",
                        data=seal(_cache_bytes, pubkey),
                        file_name=".portfolio_cache.json.enc",
                        mime="application/octet-stream",
                        key="export_portfolio_cache_enc",
                    )
            else:
                st.caption("(Run Portfolio Sync first to enable cache export.)")
            return

        # No V2 key. Public site → BLOCK V1 entirely (no plaintext leaves browser).
        if is_pyodide():
            st.caption(
                "\U0001f512 No plaintext export available on the public site. "
                "Paste your private key in the '\U0001f511 V2 private key' widget above "
                "to enable encrypted export."
            )
            return

        # V1 plaintext fallback — local host only, deprecated.
        st.caption(
            "Saves to your browser's default downloads folder. Share with the public site for third-party analysis."
        )
        st.warning(
            "⚠️ Plaintext export is deprecated and will be removed in a future release. "
            "Run `pixi run gen-data-bridge-keypair` to enable encrypted export."
        )
        if defaults:
            st.download_button(
                label="⬇️ .user_defaults.json",
                data=json.dumps(defaults, indent=2, default=str),
                file_name=".user_defaults.json",
                mime="application/json",
                key="export_user_defaults",
            )
        else:
            st.caption("(Enter your numbers first to enable export.)")
        if cache_path.exists():
            try:
                _cache_bytes = read_pii_bytes(cache_path)
            except OSError:
                st.caption("(Portfolio cache could not be read safely — possible symlink; skipping.)")
            else:
                st.download_button(
                    label="⬇️ .portfolio_cache.json",
                    data=_cache_bytes,
                    file_name=".portfolio_cache.json",
                    mime="application/json",
                    key="export_portfolio_cache",
                )
        else:
            st.caption("(Run Portfolio Sync first to enable cache export.)")


def render_data_bridge_tab(hh: Household) -> None:
    """Extracted from setup.py render() — data_bridge tab body."""
    _handle_keypair_generation()
    _handle_v2_privkey()
    _handle_personal_uploads()
    _handle_personal_exports()
