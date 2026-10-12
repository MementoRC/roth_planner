"""Tests for queueing + reviewing IRA statement balances on the Import page
(``views/ytd_income/_partials/_sync_scan.py``).

Mocked-``st`` harness, same style as tests/test_sync_scan_netbenefits.py.
Synthetic names and round amounts only.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.ira_statement_balance import apply_ira_statement, to_ira_account_summary
from engine.pdf_import import PdfImportResult
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
from engine.portfolio_sync.as_of import account_key
from models.ytd_income import YTDSnapshot
from views.ytd_income._partials import _sync_scan as sync_scan_mod


class _RerunError(Exception):
    pass


def _mock_st(*, state_extra=None, clicks=(), radio_choice=None, link_choice=None):
    """``link_choice``: None = the pre-selected option, "none" = the last
    ("separate account") option, an int = that option index."""
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
    mock_st.columns.side_effect = lambda n: [
        MagicMock() for _ in range(n if isinstance(n, int) else len(n))
    ]
    mock_st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_st.expander.return_value.__exit__ = MagicMock(return_value=False)

    def _radio(label, options, index=0, key=None, **kw):
        return radio_choice if radio_choice is not None else options[index]

    mock_st.radio.side_effect = _radio

    def _selectbox(label, options, index=0, key=None, **kw):
        if link_choice is None:
            return options[index]
        return options[-1] if link_choice == "none" else options[link_choice]

    mock_st.selectbox.side_effect = _selectbox
    mock_st.rerun.side_effect = _RerunError
    return mock_st, state


def _rec(**kw: object) -> BrokerageStatementRecord:
    base: dict[str, object] = {
        "account_number": "123-456789",
        "broker": "fidelity",
        "account_type": "traditional_ira",
        "statement_period_end": "2026-08-31",
        "interest_taxable_ytd": 0.0,
        "interest_tax_exempt_ytd": 0.0,
        "dividends_taxable_ytd": 0.0,
        "dividends_tax_exempt_ytd": 0.0,
        "stcg_net_ytd": 0.0,
        "ltcg_net_ytd": 0.0,
        "captured_at": "2026-09-01T00:00:00",
        "owner_key": "JANE Q SAMPLE",
        "ending_value": 100_000.0,
    }
    base.update(kw)
    return BrokerageStatementRecord(**base)  # type: ignore[arg-type]


def _snap() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[
            AccountSummary(account_type="trad_ira", owner="you", total_value=500.0),
            AccountSummary(account_type="roth_ira", owner="you", total_value=300.0),
        ]
    )


def _scan(mock_st, result: PdfImportResult) -> None:
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "save_ytd_snapshot"),
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


class TestScanQueuesIraStatements:
    def test_ira_record_with_a_value_is_queued(self) -> None:
        mock_st, state = _mock_st()
        rec = _rec()
        _scan(mock_st, PdfImportResult(brokerage_records=[rec]))
        assert state["ira_statements"] == [rec]

    def test_taxable_and_valueless_records_are_not_queued(self) -> None:
        mock_st, state = _mock_st()
        records = [
            _rec(account_type="taxable", account_number="T1"),
            _rec(account_number="V1", ending_value=None),
        ]
        _scan(mock_st, PdfImportResult(brokerage_records=records))
        assert not state.get("ira_statements")

    def test_rescan_keeps_only_the_newest_per_account(self) -> None:
        newer = _rec()
        older = _rec(statement_period_end="2026-07-31", ending_value=1.0)
        mock_st, state = _mock_st(state_extra={"ira_statements": [newer]})
        _scan(mock_st, PdfImportResult(brokerage_records=[older]))
        assert state["ira_statements"] == [newer]

    def test_queueing_leaves_ytd_flow_unchanged(self) -> None:
        mock_st, state = _mock_st()
        _scan(mock_st, PdfImportResult(brokerage_records=[_rec()]))
        assert list(state["statement_by_account"]) == ["123-456789"]
        success = " ".join(str(c.args[0]) for c in mock_st.success.call_args_list)
        assert "IRA statement" in success


class TestClearKeys:
    def test_clear_queued_statements_drops_ira_statements(self) -> None:
        state = {"ira_statements": [_rec()], "pdf_upload_nonce": 1}
        sync_scan_mod.clear_queued_statements(state)
        assert "ira_statements" not in state
        assert state["pdf_upload_nonce"] == 2


class TestReviewPanel:
    def _render(self, mock_st, *, snap=None, owner_map=None, identity_set=True, link_map=None):
        self.save_links = MagicMock()
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "load_snapshot", return_value=snap or _snap()),
            patch.object(sync_scan_mod, "load_link_map", return_value=link_map or {}),
            patch.object(sync_scan_mod, "save_link_map", self.save_links),
            patch.object(sync_scan_mod, "save_snapshot") as save_snap,
            patch.object(sync_scan_mod, "record_ira_balance_candidate") as record_ira,
            patch.object(sync_scan_mod, "record_roth_balance_candidate") as record_roth,
            patch.object(sync_scan_mod, "save_owner_map") as save_map,
            contextlib.suppress(_RerunError),
        ):
            sync_scan_mod._render_ira_statement_review(
                owner_map=owner_map or {}, instance_owner="you", identity_set=identity_set
            )
        return save_snap, record_ira, record_roth, save_map

    def test_apply_traditional_replaces_snapshot_and_records_ira_candidate(self) -> None:
        mock_st, state = _mock_st(
            state_extra={"ira_statements": [_rec()]},
            clicks={"ira_statement_apply_0"},
            radio_choice="spouse",
        )

        save_snap, record_ira, record_roth, save_map = self._render(mock_st)

        [saved] = save_snap.call_args.args
        assert [a.owner for a in saved.accounts if a.account_name.startswith("Fidelity")] == [
            "spouse"
        ]
        assert state["portfolio_snapshot"] is saved
        field_key, value, detail, _when = record_ira.call_args.args
        assert (field_key, value) == ("spouse_ira", pytest.approx(100_000.0))
        assert "2026-08-31" in detail
        record_roth.assert_not_called()
        assert save_map.call_args.args[0] == {"jane q sample": "spouse"}
        assert state["ira_statements"] == []
        mock_st.rerun.assert_called_once()

    def test_apply_roth_records_a_roth_candidate_with_the_roth_total(self) -> None:
        mock_st, _state = _mock_st(
            state_extra={"ira_statements": [_rec(account_type="roth_ira", ending_value=22_000.0)]},
            clicks={"ira_statement_apply_0"},
            radio_choice="you",
            link_choice="none",
        )

        _save_snap, record_ira, record_roth, _save_map = self._render(mock_st)

        field_key, value, _detail, _when = record_roth.call_args.args
        assert (field_key, value) == ("your_roth", pytest.approx(22_300.0))
        record_ira.assert_not_called()

    def test_no_click_writes_nothing(self) -> None:
        rec = _rec()
        mock_st, state = _mock_st(state_extra={"ira_statements": [rec]})

        save_snap, record_ira, record_roth, save_map = self._render(mock_st)

        save_snap.assert_not_called()
        record_ira.assert_not_called()
        record_roth.assert_not_called()
        save_map.assert_not_called()
        assert state["ira_statements"] == [rec]

    def test_already_mapped_holder_is_not_rewritten(self) -> None:
        mock_st, _state = _mock_st(
            state_extra={"ira_statements": [_rec()]}, clicks={"ira_statement_apply_0"}
        )

        _s, record_ira, _r, save_map = self._render(mock_st, owner_map={"jane q sample": "you"})

        assert record_ira.call_args.args[0] == "your_ira"
        save_map.assert_not_called()

    def test_default_owner_comes_from_the_learned_holder_name(self) -> None:
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]})

        self._render(mock_st, owner_map={"jane q sample": "spouse"})

        radio = mock_st.radio.call_args
        assert radio.args[1] == ["you", "spouse"]
        assert radio.kwargs["index"] == 1

    def test_apply_disabled_until_the_instance_has_an_owner(self) -> None:
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]})

        self._render(mock_st, identity_set=False)

        [call] = [
            c
            for c in mock_st.button.call_args_list
            if c.kwargs.get("key") == "ira_statement_apply_0"
        ]
        assert call.kwargs["disabled"] is True

    def test_dollar_amounts_are_escaped(self) -> None:
        cached = to_ira_account_summary(_rec(ending_value=90_000.0), "you")
        cached.as_of = "2026-07-31"
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]})

        self._render(mock_st, snap=PortfolioSnapshot(accounts=[cached]))

        texts = [
            str(c.args[0]) for c in mock_st.write.call_args_list + mock_st.caption.call_args_list
        ]
        dollar_texts = [t for t in texts if "$" in t]
        assert any(t.startswith("**Fidelity Traditional IRA …6789**") for t in dollar_texts)
        assert any(t.startswith("Replaces:") for t in dollar_texts)
        for text in dollar_texts:
            assert text.count("$") == text.count("\\$")

    def test_adds_caption_when_nothing_to_replace(self) -> None:
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]}, link_choice="none")

        self._render(mock_st)

        captions = " ".join(str(c.args[0]) for c in mock_st.caption.call_args_list)
        assert "Adds" in captions

    def test_other_same_type_accounts_are_listed_as_not_replaced(self) -> None:
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]}, link_choice="none")

        self._render(mock_st)

        captions = " ".join(str(c.args[0]) for c in mock_st.caption.call_args_list)
        assert "not replaced" in captions

    def test_selectbox_defaults_to_the_account_whose_number_matches(self) -> None:
        other = AccountSummary(account_type="trad_ira", owner="you", account_name="Other")
        match = AccountSummary(
            account_type="trad_ira", owner="you", account_name="Rollover IRA123456789"
        )
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]})

        self._render(mock_st, snap=PortfolioSnapshot(accounts=[other, match]))

        call = mock_st.selectbox.call_args
        assert call.args[0] == "Replaces which account on file?"
        assert call.kwargs["index"] == 1
        assert call.args[1] == [0, 1, 2]
        assert call.kwargs["format_func"](2) == "None — it's a separate account"
        captions = " ".join(str(c.args[0]) for c in mock_st.caption.call_args_list)
        assert "otherwise leave it as" not in captions

    def test_no_suggestion_defaults_to_separate_account(self) -> None:
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]})

        self._render(mock_st)

        call = mock_st.selectbox.call_args
        assert call.kwargs["index"] == len(call.args[1]) - 1
        captions = " ".join(str(c.args[0]) for c in mock_st.caption.call_args_list)
        assert "otherwise leave it as a separate account" in captions

    def test_apply_without_suggestion_deletes_nothing(self) -> None:
        mock_st, _state = _mock_st(
            state_extra={"ira_statements": [_rec()]}, clicks={"ira_statement_apply_0"}
        )

        save_snap, _i, _r, _m = self._render(mock_st)

        [saved] = save_snap.call_args.args
        assert 500.0 in [a.total_value for a in saved.accounts]
        assert self.save_links.call_args.args[0] == {"fidelity|123-456789": "NEW"}

    def test_remembered_new_defaults_to_separate_account(self) -> None:
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]})

        self._render(mock_st, link_map={"fidelity|123-456789": "NEW"})

        assert mock_st.selectbox.call_args.kwargs["index"] == 1

    def test_no_candidates_means_no_selectbox(self) -> None:
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]})

        self._render(mock_st, snap=PortfolioSnapshot())

        mock_st.selectbox.assert_not_called()

    def test_apply_with_selected_candidate_links_and_persists(self) -> None:
        synced = AccountSummary(
            account_type="trad_ira",
            owner="you",
            account_name="Rollover IRA123456789",
            total_value=90_000.0,
            as_of="2026-07-31",
            as_of_source="finextract",
        )
        mock_st, _state = _mock_st(
            state_extra={"ira_statements": [_rec()]}, clicks={"ira_statement_apply_0"}
        )

        with patch.object(sync_scan_mod, "apply_ira_statement") as apply_mock:
            apply_mock.return_value = apply_ira_statement(
                PortfolioSnapshot(accounts=[synced]),
                _rec(),
                "you",
                replaces=account_key(synced),
            )
            save_snap, record_ira, _r, _m = self._render(
                mock_st, snap=PortfolioSnapshot(accounts=[synced])
            )

        assert apply_mock.call_args_list[-1].kwargs["replaces"] == account_key(synced)
        [saved] = save_snap.call_args.args
        assert [a.account_name for a in saved.accounts] == ["Rollover IRA123456789"]
        [links] = self.save_links.call_args.args
        assert links == {"fidelity|123-456789": account_key(synced)}
        assert record_ira.call_args.args[1] == pytest.approx(100_000.0)

    def test_apply_with_separate_account_persists_new(self) -> None:
        mock_st, _state = _mock_st(
            state_extra={"ira_statements": [_rec()]},
            clicks={"ira_statement_apply_0"},
            link_choice="none",
        )

        self._render(mock_st)

        assert self.save_links.call_args.args[0] == {"fidelity|123-456789": "NEW"}

    def test_newer_cache_warns_and_offers_use_anyway(self) -> None:
        cached = to_ira_account_summary(_rec(ending_value=120_000.0), "you")
        cached.as_of = "2026-09-30"
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]})

        self._render(mock_st, snap=PortfolioSnapshot(accounts=[cached]))

        assert mock_st.warning.called
        labels = [c.args[0] for c in mock_st.button.call_args_list]
        assert "Use anyway" in labels

    def test_use_anyway_forces_the_older_statement_in(self) -> None:
        cached = to_ira_account_summary(_rec(ending_value=120_000.0), "you")
        cached.as_of = "2026-09-30"
        mock_st, _state = _mock_st(
            state_extra={"ira_statements": [_rec()]}, clicks={"ira_statement_apply_0"}
        )

        save_snap, record_ira, _r, _m = self._render(
            mock_st, snap=PortfolioSnapshot(accounts=[cached])
        )

        [saved] = save_snap.call_args.args
        assert [a.total_value for a in saved.accounts] == [100_000.0]
        assert record_ira.call_args.args[1] == pytest.approx(100_000.0)

    def test_dismiss_drops_the_statement_without_writing(self) -> None:
        mock_st, state = _mock_st(
            state_extra={"ira_statements": [_rec()]}, clicks={"ira_statement_dismiss_0"}
        )

        save_snap, record_ira, record_roth, _m = self._render(mock_st)

        save_snap.assert_not_called()
        record_ira.assert_not_called()
        record_roth.assert_not_called()
        assert state["ira_statements"] == []


class TestResultsRendersReview:
    def test_review_is_rendered_when_statements_are_queued(self) -> None:
        mock_st, _state = _mock_st(state_extra={"ira_statements": [_rec()]})
        ctx = sync_scan_mod.ScanRenderContext(
            ledger={"koinly": {}, "brokerage": {}},
            account_overrides={},
            owner_map={},
            instance_owner="you",
            identity_set=True,
        )
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "is_pyodide", return_value=True),
            patch.object(sync_scan_mod, "_render_holder_name_mismatches"),
            patch.object(sync_scan_mod, "_render_ira_statement_review") as review,
        ):
            sync_scan_mod.render_sync_scan_results(ctx)
        review.assert_called_once()
