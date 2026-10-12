"""Tests for the NetBenefits 403(b) review panel on the Setup ▸ Data page
(``views/ytd_income/_partials/_sync_scan.py``).

Mocked-``st`` harness, same style as tests/test_sync_scan_uploader.py. The
statement comes from the synthetic fixture in
tests/test_netbenefits_statement_pdf.py -- no real names or amounts.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from engine.netbenefits_statement_pdf import parse_netbenefits_text
from engine.pdf_import import PdfImportResult
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
from models.ytd_income import YTDSnapshot
from tests.test_netbenefits_statement_pdf import PAGES
from views.ytd_income._partials import _sync_scan as sync_scan_mod


class _RerunError(Exception):
    pass


def _mock_st(*, state_extra=None, clicks=(), radio_choice=None):
    mock_st = MagicMock()
    state: dict = {"ytd_snapshot": YTDSnapshot(), **(state_extra or {})}
    session_state = MagicMock()
    session_state.get.side_effect = lambda key, default=None: state.get(key, default)
    session_state.__setitem__.side_effect = state.__setitem__
    session_state.__getitem__.side_effect = state.__getitem__
    session_state.__contains__.side_effect = state.__contains__
    session_state.pop.side_effect = lambda key, default=None: state.pop(key, default)
    mock_st.session_state = session_state
    mock_st.button.side_effect = lambda label, key=None, **kw: key in clicks
    # Buttons drawn into an st.empty() slot must obey the same click set.
    mock_st.empty.return_value = mock_st
    mock_st.columns.side_effect = lambda n: [
        MagicMock() for _ in range(n if isinstance(n, int) else len(n))
    ]
    mock_st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_st.expander.return_value.__exit__ = MagicMock(return_value=False)

    def _radio(label, options, index=0, key=None, **kw):
        return radio_choice if radio_choice is not None else options[index]

    mock_st.radio.side_effect = _radio
    mock_st.rerun.side_effect = _RerunError
    return mock_st, state


def _snap() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[
            AccountSummary(account_type="trad_ira", owner="spouse", total_value=1_000.0),
            AccountSummary(
                account_type="403b", owner="spouse", account_name="Old 403b", total_value=9.0
            ),
            AccountSummary(account_type="trad_ira", owner="you", total_value=500.0),
        ]
    )


class TestScanStoresPendingStatements:
    def test_scan_result_queues_statement_for_review_and_touches_no_ytd(self) -> None:
        mock_st, state = _mock_st()
        result = PdfImportResult(netbenefits_statements=[parse_netbenefits_text(PAGES)])

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

        [pending] = state["netbenefits_statements"]
        assert pending.plan_name == "Example State University 403b"
        save_ytd.assert_not_called()
        success_text = " ".join(str(c.args[0]) for c in mock_st.success.call_args_list)
        assert "NetBenefits" in success_text

    def test_rescanning_keeps_only_the_newest_statement(self) -> None:
        older = parse_netbenefits_text([p.replace("06/30/2026", "03/31/2026") for p in PAGES])
        newer = parse_netbenefits_text(PAGES)
        mock_st, state = _mock_st(state_extra={"netbenefits_statements": [newer]})

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "save_ytd_snapshot"),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.brokerage_statement_pdf.save_statement_records"),
        ):
            sync_scan_mod._apply_scan_result(
                PdfImportResult(netbenefits_statements=[older]),
                ledger={"koinly": {}, "brokerage": {}},
                account_overrides={},
                owner_map={},
                instance_owner="you",
            )

        assert [s.period_end for s in state["netbenefits_statements"]] == ["2026-06-30"]


class TestReviewPanel:
    def _render(self, mock_st, *, owner_map=None, identity_set=True):
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "load_snapshot", return_value=_snap()),
            patch.object(sync_scan_mod, "save_snapshot") as save_snap,
            patch.object(sync_scan_mod, "record_ira_balance_candidate") as record,
            patch.object(sync_scan_mod, "save_owner_map") as save_map,
            contextlib.suppress(_RerunError),
        ):
            sync_scan_mod._render_netbenefits_review(
                owner_map=owner_map or {},
                instance_owner="you",
                identity_set=identity_set,
            )
        return save_snap, record, save_map

    def test_apply_replaces_the_chosen_owners_403b_and_records_a_pdf_candidate(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        mock_st, state = _mock_st(
            state_extra={"netbenefits_statements": [stmt]},
            clicks={"netbenefits_apply_0"},
            radio_choice="spouse",
        )

        save_snap, record, save_map = self._render(mock_st)

        [saved] = save_snap.call_args.args
        spouse_403b = [
            a for a in saved.accounts if a.owner == "spouse" and a.account_type == "403b"
        ]
        assert [a.total_value for a in spouse_403b] == [pytest.approx(34_900.0)]
        assert state["portfolio_snapshot"] is saved
        field_key, value, detail, _when = record.call_args.args
        assert field_key == "spouse_ira"
        assert value == pytest.approx(35_900.0)
        assert "2026-06-30" in detail
        # The unmapped holder name is learned as the owner the user chose.
        assert save_map.call_args.args[0] == {"jane q sample": "spouse"}
        assert state["netbenefits_statements"] == []
        mock_st.rerun.assert_called_once()

    def test_you_owner_apply_records_your_ira(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        mock_st, _state = _mock_st(
            state_extra={"netbenefits_statements": [stmt]},
            clicks={"netbenefits_apply_0"},
            radio_choice="you",
        )

        _save_snap, record, _save_map = self._render(mock_st)

        assert record.call_args.args[0] == "your_ira"

    def test_dollar_amounts_are_escaped_so_markdown_skips_latex(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        mock_st, _state = _mock_st(
            state_extra={"netbenefits_statements": [stmt]}, radio_choice="spouse"
        )

        self._render(mock_st)

        texts = [
            str(c.args[0]) for c in mock_st.write.call_args_list + mock_st.caption.call_args_list
        ]
        dollar_texts = [t for t in texts if "$" in t]
        assert any(t.startswith("**Example State University 403b**") for t in dollar_texts)
        assert any(t.startswith("Holdings:") for t in dollar_texts)
        assert any(t.startswith("Replaces:") for t in dollar_texts)
        for text in dollar_texts:
            assert text.count("$") == text.count("\\$")

    def test_no_click_writes_nothing(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        mock_st, state = _mock_st(state_extra={"netbenefits_statements": [stmt]})

        save_snap, record, save_map = self._render(mock_st)

        save_snap.assert_not_called()
        record.assert_not_called()
        save_map.assert_not_called()
        assert state["netbenefits_statements"] == [stmt]

    def test_already_mapped_holder_is_not_rewritten(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        mock_st, _state = _mock_st(
            state_extra={"netbenefits_statements": [stmt]}, clicks={"netbenefits_apply_0"}
        )

        _save_snap, record, save_map = self._render(mock_st, owner_map={"jane q sample": "spouse"})

        assert record.call_args.args[0] == "spouse_ira"
        save_map.assert_not_called()

    def test_apply_is_disabled_until_the_instance_has_an_owner(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        mock_st, _state = _mock_st(state_extra={"netbenefits_statements": [stmt]})

        self._render(mock_st, identity_set=False)

        [apply_call] = [
            c for c in mock_st.button.call_args_list if c.kwargs.get("key") == "netbenefits_apply_0"
        ]
        assert apply_call.kwargs["disabled"] is True

    def test_dismiss_drops_the_statement_without_writing(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        mock_st, state = _mock_st(
            state_extra={"netbenefits_statements": [stmt]}, clicks={"netbenefits_dismiss_0"}
        )

        save_snap, record, _save_map = self._render(mock_st)

        save_snap.assert_not_called()
        record.assert_not_called()
        assert state["netbenefits_statements"] == []
