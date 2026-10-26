"""One store transaction for a view flow that writes several stores (spec §2).

``store_transaction(st.session_state, *keys)`` runs the block in one
``Store.transaction()``: every store write in it commits together or not at
all. If the block raises, the session keys named in *keys* are also put back
as they were (removed if they were absent), so the page never shows values the
store rolled back. Values are deep-copied first because the flows mutate their
snapshots in place.

Nested blocks join the outer transaction (there are no savepoints). If an inner
block raises and the caller catches the error inside the outer block, the inner
keys are restored but the store writes are kept. Only the outermost block's
keys are restored when the outer block fails, so list every session key the
whole flow touches in the outermost call.

Rules for callers:

- Never call ``st.rerun()`` or ``st.stop()`` inside the block. Both raise, and
  the raise rolls the transaction back. Rerun after the block.
- Never list a widget-backed key: Streamlit refuses to set one once its widget
  exists in the run.
- Do no network I/O or PDF parsing inside the block: the transaction holds the
  database write lock, and other sessions wait for it.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any

from engine.store import get_store

_ABSENT = object()


@contextmanager
def store_transaction(session_state: MutableMapping[str, Any], *keys: str) -> Iterator[None]:
    """Run the block in one store transaction; on failure also restore *keys*."""
    saved: dict[str, Any] = {}
    for key in keys:
        value = session_state.get(key, _ABSENT)
        saved[key] = value if value is _ABSENT else copy.deepcopy(value)
    try:
        with get_store().transaction():
            yield
    except BaseException:
        for key, value in saved.items():
            if value is _ABSENT:
                session_state.pop(key, None)
            else:
                session_state[key] = value
        raise
