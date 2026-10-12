"""RMD Squeeze reads QCDs from the Conversion Planner's plan (redesign PR 2)."""

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from views.rmd_squeeze import plan_qcds

PAGE = "rmd"


@pytest.fixture(autouse=True)
def _isolated_app(clean_command_center_caches, monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _run(page: str | None = None, **state) -> AppTest:
    at = AppTest.from_file("app.py", default_timeout=30)
    at.session_state["_suppress_snapshot_autoload"] = True
    if page is not None:
        at.session_state["nav_page"] = page
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


def _has_qcd_chart(at: AppTest) -> bool:
    return any("QCD Impact" in m.value for m in at.markdown)


def test_no_plan_qcd_renders_like_the_old_default():
    # Characterisation: old default (toggle off) == no QCD chart.
    at = _run(PAGE, conv_plan_qcd={}, conv_plan_spouse_qcd={})
    assert not at.exception, [e.value for e in at.exception]
    assert not _has_qcd_chart(at)


def test_plan_qcds_normalises_keys_and_drops_zeros():
    state = {"conv_plan_qcd": {"2045": 20_000, 2046: 0}, "conv_plan_spouse_qcd": {2050: 10_000.0}}
    assert plan_qcds(state) == ({2045: 20_000.0}, {2050: 10_000.0})
    assert plan_qcds({}) == ({}, {})


def test_plan_qcds_is_robust_to_nan_none_and_odd_years():
    state = {
        "conv_plan_qcd": {2045: float("nan"), 2046: None, "abc": 5_000, None: 1, 2047: -3, 2048: 7},
        "conv_plan_spouse_qcd": {2050: float("inf"), 2051: "12"},
    }
    assert plan_qcds(state) == ({2048: 7.0}, {2051: 12.0})


def test_page_has_no_local_qcd_inputs():
    at = _run(PAGE)
    assert [t for t in at.toggle if t.label == "Apply QCD Strategy"] == []
    assert not [n for n in at.number_input if "Annual QCD" in n.label]


def test_plan_qcds_drive_the_qcd_chart():
    # 2045 is past the QCD age for the default household, so the full amount applies.
    at = _run(PAGE, conv_plan_qcd={2045: 20_000})
    assert not at.exception, [e.value for e in at.exception]
    assert _has_qcd_chart(at)
    assert any(m.value == "**\\$20,000 applied**" for m in at.markdown)
    at.button(key="edit_qcd").click().run()
    assert at.session_state["nav_page"] == "conversions"


def test_qcd_before_qcd_age_is_not_shown_as_applied():
    # 2026 (base year) is before the QCD age for the default household: nothing applies.
    at = _run(PAGE, conv_plan_qcd={2026: 20_000})
    assert not at.exception, [e.value for e in at.exception]
    assert not _has_qcd_chart(at)
    assert any(m.value == "**none**" for m in at.markdown)
    assert any("Planned \\$20,000, \\$0 applies" in c.value for c in at.caption)


def test_single_filer_ignores_spouse_plan_qcds():
    at = _run(PAGE, filing_status="Single", conv_plan_spouse_qcd={2045: 10_000})
    assert not at.exception, [e.value for e in at.exception]
    assert not _has_qcd_chart(at)
    assert any(m.value == "**none**" for m in at.markdown)
