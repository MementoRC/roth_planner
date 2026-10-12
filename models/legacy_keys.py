"""Pre-rename names of persisted keys, accepted on read and never written.

Older builds persisted the employer-stock fields under a ticker-specific
prefix. Saved local files (``.user_defaults.json``, the candidate store,
trust choices, the committed baseline, the portfolio cache) and data-bridge
bundles already in the wild still carry those names, so every load boundary
passes its raw mapping through :func:`migrate_legacy_keys` before reading it.
Nothing writes the old names: saves and exports only ever emit the new ones.

The legacy keys are stored as SHA-256 digests of the legacy names (hex, of
the UTF-8 encoded key) so the legacy names themselves don't appear in source.
An incoming key is recognised as legacy when its digest is in
:data:`LEGACY_KEY_DIGESTS`.

Stdlib only, so the pure engine/config modules can import it without
pulling in anything else.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import TypeVar

_V = TypeVar("_V")

# sha256(old persisted key).hexdigest() -> current key
LEGACY_KEY_DIGESTS: dict[str, str] = {
    "bcac0c9b49de1d4682f3ff13dd8a3f73755af742e326af8f9bbe25852e090a8f": "stock_price",
    "557ae52d0bb03b8167d23ac79571143ebf3e13ad9269b6488b217acd5263691c": "stock_price_now",
    "774a39a32dc09fce0219bb31a0e4841781232f69696c70b831431062df1e383e": "stock_price_growth_rate",
    "aac2081c707b79c4cc2ac56b3453092511bcf14d1486bc0d719534d683729a0d": "stock_shares_held",
    "0a4d33d7aaa9d5a700a39a6f4ffc734a54c264a0bd035dfbefb9763081c42b81": "stock_shares_value",
}


def key_digest(key: str) -> str:
    """SHA-256 hex digest of *key* (UTF-8), the form legacy keys are stored in."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def is_legacy_key(key: str) -> bool:
    """True when *key* is a pre-rename name."""
    return key_digest(key) in LEGACY_KEY_DIGESTS


def current_key(key: str) -> str:
    """The current name for *key* (itself when it is not a legacy name)."""
    return LEGACY_KEY_DIGESTS.get(key_digest(key), key)


def migrate_legacy_keys(data: Mapping[str, _V]) -> dict[str, _V]:
    """Return a copy of *data* with every legacy key moved to its current name.

    A legacy key whose current name is absent has its value moved across; when
    both are present the current key wins and the legacy one is dropped. The
    input is never mutated.
    """
    out = dict(data)
    for key in list(out):
        new = LEGACY_KEY_DIGESTS.get(key_digest(key))
        if new is not None:
            value = out.pop(key)
            out.setdefault(new, value)
    return out
