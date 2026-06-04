"""Guard tests: views must import bracket ceilings from engine.tax, not hardcode them.

These tests enforce that views/planner.py and views/rmd_squeeze.py source MFJ
bracket ceilings from engine.tax.BRACKETS_MFJ rather than embedding raw integer
literals. If bracket ceilings shift (inflation adjustment, OBBBA expiration, future
tax law), chart annotations will track the canonical values rather than drifting
silently.
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
