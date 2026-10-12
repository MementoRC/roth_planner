"""Adapter between UI-facing field keys and ``CandidateStore.record_candidate``.

Pure module: stdlib + models/ + engine.data_sources.candidate_store/resolver
only. No streamlit imports.

``record_candidate`` here is intentionally defensive: an invalid field_key
(typo, stale UI key, etc.) must never crash the Setup / Command Center — it
logs a warning and reports failure via the boolean return instead.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.resolver import GRANTS_KEY, SOURCED_SCALAR_FIELDS
from models.sourced import Provenance, Source

logger = logging.getLogger(__name__)

_MAGI_FIELD_KEY_RE = re.compile(r"^prior_year_magi\.\d+$")


def is_valid_field_key(field_key: str) -> bool:
    """True if ``field_key`` is a recognized sourced field for the arbiter.

    Valid keys: any of SOURCED_SCALAR_FIELDS, the GRANTS_KEY sentinel, or a
    per-year MAGI key of the form ``prior_year_magi.<year>``.
    """
    return (
        field_key in SOURCED_SCALAR_FIELDS
        or field_key == GRANTS_KEY
        or bool(_MAGI_FIELD_KEY_RE.match(field_key))
    )


def record_candidate(
    store: CandidateStore,
    field_key: str,
    value: Any,
    source: Source,
    detail: str,
    recorded_at: datetime,
) -> bool:
    """Validate ``field_key`` then record a candidate value; never raises.

    Returns True on success, False (after logging a warning) if ``field_key``
    is not recognized by the arbiter.
    """
    if not is_valid_field_key(field_key):
        logger.warning("record_candidate: unrecognized field_key %r ignored", field_key)
        return False
    store.record_candidate(field_key, value, Provenance(source, recorded_at, detail))
    return True
