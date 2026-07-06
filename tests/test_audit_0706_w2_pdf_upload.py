"""Regression tests for audit-0706 wave-2: tax_return_pdf + upload_merge findings.

import-merge-0 / import-merge-4: merge_pdf_magi docstring accuracy (gap-fill semantics).
import-merge-3: compute_magi IRMAA-scope note in docstring.
ui-setup-router-10: as_spouse cross-map missing your_defer_first_rmd → spouse_defer_first_rmd.
"""

from __future__ import annotations

from engine.tax_return_pdf import Form1040Record

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CAPTURED_AT = "2026-01-01T00:00:00"


def _make_record(tax_year: int, magi: float) -> Form1040Record:
    """Build a minimal Form1040Record for gap-fill tests."""
    return Form1040Record(
        tax_year=tax_year,
        agi=magi,
        tax_exempt_interest=0.0,
        taxable_ss=0.0,
        qualified_dividends=0.0,
        ordinary_dividends=0.0,
        feie=0.0,
        magi=magi,
        filing_status=None,
        captured_at=_CAPTURED_AT,
    )


# ---------------------------------------------------------------------------
# import-merge-0 / import-merge-4: merge_pdf_magi gap-fill semantics
# ---------------------------------------------------------------------------


class TestMergePdfMagiGapFill:
    """import-merge-0/4: FinExtract wins; PDF only fills absent/zero gaps."""

    def test_existing_value_not_overwritten(self):
        """A year already in existing must not be replaced by the PDF record."""
        from engine.tax_return_pdf import merge_pdf_magi

        existing = {2022: 120_000.0}
        result = merge_pdf_magi(existing, {2022: _make_record(2022, 95_000.0)})

        assert result[2022] == 120_000.0, (
            "FinExtract value must not be overwritten by PDF — FinExtract wins"
        )

    def test_absent_year_filled_from_pdf(self):
        """A year absent from existing is filled from the PDF record."""
        from engine.tax_return_pdf import merge_pdf_magi

        existing: dict[int, float] = {}
        result = merge_pdf_magi(existing, {2021: _make_record(2021, 80_000.0)})

        assert result[2021] == 80_000.0

    def test_zero_year_filled_from_pdf(self):
        """A falsy (zero) year in existing is replaced by PDF value."""
        from engine.tax_return_pdf import merge_pdf_magi

        existing = {2020: 0.0}
        result = merge_pdf_magi(existing, {2020: _make_record(2020, 70_000.0)})

        assert result[2020] == 70_000.0

    def test_existing_not_mutated(self):
        """merge_pdf_magi must return a new dict, never mutate the input."""
        from engine.tax_return_pdf import merge_pdf_magi

        existing = {2019: 55_000.0}
        result = merge_pdf_magi(existing, {2019: _make_record(2019, 60_000.0)})

        assert existing[2019] == 55_000.0, "original dict must not be mutated"
        assert result is not existing

    def test_merge_pdf_magi_docstring_corrected(self):
        """Docstring must state FinExtract wins, not that PDF takes precedence."""
        from engine.tax_return_pdf import merge_pdf_magi

        doc = merge_pdf_magi.__doc__ or ""
        assert "FinExtract" in doc and "wins" in doc.lower() or "take precedence" in doc.lower(), (
            "merge_pdf_magi docstring must clarify that FinExtract values take precedence"
        )
