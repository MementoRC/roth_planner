"""Guard tests: views must import bracket ceilings from engine.tax, not hardcode them.

These tests enforce that views/planner.py and views/rmd_squeeze.py source MFJ
bracket ceilings from engine.tax.BRACKETS_MFJ rather than embedding raw integer
literals. If bracket ceilings shift (inflation adjustment, OBBBA expiration, future
tax law), chart annotations will track the canonical values rather than drifting
silently.

G2 extension: views must also source NIIT_THRESHOLD_MFJ and NIIT_RATE from
engine.niit rather than embedding the literals "$250K" or "3.8%" in label/help
strings.
"""

import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _file_text(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Part A — Import-equality: view modules must import BRACKETS_MFJ at module scope
# ---------------------------------------------------------------------------


class TestImportEquality:
    @pytest.fixture(autouse=True)
    def _require_streamlit(self):
        pytest.importorskip("plotly")
        pytest.importorskip("streamlit")

    def test_views_planner_imports_brackets_mfj(self):
        """views/planner.py must import BRACKETS_MFJ from engine.tax."""
        import views.planner as planner

        assert "BRACKETS_MFJ" in dir(planner), (
            "views/planner.py must import BRACKETS_MFJ from engine.tax"
        )

    def test_views_rmd_squeeze_imports_brackets_mfj(self):
        """views/rmd_squeeze.py must import BRACKETS_MFJ from engine.tax."""
        import views.rmd_squeeze as rmd_squeeze

        assert "BRACKETS_MFJ" in dir(rmd_squeeze), (
            "views/rmd_squeeze.py must import BRACKETS_MFJ from engine.tax"
        )

    def test_views_ytd_income_uses_canonical_niit_threshold(self):
        """views/ytd_income.py must import NIIT_THRESHOLD_MFJ for the help-text mention."""
        import views.ytd_income as ytd_income

        assert "NIIT_THRESHOLD_MFJ" in dir(ytd_income), (
            "views/ytd_income.py must import NIIT_THRESHOLD_MFJ from engine.niit"
        )

    def test_views_sweet_spot_uses_canonical_niit_threshold(self):
        """views/sweet_spot.py must import NIIT_THRESHOLD_MFJ for the annotation."""
        import views.sweet_spot as sweet_spot

        assert "NIIT_THRESHOLD_MFJ" in dir(sweet_spot), (
            "views/sweet_spot.py must import NIIT_THRESHOLD_MFJ from engine.niit"
        )

    def test_views_aca_irmaa_uses_canonical_niit_threshold_and_rate(self):
        """views/aca_irmaa.py must import both NIIT_THRESHOLD_MFJ and NIIT_RATE."""
        import views.aca_irmaa as aca_irmaa

        assert "NIIT_THRESHOLD_MFJ" in dir(aca_irmaa), (
            "views/aca_irmaa.py must import NIIT_THRESHOLD_MFJ from engine.niit"
        )
        assert "NIIT_RATE" in dir(aca_irmaa), (
            "views/aca_irmaa.py must import NIIT_RATE from engine.niit"
        )


# ---------------------------------------------------------------------------
# Part B — AST/source guard: literal bracket values must not appear in source
# ---------------------------------------------------------------------------


class TestNoLiteralBracketCeilings:
    def test_no_literal_bracket_ceilings_in_planner(self):
        """views/planner.py must not contain the literal 100_800."""
        text = _file_text("views/planner.py")
        assert "100_800" not in text, (
            "views/planner.py contains the literal 100_800 — use BRACKETS_MFJ[1][0] instead"
        )

    def test_no_literal_bracket_ceilings_in_rmd_squeeze(self):
        """views/rmd_squeeze.py must not contain literals 100_800 or 211_400."""
        text = _file_text("views/rmd_squeeze.py")
        assert "100_800" not in text, (
            "views/rmd_squeeze.py contains the literal 100_800 — use BRACKETS_MFJ[1][0] instead"
        )
        assert "211_400" not in text, (
            "views/rmd_squeeze.py contains the literal 211_400 — use BRACKETS_MFJ[2][0] instead"
        )

    def test_no_literal_niit_threshold_in_ytd_income(self):
        """views/ytd_income.py must not contain the hardcoded $250K NIIT threshold."""
        text = _file_text("views/ytd_income.py")
        assert "$250K" not in text, (
            "views/ytd_income.py — use NIIT_THRESHOLD_MFJ interpolation instead of hardcoded $250K"
        )

    def test_no_literal_niit_threshold_in_sweet_spot(self):
        """views/sweet_spot.py must not contain the hardcoded NIIT $250K annotation."""
        text = _file_text("views/sweet_spot.py")
        assert "NIIT $250K" not in text, (
            "views/sweet_spot.py — use NIIT_THRESHOLD_MFJ interpolation"
        )

    def test_no_literal_niit_threshold_or_rate_in_aca_irmaa(self):
        """views/aca_irmaa.py must not contain hardcoded NIIT threshold or rate strings."""
        text = _file_text("views/aca_irmaa.py")
        assert "$250K" not in text, (
            "views/aca_irmaa.py — use NIIT_THRESHOLD_MFJ interpolation instead of $250K"
        )
        assert "NIIT (3.8%)" not in text, (
            "views/aca_irmaa.py — use NIIT_RATE interpolation for the NIIT label"
        )
        assert "**3.8% surtax**" not in text, (
            "views/aca_irmaa.py — use NIIT_RATE interpolation in the explanatory text"
        )
