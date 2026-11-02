"""Audit-1101 follow-ups #99, #100, #101 (UI display).

* #99 — the completeness badge's "Mark X as confirmed" showed no note; it now
  carries a one-shot "Confirmed X." over its st.rerun(), as Review Changes does.
* #100 — no view passes a fixed ``suffix="K"`` to ``fmt_dollars_short``.
* #101 — the Comparator survivor caption names the owner capitalised ("You",
  "Spouse"), never a bold lowercase "you"/"spouse".

All data is synthetic.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from engine.data_sources.committed import migrate_committed, save_committed
from engine.data_sources.seed_defaults import seed_default_fields
from models.household import Household
from views._format import fmt_dollars_short

VIEWS_DIR = Path(__file__).resolve().parent.parent / "views"

# --- #99: "Mark X as confirmed" note ------------------------------------------


def _badge_script() -> None:
    import views._shared as shared
    from engine.data_sources.committed import apply_committed, load_committed
    from models.household import Household

    hh = Household()
    committed = load_committed()
    assert committed is not None
    apply_committed(hh, committed)
    shared.render_completeness_badge(hh)


def _click_confirm(store):  # noqa: ANN001, ANN202 -- store fixture activates the test store
    from streamlit.testing.v1 import AppTest

    hh = Household()
    save_committed(
        migrate_committed(hh, datetime(2026, 1, 1), default_fields=seed_default_fields(hh))
    )
    at = AppTest.from_function(_badge_script, default_timeout=60).run()
    (button,) = [b for b in at.button if b.key == "confirm_asis_your_ira"]
    button.click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_99_mark_as_confirmed_shows_a_note(store) -> None:  # noqa: ANN001
    at = _click_confirm(store)
    assert "Confirmed Your IRA balance." in [s.value for s in at.success]


def test_99_note_shows_exactly_once(store) -> None:  # noqa: ANN001
    at = _click_confirm(store)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "Confirmed Your IRA balance." not in [s.value for s in at.success]


# --- #101: survivor caption capitalises the owner -----------------------------


def test_101_default_sweep_caption_capitalises_owner() -> None:
    from views.comparator import survivor_caption

    text = survivor_caption("you", [70, 80])
    assert "**you**" not in text
    assert text.startswith("Default sweep: You die at ages [70, 80]")


def test_101_modeled_caption_capitalises_owner() -> None:
    from views.comparator import survivor_caption

    assert survivor_caption("spouse", [70], death_year=2040, death_age=72) == (
        "Modeling: **Spouse** dies in **2040** (age 72)."
    )
    assert survivor_caption("you", [70], death_year=2040, death_age=72) == (
        "Modeling: **You** die in **2040** (age 72)."
    )


# --- #100: fixed K suffix ----------------------------------------------------

# A fixed K suffix renders $1.5M as "$1,500.0K". Sites listed here keep a
# fixed unit on purpose (the value is bounded well below $1M).
_FIXED_K_ALLOWED: set[str] = set()


class TestShortDollarAutoScale:
    def test_auto_renders_millions_as_m(self):
        assert fmt_dollars_short(1_500_000, suffix="auto") == "$1.5M"

    def test_no_view_passes_a_fixed_k_suffix(self):
        pattern = re.compile(r"""suffix\s*=\s*["']K["']""")
        hits = []
        for path in sorted(VIEWS_DIR.rglob("*.py")):
            if path.name == "_format.py":
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                site = f"{path.relative_to(VIEWS_DIR.parent)}:{lineno}"
                if pattern.search(line) and site not in _FIXED_K_ALLOWED:
                    hits.append(f"{site}: {line.strip()}")
        assert hits == [], "fixed suffix='K' call sites:\n" + "\n".join(hits)
