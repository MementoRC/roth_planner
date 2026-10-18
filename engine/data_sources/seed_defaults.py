"""Which governed Household attrs still hold their synthetic seed default.

Pure module (no streamlit). The seed is ``config.defaults.DEFAULTS`` — the
generic synthetic values a fresh install starts from — NOT ``load_defaults()``,
so a user's own ``.user_defaults`` figures are never labelled "seed default".
"""

from __future__ import annotations

from config.defaults import DEFAULTS
from engine.data_sources.committed import COMMITTED_FIELD_ATTRS
from models.household import Household


def seed_default_fields(hh: Household) -> frozenset[str]:
    """Governed attrs whose current value equals the synthetic seed default.

    Attrs the seed does not define (e.g. ``prior_year_magi``, which seeds
    empty) are never reported.
    """
    return frozenset(
        attr
        for attr in COMMITTED_FIELD_ATTRS
        if attr in DEFAULTS and getattr(hh, attr, None) == DEFAULTS[attr]
    )
