"""Regression tests for audit-0706 wave-2: tax_return_pdf + upload_merge findings.

import-merge-3: compute_magi IRMAA-scope note in docstring.
ui-setup-router-10: as_spouse cross-map missing your_defer_first_rmd → spouse_defer_first_rmd.

Wave 5 (Setup / Command Center, 2026-07-16): the former
``TestMergePdfMagiGapFill`` class tested ``engine.tax_return_pdf.merge_pdf_magi``,
which is now removed — its FinExtract-wins/PDF-fills-gaps policy was the
"contradictory MAGI policy" (audit defect #2). PDF-sourced MAGI now records
``prior_year_magi.<year>`` candidates (Source.PDF) via
``engine.data_sources.record.record_magi_candidates``, arbitrated by
``engine.data_sources.resolver`` against the default ladder (which ranks PDF
over FinExtract) — covered by ``tests/test_data_sources.py``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ui-setup-router-10: spouse cross-map must preserve RMD-deferral preference
# ---------------------------------------------------------------------------


class TestSpouseDeferFirstRmdCrossMap:
    """ui-setup-router-10: your_defer_first_rmd must map to spouse_defer_first_rmd."""

    def test_as_spouse_maps_defer_first_rmd(self):
        """as_spouse=True upload must cross-map your_defer_first_rmd to spouse slot."""
        from engine.upload_merge import build_user_defaults_session_updates

        data = {
            "your_age": 55,
            "your_ira": 1_700_000.0,
            "your_defer_first_rmd": True,
        }
        result = build_user_defaults_session_updates(data, as_spouse=True)

        assert "spouse_defer_first_rmd" in result, (
            "spouse_defer_first_rmd must be present when your_defer_first_rmd is in the upload"
        )
        assert result["spouse_defer_first_rmd"] is True

    def test_as_spouse_maps_defer_first_rmd_false(self):
        """False value for your_defer_first_rmd is preserved on cross-map."""
        from engine.upload_merge import build_user_defaults_session_updates

        data = {"your_defer_first_rmd": False}
        result = build_user_defaults_session_updates(data, as_spouse=True)

        assert "spouse_defer_first_rmd" in result
        assert result["spouse_defer_first_rmd"] is False

    def test_as_spouse_does_not_pass_through_raw_key(self):
        """your_defer_first_rmd must not appear verbatim in the output under as_spouse=True."""
        from engine.upload_merge import build_user_defaults_session_updates

        data = {"your_defer_first_rmd": True}
        result = build_user_defaults_session_updates(data, as_spouse=True)

        assert "your_defer_first_rmd" not in result, (
            "Raw your_defer_first_rmd must not leak into session state on as_spouse path"
        )

    def test_non_spouse_path_still_passes_defer_first_rmd_verbatim(self):
        """as_spouse=False must keep your_defer_first_rmd in the scalar pass-through."""
        from engine.upload_merge import build_user_defaults_session_updates

        data = {"your_defer_first_rmd": True, "spouse_defer_first_rmd": False}
        result = build_user_defaults_session_updates(data, as_spouse=False)

        assert result.get("your_defer_first_rmd") is True
        assert result.get("spouse_defer_first_rmd") is False

    def test_as_spouse_missing_defer_first_rmd_key_omitted(self):
        """If upload lacks your_defer_first_rmd the spouse slot is simply absent (no KeyError)."""
        from engine.upload_merge import build_user_defaults_session_updates

        data = {"your_age": 60}
        result = build_user_defaults_session_updates(data, as_spouse=True)

        assert "spouse_defer_first_rmd" not in result


# ---------------------------------------------------------------------------
# import-merge-3: compute_magi IRMAA scope documented in docstring
# ---------------------------------------------------------------------------


class TestComputeMagiDocstring:
    """import-merge-3: compute_magi docstring must mention IRMAA exclusion of FEIE."""

    def test_docstring_mentions_irmaa(self):
        from engine.tax_return_pdf import compute_magi

        doc = compute_magi.__doc__ or ""
        assert "IRMAA" in doc, "compute_magi docstring must reference IRMAA MAGI distinction"

    def test_docstring_mentions_irc_1395r(self):
        from engine.tax_return_pdf import compute_magi

        doc = compute_magi.__doc__ or ""
        assert "1395r" in doc, "compute_magi docstring must cite 42 U.S.C. §1395r for IRMAA"

    def test_compute_magi_still_adds_feie(self):
        """Behavioural contract unchanged — function still includes FEIE."""
        from engine.tax_return_pdf import compute_magi

        assert compute_magi(100_000.0, 500.0, 1_000.0) == 101_500.0
