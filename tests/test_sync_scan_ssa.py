"""Tests for the SSA statement review panel on the Setup ▸ Data page
(``views/ytd_income/_partials/_sync_scan.py``) and the persisted comparison
under the SS-at-FRA field (``views/setup/_partials/_accounts.py``).

Mocked-``st`` harness, same style as tests/test_sync_scan_netbenefits.py. The
statement comes from the synthetic fixture in tests/test_ssa_statement_pdf.py
-- no real names, dates of birth or amounts.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.record import record_ss_fra_candidate
from engine.pdf_import import PdfImportResult
from engine.ssa_statement_pdf import (
    load_ssa_pdf_estimates,
    parse_ssa_statement_text,
    save_ssa_pdf_estimates,
)
from models.sourced import Source
from tests.test_ssa_statement_pdf import PAGE_1, PAGE_2, PAGES
from tests.test_sync_scan_netbenefits import _mock_st, _RerunError
from views.setup._partials import _accounts as accounts_mod
from views.ytd_income._partials import _sync_scan as sync_scan_mod


class TestScanQueuesStatements:
    def _apply(self, mock_st, result):
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "save_ytd_snapshot") as save_ytd,
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.brokerage_statement_pdf.save_statement_records"),
        ):
            sync_scan_mod._apply_scan_result(
                result,
                ledger={"koinly": {}, "brokerage": {}},
                account_overrides={},
                owner_map={},
                instance_owner="you",
            )
        return save_ytd

    def test_scan_queues_the_statement_and_touches_no_ytd(self) -> None:
        mock_st, state = _mock_st()
        save_ytd = self._apply(
            mock_st, PdfImportResult(ssa_statements=[parse_ssa_statement_text(PAGES)])
        )

        [pending] = state["ssa_statements"]
        assert pending.fra_monthly == 2000
        save_ytd.assert_not_called()
        success_text = " ".join(str(c.args[0]) for c in mock_st.success.call_args_list)
        assert "Social Security" in success_text

    def test_rescanning_keeps_only_the_newest_statement(self) -> None:
        older = parse_ssa_statement_text([PAGE_1.replace("March 4, 2026", "May 1, 2025"), PAGE_2])
        newer = parse_ssa_statement_text(PAGES)
        mock_st, state = _mock_st(state_extra={"ssa_statements": [newer]})

        self._apply(mock_st, PdfImportResult(ssa_statements=[older]))

        assert [s.statement_date for s in state["ssa_statements"]] == ["2026-03-04"]


def test_record_ss_fra_candidate_rejects_an_unknown_field_key() -> None:
    with pytest.raises(ValueError, match="you_ss_fra"):
        record_ss_fra_candidate("you_ss_fra", 2000, Source.PDF, "x", datetime(2026, 1, 1))


class TestReviewPanel:
    def _render(self, mock_st, *, owner_map=None, identity_set=True):
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "record_ss_fra_candidate") as record,
            patch.object(sync_scan_mod, "save_owner_map") as save_map,
            contextlib.suppress(_RerunError),
        ):
            sync_scan_mod._render_ssa_review(
                owner_map=owner_map or {},
                instance_owner="you",
                identity_set=identity_set,
            )
        return record, save_map

    def test_apply_records_a_pdf_candidate_and_saves_the_estimates(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        mock_st, state = _mock_st(
            state_extra={"ssa_statements": [stmt]},
            clicks={"ssa_apply_0"},
            radio_choice="spouse",
        )

        record, save_map = self._render(mock_st)

        field_key, value, source, detail, _when = record.call_args.args
        assert field_key == "spouse_ss_fra"
        assert value == pytest.approx(2000)
        assert source is Source.PDF
        assert "2026-03-04" in detail
        saved = load_ssa_pdf_estimates("spouse")
        assert saved is not None
        assert saved.estimates[70] == 2500
        assert save_map.call_args.args[0] == {"john q example": "spouse"}
        assert state["ssa_statements"] == []
        mock_st.rerun.assert_called_once()

    def test_apply_as_you_stores_a_your_ss_fra_pdf_candidate(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        mock_st, _state = _mock_st(
            state_extra={"ssa_statements": [stmt]},
            clicks={"ssa_apply_0"},
            radio_choice="you",
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "save_owner_map"),
            contextlib.suppress(_RerunError),
        ):
            sync_scan_mod._render_ssa_review(owner_map={}, instance_owner="you", identity_set=True)

        store = CandidateStore.load()
        [cand] = store.candidates_for("your_ss_fra")
        assert cand.prov.source is Source.PDF
        assert cand.value == pytest.approx(stmt.fra_monthly)

    def test_review_shows_the_ssa_vs_planner_comparison(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        mock_st, _state = _mock_st(state_extra={"ssa_statements": [stmt]})

        record, _save_map = self._render(mock_st)

        record.assert_not_called()
        [table] = [c.args[0] for c in mock_st.dataframe.call_args_list]
        assert [r["Claim age"] for r in table] == list(range(62, 71))
        assert table[-1]["SSA estimate"] == "$2,500"

    def test_old_statement_shows_a_notice(self) -> None:
        old = parse_ssa_statement_text([PAGE_1.replace("March 4, 2026", "May 1, 2023"), PAGE_2])
        mock_st, _state = _mock_st(state_extra={"ssa_statements": [old]})

        self._render(mock_st)

        warning_text = " ".join(str(c.args[0]) for c in mock_st.warning.call_args_list)
        assert "2023-05-01" in warning_text

    def test_apply_is_disabled_until_the_instance_has_an_owner(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        mock_st, _state = _mock_st(state_extra={"ssa_statements": [stmt]})

        self._render(mock_st, identity_set=False)

        [apply_call] = [
            c for c in mock_st.button.call_args_list if c.kwargs.get("key") == "ssa_apply_0"
        ]
        assert apply_call.kwargs["disabled"] is True

    def test_dismiss_drops_the_statement_without_writing(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        mock_st, state = _mock_st(state_extra={"ssa_statements": [stmt]}, clicks={"ssa_dismiss_0"})

        record, _save_map = self._render(mock_st)

        record.assert_not_called()
        assert load_ssa_pdf_estimates("you") is None
        assert state["ssa_statements"] == []


class TestPersistedComparison:
    def test_accounts_partial_shows_saved_estimates_next_to_the_planner(self) -> None:
        save_ssa_pdf_estimates("you", parse_ssa_statement_text(PAGES))
        container = MagicMock()
        expander = MagicMock()
        container.expander.return_value.__enter__ = MagicMock(return_value=expander)
        container.expander.return_value.__exit__ = MagicMock(return_value=False)

        accounts_mod._render_ssa_pdf_comparison(container, "you", fra_monthly=1900, fra_age=67)

        [table] = [c.args[0] for c in expander.dataframe.call_args_list]
        by_age = {r["Claim age"]: r for r in table}
        assert by_age[67]["SSA estimate"] == "$2,000"
        assert by_age[67]["Planner formula"] == "$1,900"
        assert "2026-03-04" in container.expander.call_args.args[0]

    def test_nothing_renders_without_a_saved_statement(self) -> None:
        container = MagicMock()
        accounts_mod._render_ssa_pdf_comparison(container, "spouse", fra_monthly=1900, fra_age=67)
        container.expander.assert_not_called()


def test_uploader_combines_ssa_statements() -> None:
    """The per-file upload loop must carry SSA statements into the combined
    result, or an uploaded statement would parse and then vanish."""
    stmt = parse_ssa_statement_text(PAGES)
    single = MagicMock(raw=PdfImportResult(ssa_statements=[stmt]))
    uploaded = MagicMock()
    uploaded.name = "ssa.pdf"
    uploaded.getvalue.return_value = b"%PDF-fake"
    mock_st, state = _mock_st(clicks={"scan_uploaded_pdfs_btn"})
    mock_st.file_uploader.return_value = [uploaded]
    mock_st.status.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_st.status.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
        patch.object(sync_scan_mod, "run_uploaded_scan", return_value=single),
        patch.object(sync_scan_mod, "save_ytd_snapshot"),
        patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
        patch("engine.brokerage_statement_pdf.save_statement_records"),
    ):
        sync_scan_mod._render_pdf_uploader(
            instance_owner="you",
            account_overrides={},
            owner_map={},
            ledger={"koinly": {}, "brokerage": {}},
            identity_set=True,
        )

    assert [s.statement_date for s in state["ssa_statements"]] == ["2026-03-04"]
