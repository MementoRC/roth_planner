"""Asset Location shows composition and blended growth only (redesign PR 3c, per the mockup)."""

from streamlit.testing.v1 import AppTest


def _render() -> None:
    from models.household import Household
    from views.asset_location import render

    render(
        Household(
            your_age=60,
            spouse_age=54,
            base_year=2026,
            your_ira=1_000_000,
            spouse_ira=1_000_000,
        )
    )


def _headings(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_asset_location_keeps_composition_and_growth_only() -> None:
    at = AppTest.from_function(_render, default_timeout=60)
    at.run()
    assert not at.exception
    text = _headings(at)
    assert "Strategy Comparison" in text
    assert "IRA Composition" in text
    assert "IRA Blended Growth Rate" in text
    for removed in ("IRA Balance Over Time", "Roth Balance Over Time", "RMD Size Comparison"):
        assert removed not in text
