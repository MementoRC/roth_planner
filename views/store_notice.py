"""Banners for the document store: storage unavailable, and the one-time JSON import."""

from __future__ import annotations

import streamlit as st

from engine.store import StoreUnavailableError, get_store, migration_notice

_IMPORT_NOTICE_SHOWN = "_store_import_notice_shown"


def storage_unavailable_message(exc: StoreUnavailableError) -> str:
    return f"Storage unavailable at {exc.location}: {exc.reason}. Nothing was saved."


def render_store_notice() -> None:
    """Open the store (running the one-time JSON import) and report how it went.

    An unavailable database stops the page: the app never falls back to an
    empty store (spec §2). A failed import is shown on every run, because
    saving stays disabled. A successful import is shown once per session.
    """
    try:
        get_store()
    except StoreUnavailableError as exc:
        st.error(storage_unavailable_message(exc))
        st.stop()
    result = migration_notice()
    if result is None:
        return
    if result.error is not None:
        st.error(
            f"Could not import {result.failed_file} into the database ({result.error}). "
            "Your JSON files are untouched and are being read instead; saving is "
            "disabled until the app is restarted with the file repaired or removed."
        )
        return
    if result.imported and not st.session_state.get(_IMPORT_NOTICE_SHOWN):
        st.session_state[_IMPORT_NOTICE_SHOWN] = True
        st.success(
            f"Imported {len(result.imported)} stores into the database; "
            f"JSON backup at {result.backup_dir}."
        )
        if result.unmoved:
            st.warning(
                "Could not move to the backup folder: "
                + ", ".join(result.unmoved)
                + ". They are no longer read and can be moved by hand."
            )
