"""Audit 2026-10-18 W4: auto-fill sizing sees the same YTD / NII as the projection.

F-10: Dashboard, Conversion Plan Auto-Fill and RMD Squeeze size "Fill 12%" with the
      same ``ytd`` they hand to ``run_scenario``.
F-27: the Conversion Plan "No Conversion" trajectory uses the same manual NII
      estimate as "Your Plan".
"""

from __future__ import annotations

import sys

from streamlit.testing.v1 import AppTest

from models.ytd_income import YTDSnapshot

_NII = 40_000
_YTD = YTDSnapshot(tax_year=2026, wages_ytd=60_000.0)


def _script(module: str) -> None:
    import importlib
    import sys

    import engine.scenario as sc
    import engine.scenario_autofill as sa
    from models.household import Household

    if not hasattr(sa, "_w4_calls"):
        sa._w4_calls = {"fill": [], "noconv": []}
        orig_fill = sa.auto_fill_12
        orig_nc = sc.run_no_conversion

        def fill(*a, **k):
            sa._w4_calls["fill"].append((a, k))
            return orig_fill(*a, **k)

        def noconv(*a, **k):
            sa._w4_calls["noconv"].append((a, k))
            return orig_nc(*a, **k)

        sa.auto_fill_12 = fill
        sc.run_no_conversion = noconv
    mod = importlib.import_module(module)
    if hasattr(mod, "auto_fill_12"):
        mod.auto_fill_12 = sa.auto_fill_12
    if hasattr(mod, "run_no_conversion"):
        mod.run_no_conversion = sc.run_no_conversion
    sys.modules[module].render(Household())


def _run(module: str, *, click: str | None = None) -> dict:
    sa = sys.modules.get("engine.scenario_autofill")
    if sa is not None and hasattr(sa, "_w4_calls"):
        sa._w4_calls["fill"].clear()
        sa._w4_calls["noconv"].clear()
    at = AppTest.from_function(_script, args=(module,), default_timeout=120)
    at.session_state["apply_ytd_to_projection"] = True
    at.session_state["ytd_snapshot"] = _YTD
    at.session_state["net_inv_income"] = _NII
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    if click is not None:
        sys.modules["engine.scenario_autofill"]._w4_calls["fill"].clear()
        [b for b in at.button if b.label == click][0].click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
    return sys.modules["engine.scenario_autofill"]._w4_calls


def _ytd_arg(call: tuple) -> object:
    args, kwargs = call
    return kwargs["ytd"] if "ytd" in kwargs else (args[1] if len(args) > 1 else None)


def _nii_arg(call: tuple) -> object:
    args, kwargs = call
    return kwargs["net_inv_income"] if "net_inv_income" in kwargs else args[2]


def test_dashboard_sizes_fill_12_with_the_ytd_it_projects_with() -> None:
    calls = _run("views.dashboard")
    assert calls["fill"], "dashboard did not call auto_fill_12"
    assert all(_ytd_arg(c) == _YTD for c in calls["fill"])


def test_rmd_squeeze_sizes_fill_12_with_the_ytd_it_projects_with() -> None:
    calls = _run("views.rmd_squeeze")
    assert calls["fill"], "rmd_squeeze did not call auto_fill_12"
    assert all(_ytd_arg(c) == _YTD for c in calls["fill"])


def test_asset_location_sizes_fill_12_with_the_ytd_and_nii_dashboard_uses() -> None:
    # issue #41: the Asset Location schedule must match the Dashboard's Fill 12%.
    calls = _run("views.asset_location")
    assert calls["fill"], "asset_location did not call auto_fill_12"
    assert all(_ytd_arg(c) == _YTD for c in calls["fill"])
    assert all(_nii_arg(c) == _NII for c in calls["fill"])


def test_planner_auto_fill_button_sizes_with_the_ytd_it_projects_with() -> None:
    calls = _run("views.planner", click="🎯 Auto-Fill to 12%")
    assert calls["fill"], "Auto-Fill button did not call auto_fill_12"
    assert all(_ytd_arg(c) == _YTD for c in calls["fill"])
    assert all(_nii_arg(c) == _NII for c in calls["fill"])


def test_planner_no_conversion_trajectory_uses_the_manual_nii_estimate() -> None:
    calls = _run("views.planner")
    assert calls["noconv"], "planner did not call run_no_conversion"
    assert all(c[1].get("net_inv_income") == _NII for c in calls["noconv"])
