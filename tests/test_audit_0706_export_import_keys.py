"""Regression test: export/import roundtrip preserves defer_first_rmd and ACA flags.

Audit 2026-07-06: _user_defaults_from_session (export) and
build_user_defaults_session_updates (import) both omitted four session keys:
  your_aca, spouse_aca, your_defer_first_rmd, spouse_defer_first_rmd.

A user who sets defer_first_rmd=True, exports .user_defaults.json, then
re-imports it had the flag silently reset to False.  The engine then
computes only one RMD in the first RMD year instead of the stacked two,
understating ordinary income for that year by a full RMD.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from engine.upload_merge import build_user_defaults_session_updates

# ---------------------------------------------------------------------------
# Phase 1: import path (engine/upload_merge.py — pure function, no Streamlit)
# ---------------------------------------------------------------------------


class TestBuildUserDefaultsSessionUpdatesRoundtrip:
    """build_user_defaults_session_updates must pass the four flags through."""

    FOUR_FLAGS = {
        "your_aca": True,
        "spouse_aca": False,
        "your_defer_first_rmd": True,
        "spouse_defer_first_rmd": True,
    }

    def test_your_aca_survives_import(self) -> None:
        updates = build_user_defaults_session_updates(self.FOUR_FLAGS, as_spouse=False)
        assert "your_aca" in updates, "your_aca dropped during import"
        assert updates["your_aca"] is True

    def test_spouse_aca_survives_import(self) -> None:
        updates = build_user_defaults_session_updates(self.FOUR_FLAGS, as_spouse=False)
        assert "spouse_aca" in updates, "spouse_aca dropped during import"
        assert updates["spouse_aca"] is False

    def test_your_defer_first_rmd_survives_import(self) -> None:
        updates = build_user_defaults_session_updates(self.FOUR_FLAGS, as_spouse=False)
        assert "your_defer_first_rmd" in updates, "your_defer_first_rmd dropped during import"
        assert updates["your_defer_first_rmd"] is True

    def test_spouse_defer_first_rmd_survives_import(self) -> None:
        updates = build_user_defaults_session_updates(self.FOUR_FLAGS, as_spouse=False)
        assert "spouse_defer_first_rmd" in updates, "spouse_defer_first_rmd dropped during import"
        assert updates["spouse_defer_first_rmd"] is True

    def test_all_four_flags_survive_import(self) -> None:
        """Single combined assertion: all four keys present with correct values."""
        updates = build_user_defaults_session_updates(self.FOUR_FLAGS, as_spouse=False)
        for key, expected in self.FOUR_FLAGS.items():
            assert key in updates, f"{key} dropped during import"
            assert updates[key] == expected, f"{key}: expected {expected!r}, got {updates[key]!r}"

    def test_as_spouse_path_does_not_leak_aca_or_defer_flags(self) -> None:
        """as_spouse=True only cross-maps age/ira/ss/rmd-age fields — not boolean flags."""
        spouse_data = {
            "your_age": 55,
            "your_ira": 1_700_000,
            "your_aca": True,
            "your_defer_first_rmd": True,
        }
        updates = build_user_defaults_session_updates(spouse_data, as_spouse=True)
        # ACA and defer flags from the spouse file must NOT appear on the receiver's side
        assert "your_aca" not in updates
        assert "your_defer_first_rmd" not in updates
        # But the cross-mapped age/ira should be present
        assert "spouse_age" in updates
        assert "spouse_ira" in updates


# ---------------------------------------------------------------------------
# Phase 2: export path (views/setup/_state.py — requires mocked st.session_state)
# ---------------------------------------------------------------------------


class TestUserDefaultsFromSessionRoundtrip:
    """_user_defaults_from_session must include all four flags in its output."""

    def _make_session(self) -> dict:
        return {
            "your_age": 61,
            "spouse_age": 55,
            "your_ira": 1_700_000.0,
            "spouse_ira": 1_700_000.0,
            "your_roth": 0.0,
            "spouse_roth": 0.0,
            "your_ss_fra": 24_000.0,
            "spouse_ss_fra": 18_000.0,
            "your_ss_start_age": 67,
            "spouse_ss_start_age": 67,
            "your_rmd_start_age": 75,
            "spouse_rmd_start_age": 75,
            "your_fra_age": 67,
            "spouse_fra_age": 67,
            "living_expenses": 80_000.0,
            "txn_price": 200.0,
            "aca_benchmark_premium_annual": 15_000.0,
            "aca_enhanced_subsidies_active": False,
            "advance_aptc_annual": 0.0,
            "medicare_part_b_base_monthly": 185.0,
            "cpi_assumption": 0.025,
            "filing_status": "married_filing_jointly",
            # The four keys under test — set to non-default values
            "your_aca": True,
            "spouse_aca": True,
            "your_defer_first_rmd": True,
            "spouse_defer_first_rmd": True,
        }

    def test_your_aca_included_in_export(self) -> None:
        session = self._make_session()
        mock_ss = MagicMock()
        mock_ss.__contains__ = lambda self_, k: k in session
        mock_ss.__getitem__ = lambda self_, k: session[k]
        mock_ss.get = lambda k, default=None: session.get(k, default)

        with patch("views.setup._state.st") as mock_st:
            mock_st.session_state = mock_ss
            from views.setup._state import _user_defaults_from_session
            payload = _user_defaults_from_session()

        assert "your_aca" in payload, "your_aca missing from export payload"
        assert payload["your_aca"] is True

    def test_spouse_aca_included_in_export(self) -> None:
        session = self._make_session()
        mock_ss = MagicMock()
        mock_ss.__contains__ = lambda self_, k: k in session
        mock_ss.__getitem__ = lambda self_, k: session[k]
        mock_ss.get = lambda k, default=None: session.get(k, default)

        with patch("views.setup._state.st") as mock_st:
            mock_st.session_state = mock_ss
            from views.setup._state import _user_defaults_from_session
            payload = _user_defaults_from_session()

        assert "spouse_aca" in payload, "spouse_aca missing from export payload"
        assert payload["spouse_aca"] is True

    def test_your_defer_first_rmd_included_in_export(self) -> None:
        session = self._make_session()
        mock_ss = MagicMock()
        mock_ss.__contains__ = lambda self_, k: k in session
        mock_ss.__getitem__ = lambda self_, k: session[k]
        mock_ss.get = lambda k, default=None: session.get(k, default)

        with patch("views.setup._state.st") as mock_st:
            mock_st.session_state = mock_ss
            from views.setup._state import _user_defaults_from_session
            payload = _user_defaults_from_session()

        assert "your_defer_first_rmd" in payload, "your_defer_first_rmd missing from export payload"
        assert payload["your_defer_first_rmd"] is True

    def test_spouse_defer_first_rmd_included_in_export(self) -> None:
        session = self._make_session()
        mock_ss = MagicMock()
        mock_ss.__contains__ = lambda self_, k: k in session
        mock_ss.__getitem__ = lambda self_, k: session[k]
        mock_ss.get = lambda k, default=None: session.get(k, default)

        with patch("views.setup._state.st") as mock_st:
            mock_st.session_state = mock_ss
            from views.setup._state import _user_defaults_from_session
            payload = _user_defaults_from_session()

        assert "spouse_defer_first_rmd" in payload, (
            "spouse_defer_first_rmd missing from export payload"
        )
        assert payload["spouse_defer_first_rmd"] is True

    def test_full_roundtrip_export_then_import(self) -> None:
        """Set all four flags → export → import → assert all survive."""
        session = self._make_session()
        mock_ss = MagicMock()
        mock_ss.__contains__ = lambda self_, k: k in session
        mock_ss.__getitem__ = lambda self_, k: session[k]
        mock_ss.get = lambda k, default=None: session.get(k, default)

        with patch("views.setup._state.st") as mock_st:
            mock_st.session_state = mock_ss
            from views.setup._state import _user_defaults_from_session
            exported = _user_defaults_from_session()

        # Now import the exported payload
        imported = build_user_defaults_session_updates(exported, as_spouse=False)

        for key in ("your_aca", "spouse_aca", "your_defer_first_rmd", "spouse_defer_first_rmd"):
            assert key in exported, f"{key} missing from export"
            assert key in imported, f"{key} dropped during import (was in export)"
            assert imported[key] == session[key], (
                f"{key}: roundtrip mismatch — session={session[key]!r}, "
                f"exported={exported[key]!r}, imported={imported[key]!r}"
            )


# ---------------------------------------------------------------------------
# Phase 3: W3 workplace-plan flags — same export/import roundtrip contract
# ---------------------------------------------------------------------------


class TestWorkplacePlanFlagsExportImportRoundtrip:
    """W3: your/spouse_has_workplace_plan must survive export -> import, same as
    the four ACA/defer flags above (SCALAR_KEYS-driven persistence)."""

    FLAGS = {"your_has_workplace_plan": False, "spouse_has_workplace_plan": True}

    def test_flags_survive_import(self) -> None:
        updates = build_user_defaults_session_updates(self.FLAGS, as_spouse=False)
        assert updates["your_has_workplace_plan"] is False
        assert updates["spouse_has_workplace_plan"] is True

    def _make_session(self) -> dict:
        return {
            "your_age": 61,
            "spouse_age": 55,
            "your_has_workplace_plan": False,
            "spouse_has_workplace_plan": True,
        }

    def test_flags_included_in_export(self) -> None:
        session = self._make_session()
        mock_ss = MagicMock()
        mock_ss.__contains__ = lambda self_, k: k in session
        mock_ss.__getitem__ = lambda self_, k: session[k]
        mock_ss.get = lambda k, default=None: session.get(k, default)

        with patch("views.setup._state.st") as mock_st:
            mock_st.session_state = mock_ss
            from views.setup._state import _user_defaults_from_session

            payload = _user_defaults_from_session()

        assert payload["your_has_workplace_plan"] is False
        assert payload["spouse_has_workplace_plan"] is True

    def test_full_roundtrip_export_then_import(self) -> None:
        session = self._make_session()
        mock_ss = MagicMock()
        mock_ss.__contains__ = lambda self_, k: k in session
        mock_ss.__getitem__ = lambda self_, k: session[k]
        mock_ss.get = lambda k, default=None: session.get(k, default)

        with patch("views.setup._state.st") as mock_st:
            mock_st.session_state = mock_ss
            from views.setup._state import _user_defaults_from_session

            exported = _user_defaults_from_session()

        imported = build_user_defaults_session_updates(exported, as_spouse=False)

        for key in ("your_has_workplace_plan", "spouse_has_workplace_plan"):
            assert key in exported, f"{key} missing from export"
            assert key in imported, f"{key} dropped during import (was in export)"
            assert imported[key] == session[key]
