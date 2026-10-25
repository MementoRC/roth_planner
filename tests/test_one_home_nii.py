"""Net investment income has one editable home: Profile > Assumptions (redesign PR 2)."""

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from config.scalar_registry import scalar_default, scalar_keys
from views.navigation import resolve_page

NII_LABEL = "Additional net investment income ($/yr)"


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
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


def test_nii_is_a_joint_registry_scalar():
    assert scalar_default("net_inv_income") == 0
    assert "net_inv_income" in scalar_keys()


def _nii_inputs(at):
    return [n for n in at.number_input if n.label == NII_LABEL]


def test_assumptions_has_the_one_nii_input():
    at = _run("assumptions")
    assert not at.exception, [e.value for e in at.exception]
    assert len(_nii_inputs(at)) == 1


READONLY_PAGES = ["conversions", "sweet", "aca"]


def _go(at: AppTest, page: str) -> AppTest:
    at.button(key=f"nav_btn_{resolve_page(page)}").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.mark.parametrize("page", READONLY_PAGES)
def test_page_shows_nii_read_only_with_edit_link(page):
    at = _run(page, net_inv_income=25_000)
    assert not at.exception, [e.value for e in at.exception]
    assert _nii_inputs(at) == []
    assert any("\\$25,000" in m.value for m in at.markdown)
    at.button(key="edit_nii").click().run()
    assert at.session_state["nav_page"] == "assumptions"


def test_nii_survives_leaving_assumptions():
    """Regression pin: NII survives page navigation.

    It passed before PR 2 too, so it does not demonstrate a prior bug.
    """
    at = _run("assumptions")
    _nii_inputs(at)[0].set_value(40_000).run()
    assert at.session_state["net_inv_income"] == 40_000
    for page in ["overview", "sweet", "assumptions"]:
        _go(at, page)
        assert at.session_state["net_inv_income"] == 40_000
    assert _nii_inputs(at)[0].value == 40_000


def test_nii_round_trips_through_the_unkeyed_input():
    at = _run("assumptions", net_inv_income=12_000)
    assert not at.exception, [e.value for e in at.exception]
    (nii,) = _nii_inputs(at)
    assert nii.value == 12_000
    assert at.session_state["net_inv_income"] == 12_000
    # Unkeyed on purpose: no widget may own the session key.
    assert all(n.key != "net_inv_income" for n in at.number_input)

    nii.set_value(20_000).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["net_inv_income"] == 20_000
    (nii,) = _nii_inputs(at)
    assert nii.value == 20_000
