"""Regression test for audit-0722b: spouse ACA enrollment dropped on cross-import.

engine/upload_merge.py's spouse_field_map cross-maps per-person keys for a
spouse import (your_age -> spouse_age, your_ira -> spouse_ira, ...) but
omitted "your_aca" -> "spouse_aca", so importing a spouse's exported bundle
(as_spouse=True) silently discarded their ACA marketplace enrollment marker.
"""

from engine.upload_merge import build_user_defaults_session_updates


def test_spouse_aca_cross_mapped_on_spouse_import() -> None:
    result = build_user_defaults_session_updates(
        {"your_aca": True, "your_ira": 1700000.0, "your_age": 55},
        as_spouse=True,
    )

    assert result["spouse_aca"] is True
