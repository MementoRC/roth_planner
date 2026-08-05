"""Tests for the W2 Part B "Sync everything" fan-out (views/_shared.py::sync_everything).

- B1: ``sync_everything`` fans out to all three already-candidate-based
  ingestion paths (FinExtract portfolio, FinExtract SS, unified PDF folder
  scan), returns a combined per-source summary, and every produced value
  lands PENDING (freeze-until-confirm gate unchanged — nothing commits).
  A raising FinExtract portfolio fetch still lets the SS + scan sources
  record (independent error isolation).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import views._shared as shared_mod
import views.setup._partials._accounts as partials_mod
import views.setup.portfolio as portfolio_mod
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.committed import load_committed
from engine.data_sources.resolver import magi_field_key
from engine.pdf_import import PdfImportResult
from engine.portfolio_sync import (
    AccountSummary,
    DividendsRollupSnapshot,
    EquityGrant,
    OptionExercisesSnapshot,
    PortfolioSnapshot,
    SSABenefitEstimate,
    SSASnapshot,
)
from engine.tax_return_pdf import Form1040Record
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot

_SCAN_YEAR = 2023
_SCAN_MAGI = 210_000.0

# clean_command_center_caches fixture is provided by tests/conftest.py (cleans
# up the 3 Command Center cache files -- CANDIDATE_STORE_PATH, TRUST_CHOICES_PATH,
# COMMITTED_PATH -- BEFORE and AFTER each test) -- do not redeclare a narrower
# local fixture here. This file previously had a same-purpose but differently
# -named ``clean_candidate_caches`` fixture that only cleaned up 2 of the 3
# cache files, omitting TRUST_CHOICES_PATH; nothing in this module writes to
# trust_choices.json today, but the gap was inconsistent with the sibling
# fixture and a latent risk if that ever changes.

_FORM_1040 = Form1040Record(
    tax_year=_SCAN_YEAR,
    agi=200_000.0,
    tax_exempt_interest=0.0,
    taxable_ss=0.0,
    qualified_dividends=0.0,
    ordinary_dividends=0.0,
    feie=0.0,
    magi=_SCAN_MAGI,
    filing_status=None,
    captured_at="2026-07-17T00:00:00+00:00",
)


class _FakeSessionState(dict):
    """Minimal stand-in for Streamlit's SessionStateProxy (attr + dict access)."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value) -> None:
        self[name] = value


def _stub_hh() -> Household:
    return Household(your_age=61, spouse_age=55, your_ira=500_000, spouse_ira=500_000)


def _stub_portfolio_snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[
            AccountSummary(
                account_type="trad_ira", owner="you", account_name="IRA1", total_value=750_000.0
            )
        ],
        equity_grants=[],
        server_available=True,
    )


def _mock_st(**session_overrides) -> MagicMock:
    mock_st = MagicMock()
    state = _FakeSessionState(
        account_type_overrides=None,
        _user_grant_strikes=None,
        filing_status="MFJ",
        your_fra_age=67,
        spouse_fra_age=67,
    )
    state.update(session_overrides)
    mock_st.session_state = state
    return mock_st


def _stub_portfolio_snapshot_with_grants() -> PortfolioSnapshot:
    """A snapshot carrying one REAL outstanding equity grant.

    ``_stub_portfolio_snapshot`` above deliberately uses ``equity_grants=[]``
    and never exercises grant serialization (see the "Sync everything" crash
    this regression test guards against) — this variant drives the real
    ``EquityGrant`` -> ``StockGrant`` merge + candidate-store persist path.
    """
    return PortfolioSnapshot(
        accounts=[
            AccountSummary(
                account_type="trad_ira", owner="you", account_name="IRA1", total_value=750_000.0
            )
        ],
        equity_grants=[
            EquityGrant(
                grant_id="NQO-2020-A",
                grant_type="NQO",
                grant_date="2020-03-15",
                shares_granted=1000,
                outstanding=600,
                current_value=45_000.0,
            )
        ],
        server_available=True,
    )


def _patch_portfolio_fetches(*, fetch_portfolio_side_effect=None, snapshot=None):
    """Patches for every network-touching name imported into portfolio_mod."""
    patches = [
        patch.object(
            portfolio_mod,
            "fetch_dividends_rollup",
            return_value=DividendsRollupSnapshot(server_available=False),
        ),
        patch.object(portfolio_mod, "save_snapshot", MagicMock()),
        patch.object(portfolio_mod, "fetch_magi", return_value=None),
        patch.object(portfolio_mod, "fetch_ytd_snapshot", return_value=YTDSnapshot()),
        patch.object(
            portfolio_mod,
            "fetch_option_exercises_with_cache",
            return_value=OptionExercisesSnapshot(server_available=False),
        ),
        patch.object(portfolio_mod, "save_ytd_snapshot", MagicMock()),
    ]
    if fetch_portfolio_side_effect is not None:
        patches.append(
            patch.object(portfolio_mod, "fetch_portfolio", side_effect=fetch_portfolio_side_effect)
        )
    else:
        patches.append(
            patch.object(
                portfolio_mod,
                "fetch_portfolio",
                return_value=snapshot if snapshot is not None else _stub_portfolio_snapshot(),
            )
        )
    return patches


def _patch_ss_fetch(*, estimates=None):
    """Patch views.setup._partials._accounts's fetch/save SSA snapshot calls.

    ``_sync_ssa_for`` (called by both each owner's inline "Sync SS from
    FinExtract" button and ``views._shared._sync_ss_source``) moved from
    ``views.setup.parameters`` to ``views.setup._partials`` in Task 4 of the
    ui-shell-theme-toggle plan, and now lives in the package's
    ``_accounts`` submodule (post Task-6b package split) — patches must
    target that submodule directly (not the package's ``__init__.py``
    re-export) since ``_sync_ssa_for``'s internal calls to
    ``fetch_ssa_snapshot``/``save_ssa_snapshot``/``st`` resolve against its
    OWN defining module's globals, not the package namespace.
    """
    estimates = estimates if estimates is not None else [
        SSABenefitEstimate(retirement_age=67, claim_date="", benefit_type="", monthly_amount=2500.0)
    ]
    return [
        patch.object(
            partials_mod,
            "fetch_ssa_snapshot",
            return_value=SSASnapshot(server_available=True, estimates=estimates),
        ),
        patch.object(partials_mod, "save_ssa_snapshot", MagicMock()),
    ]


def _patch_scan(tmp_path, monkeypatch):
    import engine.tax_return_pdf as tax_return_pdf_mod

    monkeypatch.setattr(tax_return_pdf_mod, "_PDF_TAX_CACHE_PATH", tmp_path / ".tax_pdf_cache.json")
    return [
        patch(
            "engine.pdf_import.scan_pdf_folder",
            return_value=PdfImportResult(form_1040_records={_SCAN_YEAR: _FORM_1040}),
        ),
        patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=str(tmp_path)),
    ]


def test_sync_everything_fans_out_and_every_value_lands_pending(
    clean_command_center_caches, tmp_path, monkeypatch
):
    hh = _stub_hh()
    mock_st = _mock_st()

    patches = (
        _patch_portfolio_fetches()
        + _patch_ss_fetch()
        + _patch_scan(tmp_path, monkeypatch)
        + [
            patch.object(portfolio_mod, "st", mock_st),
            patch.object(partials_mod, "st", mock_st),
            patch.object(shared_mod, "st", mock_st),
        ]
    )
    with _ApplyAll(patches):
        result = shared_mod.sync_everything(hh)

    # Per-source counts: portfolio recorded your_ira only (spouse_ira/roth/txn
    # price/grants all zero-valued in the stub snapshot); SS recorded both
    # you+spouse (MFJ); scan recorded one MAGI year.
    assert result.portfolio.server_available is True
    assert result.portfolio.error is None
    assert result.portfolio.candidates_recorded == 1

    assert result.ss.candidates_recorded == 2
    assert result.ss.warnings == []

    assert result.scan.error is None
    assert result.scan.result is not None
    assert result.scan.result.magi_candidates_recorded == 1

    # Freeze invariant: every produced value is PENDING (a candidate exists),
    # and nothing was ever committed.
    # audit-0805 W1: re-import at test-run time (not the module-level binding
    # frozen at collection) to see tests/conftest.py's per-test redirect.
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH

    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    assert store.has_candidates("your_ira")
    assert store.has_candidates("your_ss_fra")
    assert store.has_candidates("spouse_ss_fra")
    assert store.has_candidates(magi_field_key(_SCAN_YEAR))

    assert load_committed(COMMITTED_PATH) is None


def test_sync_everything_isolates_a_raising_portfolio_fetch(
    clean_command_center_caches, tmp_path, monkeypatch
):
    """A raising FinExtract portfolio fetch must not prevent SS/scan from recording."""
    hh = _stub_hh()
    mock_st = _mock_st()

    patches = (
        _patch_portfolio_fetches(fetch_portfolio_side_effect=RuntimeError("FinExtract unreachable"))
        + _patch_ss_fetch()
        + _patch_scan(tmp_path, monkeypatch)
        + [
            patch.object(portfolio_mod, "st", mock_st),
            patch.object(partials_mod, "st", mock_st),
            patch.object(shared_mod, "st", mock_st),
        ]
    )
    with _ApplyAll(patches):
        result = shared_mod.sync_everything(hh)

    assert result.portfolio.server_available is False
    assert result.portfolio.candidates_recorded == 0
    assert "FinExtract unreachable" in (result.portfolio.error or "")

    # Independent sources still ran and recorded.
    assert result.ss.candidates_recorded == 2
    assert result.scan.result is not None
    assert result.scan.result.magi_candidates_recorded == 1

    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH

    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    assert not store.has_candidates("your_ira")
    assert store.has_candidates("your_ss_fra")
    assert store.has_candidates(magi_field_key(_SCAN_YEAR))
    assert load_committed(COMMITTED_PATH) is None


def test_sync_everything_persists_real_stock_grant_candidates_to_disk(
    clean_command_center_caches, tmp_path, monkeypatch
):
    """Reproduces the production crash: a fetched snapshot with a REAL
    outstanding ``EquityGrant`` (merged into a REAL ``StockGrant``) must
    round-trip through an actual ``CandidateStore.save``/``.load`` at a real
    temp-file path without raising
    ``TypeError: Object of type StockGrant is not JSON serializable``, and
    the grant candidate must be readable back as a live ``StockGrant``.
    """
    store_path = tmp_path / "candidate_store.json"
    monkeypatch.setattr(shared_mod, "CANDIDATE_STORE_PATH", store_path)

    hh = _stub_hh()
    mock_st = _mock_st(_user_grant_strikes={"2020": 130.0})

    patches = (
        _patch_portfolio_fetches(snapshot=_stub_portfolio_snapshot_with_grants())
        + _patch_ss_fetch()
        + _patch_scan(tmp_path, monkeypatch)
        + [
            patch.object(portfolio_mod, "st", mock_st),
            patch.object(partials_mod, "st", mock_st),
            patch.object(shared_mod, "st", mock_st),
        ]
    )
    with _ApplyAll(patches):
        result = shared_mod.sync_everything(hh)

    assert result.portfolio.server_available is True
    assert result.portfolio.error is None

    # The store must have actually hit disk (this is where the production
    # crash occurred: json.dumps on a raw StockGrant).
    assert store_path.exists()
    store = CandidateStore.load(store_path)
    candidates = store.candidates_for("grants")
    assert len(candidates) == 1
    grants = candidates[0].value
    assert len(grants) == 1
    assert isinstance(grants[0], StockGrant)
    assert grants[0].year == 2020
    assert grants[0].strike == 130.0
    assert grants[0].shares == 600


class _ApplyAll:
    """Enter/exit a list of ``unittest.mock.patch`` context managers together."""

    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False
