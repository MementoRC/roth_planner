"""Data Bridge tab — V2 keypair UX, personal uploads, personal exports."""

from __future__ import annotations

import base64
import json
from datetime import datetime

import streamlit as st

from engine.bridge_bundle import apply_bundle, read_format_version
from engine.data_bridge_browser import (
    is_pyodide,
)
from engine.data_bridge_keys import (
    decode_keymaterial,
    load_privkey,
    load_pubkey,
)
from engine.data_sources.record import record_magi_candidates
from engine.pdf_ledger import load_ledger as _load_pdf_ledger
from engine.pdf_ledger import save_ledger as _save_pdf_ledger
from engine.portfolio_sync import PortfolioSnapshot, save_ytd_snapshot
from engine.portfolio_sync.portfolio import load_snapshot, save_snapshot
from engine.upload_merge import extract_bundle_magi
from models.household import Household
from models.sourced import Source

from ._state import (
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
    """Widget to full-replace one owner's slot from a sealed ``roth_bridge.enc`` bundle.

    For use in the deployed (stlite) demo where the visitor cannot put files
    next to the app. Local users can ignore this and just keep the on-disk
    caches in cwd.

    Accepts a V2 sealed ``.enc`` consolidated bundle (see ``engine.bridge_bundle``).
    Decryption requires the V2 private key configured in the "\U0001f511 V2
    private key" expander (or available on disk).

    The "Whose data?" toggle selects which owner slot ("you" or "spouse")
    the bundle full-replaces: setup scalars are cross-mapped/applied for that
    owner, portfolio accounts for that owner are replaced (other owner's
    accounts and all grants/TXN holdings are preserved), and the PDF ledger
    slice for that owner is replaced.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import (
        DataBridgeCryptoError,
        open_uploaded_payload,
    )

    with st.expander("\U0001f513 Use my real data (this session)"):
        st.caption(
            "Upload your encrypted bundle for a personalized session. "
            "Values stay in this browser only; refresh = back to demo. "
            "`.enc` files require the private key configured above. "
            'Use the "Whose data?" toggle when uploading your spouse\'s planner export.'
        )
        pc_role = st.radio(
            "Whose data?",
            ["Me", "Spouse"],
            horizontal=True,
            key="pc_role",
        )
        bundle_file = st.file_uploader(
            "roth_bridge.enc (setup scalars + portfolio + PDF ledger)",
            type=["enc"],
            key="bundle_upload",
        )
        col_a, col_b = st.columns(2)
        if col_a.button("Apply", key="apply_uploads", use_container_width=True) and bundle_file is not None:
            privkey = _resolve_privkey_bytes()
            try:
                raw = bundle_file.read()
                plaintext = open_uploaded_payload(raw, privkey)
                data = json.loads(plaintext.decode("utf-8"))
                if read_format_version(data) is None:
                    st.warning(
                        "This looks like an older export. Please re-export from the "
                        "sender using the current version and upload the new "
                        "roth_bridge.enc."
                    )
                else:
                    target_owner = "spouse" if pc_role == "Spouse" else "you"
                    incoming_snap = _portfolio_snapshot_from_dict(
                        {"accounts": data["sections"]["portfolio"]["accounts"]}
                    )
                    data["sections"]["portfolio"]["accounts"] = incoming_snap.accounts
                    existing_snapshot = load_snapshot() or PortfolioSnapshot()
                    new_snapshot, new_ledger = apply_bundle(
                        target_owner,
                        data,
                        existing_snapshot=existing_snapshot,
                        existing_ledger=_load_pdf_ledger(),
                    )
                    save_snapshot(new_snapshot)
                    _save_pdf_ledger(new_ledger)
                    _apply_user_defaults_to_session(
                        data["sections"]["setup_scalars"], as_spouse=(target_owner == "spouse")
                    )
                    if target_owner != "spouse":
                        # Bundle MAGI becomes Source.BUNDLE candidates for
                        # Command Center review, never a full session_state
                        # replace (audit defect #2). Mirrors
                        # build_user_defaults_session_updates: the spouse
                        # cross-map never touches prior_year_magi either.
                        bundle_magi = extract_bundle_magi(data["sections"]["setup_scalars"])
                        if bundle_magi:
                            record_magi_candidates(
                                bundle_magi, Source.BUNDLE, "Data bridge import", datetime.now()
                            )
                    st.session_state["portfolio_snapshot"] = new_snapshot
                    st.session_state.pop("_suppress_snapshot_autoload", None)
                    _rederive_ytd_from_ledger(new_ledger)
                    st.success(f"Applied: {bundle_file.name} ({pc_role.lower()}). Rerunning…")
                    st.rerun()
            except (
                json.JSONDecodeError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                DataBridgeCryptoError,
            ) as e:
                st.error(f"Invalid {bundle_file.name}: {e}")
        if col_b.button("Reset to demo", key="reset_demo", use_container_width=True):
            _clear_personal_session_state()
            st.success("Reset to demo defaults.")
            st.rerun()


def _rederive_ytd_from_ledger(ledger: object) -> None:
    """Re-derive brokerage + Koinly YTD fields onto the session snapshot after an import.

    Mirrors the exact field assignments in ``views/ytd_income.py`` (scan-folder
    handler): fresh overwrite (``=``, not ``+=``) so the re-derive is idempotent
    across repeated bundle imports. No-op if no YTD snapshot exists yet this
    session (nothing to overwrite onto).
    """
    from engine.pdf_ledger import derive_brokerage_totals, derive_koinly_totals

    snap = st.session_state.get("ytd_snapshot")
    if snap is None:
        return

    brokerage_totals = derive_brokerage_totals(ledger)  # type: ignore[arg-type]
    snap.interest_ytd = brokerage_totals["interest_ytd"]
    snap.tax_exempt_interest_ytd = brokerage_totals["tax_exempt_interest_ytd"]
    snap.ordinary_dividends_ytd = brokerage_totals["ordinary_dividends_ytd"]
    snap.stcg_ytd = brokerage_totals["stcg_ytd"]
    snap.ltcg_ytd = brokerage_totals["ltcg_ytd"]

    koinly_totals = derive_koinly_totals(ledger)  # type: ignore[arg-type]
    snap.crypto_stcg_ytd = koinly_totals["stcg"]
    snap.crypto_ltcg_ytd = koinly_totals["ltcg"]
    snap.crypto_income_ytd = koinly_totals["income"]

    snap.with_snapshot_date()
    st.session_state["ytd_snapshot"] = snap
    st.session_state["ytd_manual_entry"] = False
    save_ytd_snapshot(snap)


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
    from engine.bridge_bundle import build_bundle
    from engine.data_bridge_crypto import seal

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
        pubkey = recipient_pubkey if sealing_for_third_party else _resolved_pubkey()

        if pubkey is not None:
            if sealing_for_third_party:
                st.caption(
                    "🔐 Sealing for the recipient's public key — only their private "
                    "key can open this file."
                )
            else:
                st.caption("🔐 V2 encrypted export active — file is sealed for your private key.")
            scalars = _user_defaults_from_session()
            snapshot = load_snapshot()
            ledger = _load_pdf_ledger()
            bundle = build_bundle(scalars, snapshot, ledger, owner="you")
            payload = json.dumps(bundle).encode("utf-8")
            st.download_button(
                label="⬇️ Download my encrypted data (.enc)",
                data=seal(payload, pubkey),
                file_name="roth_bridge.enc",
                mime="application/octet-stream",
                key="export_bundle",
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
