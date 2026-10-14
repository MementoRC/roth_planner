"""Issue #7: Room This Year's own scenario runs ignored the manual "Additional
NII" estimate that the optimizer behind it prices (#591). summarize_this_year
and rmd_lookahead must run their scenarios with it. Synthetic data only.
"""

from __future__ import annotations

import pytest

import engine.this_year as ty
from engine.exercise_optimizer import OptimizedPlan
from engine.scenario_types import ConversionPlan
from models.exercise_schedule import ExerciseSchedule
from models.household import Household

_NII = 100_000.0


def _hh(your_age: int = 60, spouse_age: int = 54) -> Household:
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        base_year=2026,
        your_ira=1_000_000,
        spouse_ira=1_000_000,
        grants=[],
    )


def _plan(you: float = 0.0) -> OptimizedPlan:
    sched = ExerciseSchedule()
    conv = ConversionPlan(your_conversions={2026: you}, spouse_conversions={2026: 0.0})
    return OptimizedPlan("top-of-22", sched, conv, lifetime_all_in=0.0)


def test_summary_niit_room_reflects_the_estimate() -> None:
    hh = _hh()
    plain = ty.summarize_this_year(hh, None, _plan(), current_plan=ConversionPlan())
    with_nii = ty.summarize_this_year(
        hh, None, _plan(), current_plan=ConversionPlan(), net_inv_income=_NII
    )
    assert plain.room_after.to_niit > _NII  # room is not clamped away in this setup
    assert with_nii.room_after.to_niit == pytest.approx(plain.room_after.to_niit - _NII)


def test_summary_default_matches_explicit_zero() -> None:
    hh = _hh()
    a = ty.summarize_this_year(hh, None, _plan(), current_plan=ConversionPlan())
    b = ty.summarize_this_year(hh, None, _plan(), current_plan=ConversionPlan(), net_inv_income=0.0)
    assert a == b


def _spy_runs(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    seen: list[float] = []
    real_run, real_none = ty.run_scenario, ty.run_no_conversion

    def run(*args, **kwargs):
        seen.append(kwargs.get("net_inv_income", 0.0))
        return real_run(*args, **kwargs)

    def none(*args, **kwargs):
        seen.append(kwargs.get("net_inv_income", 0.0))
        return real_none(*args, **kwargs)

    monkeypatch.setattr(ty, "run_scenario", run)
    monkeypatch.setattr(ty, "run_no_conversion", none)
    return seen


def test_lookahead_runs_every_case_with_the_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    hh = _hh(70, 68)
    current = ConversionPlan(your_conversions={2026: 10_000.0})
    split_plan = _plan(you=60_000.0)
    split = ty.summarize_this_year(hh, None, split_plan, current_plan=current)
    seen = _spy_runs(monkeypatch)
    ty.rmd_lookahead(hh, None, current, split, split_plan, net_inv_income=_NII)
    assert len(seen) == 3  # none, current, split
    assert all(n == pytest.approx(_NII) for n in seen)


def test_summary_runs_with_the_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _spy_runs(monkeypatch)
    ty.summarize_this_year(_hh(), None, _plan(), current_plan=ConversionPlan(), net_inv_income=_NII)
    assert seen
    assert all(n == pytest.approx(_NII) for n in seen)


def test_room_page_passes_the_manual_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    import views.room_this_year as page

    seen: dict[str, float] = {}
    monkeypatch.setattr(page.optimizer_panel, "manual_nii", lambda: _NII)
    monkeypatch.setattr(page, "optimize_exercises", lambda *a, **k: _FakeResult())

    def fake_summary(*args, **kwargs):
        seen["summary"] = kwargs.get("net_inv_income", 0.0)
        return "split"

    def fake_lookahead(*args, **kwargs):
        seen["lookahead"] = kwargs.get("net_inv_income", 0.0)

    monkeypatch.setattr(page, "summarize_this_year", fake_summary)
    monkeypatch.setattr(page, "rmd_lookahead", fake_lookahead)
    monkeypatch.setattr(page, "_actuals", lambda: None)
    monkeypatch.setattr(page, "_current_plan", ConversionPlan)
    monkeypatch.setattr(page.st, "session_state", {})
    page._run(_hh(), ())
    assert seen == {"summary": _NII, "lookahead": _NII}


class _FakeResult:
    best = "best"
