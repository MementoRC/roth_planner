"""Audit 2026-10-08 W0: RMD Squeeze explanation text (F-81, F-82, hard-coded range).

- The "Growth amplifies" bullet is derived from the household's projection,
  not a hard-coded balance range.
- Dollar amounts in the explanation are escaped so Streamlit does not render
  the text between two ``$`` signs as LaTeX (F-82).
- The Tier-1 IRMAA note uses the household's Part B base premium (F-81).
"""

from __future__ import annotations

import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

from engine.irmaa import IRMAA_TIERS_MFJ, irmaa_surcharge
from engine.tax_indexing import index_value
from views._format import fmt_dollars

SRC = Path(__file__).resolve().parent.parent / "views" / "rmd_squeeze.py"


def _explanation(**hh_kwargs) -> str:
    def _script(kw: dict) -> None:
        import views.rmd_squeeze as rmd
        from models.household import Household

        rmd.render(Household(**kw))

    at = AppTest.from_function(_script, args=(hh_kwargs,), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return next(m.value for m in at.markdown if "Growth amplifies" in m.value)


def test_source_has_no_hard_coded_balance_range() -> None:
    src = SRC.read_text(encoding="utf-8")
    assert not re.search(r"\$\d+(\.\d+)?[KM] to \$\d", src)


def test_growth_line_follows_the_household() -> None:
    small = _explanation(your_ira=200_000.0, spouse_ira=100_000.0)
    large = _explanation(your_ira=900_000.0, spouse_ira=600_000.0)
    line = lambda text: next(ln for ln in text.splitlines() if "Growth amplifies" in ln)  # noqa: E731
    assert line(small) != line(large)


def test_explanation_has_no_unescaped_dollars() -> None:
    text = _explanation(your_ira=400_000.0, spouse_ira=300_000.0)
    assert "\\$" in text
    assert not re.search(r"(?<!\\)\$", text), text


def test_tier1_note_uses_household_part_b_base() -> None:
    from models.household import Household

    hh = Household(medicare_part_b_base_monthly=150.0)
    year = hh.base_year + 2
    thresh = index_value(IRMAA_TIERS_MFJ[0][0], year, hh.cpi_assumption)
    expected = irmaa_surcharge(
        thresh + 1,
        2,
        base_part_b=150.0 * 12,
        filing_status="MFJ",
        year=year,
        cpi=hh.cpi_assumption,
    )
    default = irmaa_surcharge(thresh + 1, 2, filing_status="MFJ", year=year, cpi=hh.cpi_assumption)
    assert expected != default
    text = _explanation(medicare_part_b_base_monthly=150.0)
    assert fmt_dollars(expected).replace("$", "\\$") in text
