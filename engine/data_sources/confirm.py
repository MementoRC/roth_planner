"""Pure confirm-field logic for the Setup / Command Center review gate.

Pure module: stdlib + models/ + engine.data_sources.choices/resolver only.
No streamlit imports.

``confirm_field`` is the sole function invoked by the Command Center's
"Confirm" button. It mutates a committed_json payload (the on-disk shape
produced/consumed by engine.data_sources.committed.extract_committed /
load_committed / save_committed) to freeze one field at a user-chosen value
and source, and records the trust choice so future resolve() calls default
to that source. It mirrors engine.data_sources.resolver.confirm's semantics
but operates directly on the JSON payload instead of a Household instance,
since the Command Center works with the on-disk committed_json directly.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

from engine.data_sources.choices import ChoiceMap
from engine.data_sources.resolver import GRANTS_KEY, HOUSEHOLD_SCALAR_FIELDS
from models.grants import StockGrant
from models.sourced import Provenance, Source

_MAGI_PREFIX = "prior_year_magi."


def confirm_field(
    committed_json: dict,
    choices: ChoiceMap,
    field_key: str,
    value: Any,
    source: Source,
    recorded_at: datetime,
    detail: str = "",
) -> dict:
    """Freeze ``field_key`` at ``value`` from ``source`` in ``committed_json``.

    Mutates and returns ``committed_json`` in place. Also records
    ``choices.set_choice(field_key, source, recorded_at)`` so future
    ``resolver.resolve()`` calls default to this source for this field.

    Handles three field-key shapes: a scalar key in ``HOUSEHOLD_SCALAR_FIELDS``
    (wrapped as a ``SourcedValue``-shaped JSON payload), a per-year MAGI key of
    the form ``prior_year_magi.<year>`` (updates just that year within the
    committed ``SourcedDict`` JSON), or the ``grants`` sentinel (``value`` is a
    list of ``StockGrant``, serialized as a ``SourcedList`` JSON payload, one
    per grant).
    """
    prov = Provenance(source=source, recorded_at=recorded_at, detail=detail)
    choices.set_choice(field_key, source, recorded_at)

    if field_key.startswith(_MAGI_PREFIX):
        year = int(field_key[len(_MAGI_PREFIX) :])
        magi_payload = committed_json.get("prior_year_magi") or {"data": {}, "prov": {}}
        data = dict(magi_payload.get("data", {}))
        prov_map = dict(magi_payload.get("prov", {}))
        data[str(year)] = float(value)
        prov_map[str(year)] = prov.to_json()
        committed_json["prior_year_magi"] = {"data": data, "prov": prov_map}
    elif field_key == GRANTS_KEY:
        grants: list[StockGrant] = list(value)
        committed_json[GRANTS_KEY] = {
            "data": [dataclasses.asdict(g) for g in grants],
            "prov": [prov.to_json() for _ in grants],
        }
    elif field_key in HOUSEHOLD_SCALAR_FIELDS:
        payload = prov.to_json()
        payload["value"] = float(value)
        committed_json[field_key] = payload
    else:
        raise ValueError(f"Unknown or unsupported field: {field_key!r}")

    return committed_json
