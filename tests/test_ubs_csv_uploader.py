"""Tests for the UBS ACTIVITY CSV uploader wired into the YTD sync/scan view
(``views/ytd_income/_partials/_sync_scan.py``: ``_render_ubs_csv_uploader``,
``_merge_ubs_orders``, ``_load_ubs_orders_safe``).

Mirrors ``tests/test_sync_scan_uploader.py``'s mocked-``st`` harness style
(``_mock_st``) -- these tests only need to assert which store/session-state
calls the view made, not a full rendered widget tree. This is the STAGE B
wiring layer; the CSV parser itself (``engine/ubs_activity_csv.py``) and its
own tests belong to stage A and are not touched here.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date
from unittest.mock import MagicMock, patch

from engine.ubs_activity_csv import UbsActivityOrder, UbsExerciseLot
from engine.ubs_activity_store import UbsActivityStoreError
from models.ytd_income import YTDSnapshot
from views.ytd_income._partials import _sync_scan as sync_scan_mod

# ---------------------------------------------------------------------------
# Synthetic UBS ACTIVITY CSV fixtures -- same shape as
# tests/test_ubs_activity_csv.py's BASIC_FIXTURE (one order, one lot),
# reconstructed here (not imported) so this stage-B test file has no
# dependency on the stage-A test module's internals.
# ---------------------------------------------------------------------------

_ORDER_HEADER = (
    '"Reference Number","Source","Entry Date (m/d/yyyy)","Execution Date (m/d/yyyy)",'
    '"Quantity","Order Type","Transaction Type","Limit Price (USD)","Execution Price (USD)",'
    '"Entered Through","Proceeds Method","Funds Available Date (m/d/yyyy)",'
    '"Total Shares Delivered"'
)
_LOT_HEADER = (
    '"Grant Number","Grant Date (m/d/yyyy)","Grant Price (USD)","Grant Quantity",'
    '"Execution Quantity","Open Quantity","Cancel Quantity","Total Fees (USD)",'
    '"Total Taxes (USD)","Gross Proceeds (USD)","Net Proceeds (USD)",'
    '"Funds Available Date (m/d/yyyy)"'
)


def _order_csv(reference_number: str) -> str:
    """One order/one lot CSV text. Numbers hand-verified to satisfy the
    reconciliation invariant exactly to the cent: strike_cost=90.00*40=
    3600.00, net = 7200.15 - 3600.00 - 8.00 - 1067.44 = 2524.71."""
    order_row = (
        f'"{reference_number}","Options","3/15/2026","3/15/2026","40","MKT","SDS","N/A",'
        '"180.00","WEB","","3/19/2026","0"'
    )
    lot_row = (
        '"S1234567890","2/1/2020","90.00","40","40","0","0","8.00","1067.44","7200.15",'
        '"2524.71","3/19/2026"'
    )
    return "\n".join(
        [
            "ACTIVITY",
            _ORDER_HEADER,
            '"General Info",,,,,,,,,,,,,',
            order_row,
            "",
            _LOT_HEADER,
            '"Order Summary",,,,,,,,,,,,',
            lot_row,
            "",
        ]
    )


def _stub_order(reference_number: str) -> UbsActivityOrder:
    lot = UbsExerciseLot(
        grant_number="S1234567890",
        grant_date=date(2020, 2, 1),
        grant_price=90.00,
        grant_quantity=40,
        execution_quantity=40,
        open_quantity=0,
        cancel_quantity=0,
        total_fees=8.00,
        total_taxes=1067.44,
        gross_proceeds=7200.15,
        net_proceeds=2524.71,
        funds_available_date=date(2026, 3, 19),
    )
    return UbsActivityOrder(
        reference_number=reference_number,
        source="Options",
        entry_date=date(2026, 3, 15),
        execution_date=date(2026, 3, 15),
        quantity=40,
        order_type="MKT",
        transaction_type="SDS",
        limit_price=None,
        execution_price=180.00,
        entered_through="WEB",
        proceeds_method="",
        funds_available_date=date(2026, 3, 19),
        total_shares_delivered=0,
        lots=(lot,),
    )


class _FakeUploadedFile:
    """Stand-in for Streamlit's ``UploadedFile`` -- only ``.name`` and
    ``.getvalue()`` are used by ``_render_ubs_csv_uploader``."""

    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self._data = text.encode()

    def getvalue(self) -> bytes:
        return self._data


def _mock_st(*, session_extra=None, uploaded_files=(), button_clicks=()):
    mock_st = MagicMock()
    state: dict = {"ytd_snapshot": YTDSnapshot(), **(session_extra or {})}
    session_state = MagicMock()
    session_state.get.side_effect = lambda key, default=None: state.get(key, default)
    session_state.__setitem__.side_effect = state.__setitem__
    session_state.__contains__.side_effect = state.__contains__
    mock_st.session_state = session_state
    mock_st.file_uploader.return_value = list(uploaded_files)
    mock_st.button.side_effect = lambda label, key=None, **kw: key in button_clicks
    return mock_st, state


class TestMergeNotReplace:
    """``_merge_ubs_orders`` -- pure dedup-by-reference_number merge."""

    def test_merge_keeps_existing_orders_not_in_new_upload(self) -> None:
        existing = [_stub_order("FA-OLD")]
        new = [_stub_order("FA-NEW")]
        merged = sync_scan_mod._merge_ubs_orders(existing, new)
        refs = {o.reference_number for o in merged}
        assert refs == {"FA-OLD", "FA-NEW"}

    def test_new_order_with_same_reference_wins(self) -> None:
        old = _stub_order("FA-1")
        new = _stub_order("FA-1")
        merged = sync_scan_mod._merge_ubs_orders([old], [new])
        assert merged == [new]


class TestLoadUbsOrdersSafe:
    """A corrupt store must surface loudly, never silently become []."""

    def test_corrupt_store_surfaces_error_not_silent_empty(self) -> None:
        mock_st = MagicMock()
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(
                sync_scan_mod,
                "load_ubs_orders",
                side_effect=UbsActivityStoreError("cache is corrupt"),
            ),
        ):
            result = sync_scan_mod._load_ubs_orders_safe()

        assert result == []
        errors = [c.args[0] for c in mock_st.error.call_args_list]
        assert any("corrupt" in e for e in errors), errors


def _patch_recompute_path(stack):
    """Patch every downstream call ``_render_ubs_csv_uploader`` makes after
    ``save_ubs_orders`` (the post-upload recompute) via *stack* (an
    ``ExitStack``) so these tests can isolate the upload/parse/merge/save
    behavior itself."""
    stack.enter_context(patch.object(sync_scan_mod, "derive_brokerage_totals", return_value={}))
    stack.enter_context(
        patch.object(sync_scan_mod, "correct_ubs_option_basis", return_value=({}, []))
    )
    stack.enter_context(patch.object(sync_scan_mod, "apply_brokerage_totals"))
    stack.enter_context(patch.object(sync_scan_mod, "apply_ubs_exercise_income", return_value=[]))
    stack.enter_context(patch.object(sync_scan_mod, "save_ytd_snapshot"))
    stack.enter_context(
        patch.object(sync_scan_mod, "auto_deselect_manual_entry", return_value=True)
    )


class TestUploadParseMergeSave:
    def test_upload_merges_with_existing_and_saves(self) -> None:
        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"},
            uploaded_files=[_FakeUploadedFile("order.csv", _order_csv("FA-NEW"))],
            button_clicks={"import_ubs_csv_btn"},
        )
        existing = [_stub_order("FA-OLD")]

        with ExitStack() as stack:
            stack.enter_context(patch.object(sync_scan_mod, "st", mock_st))
            stack.enter_context(
                patch.object(sync_scan_mod, "load_ubs_orders", return_value=existing)
            )
            mock_save = stack.enter_context(patch.object(sync_scan_mod, "save_ubs_orders"))
            _patch_recompute_path(stack)
            sync_scan_mod._render_ubs_csv_uploader(
                ledger={"koinly": {}, "brokerage": {}}, identity_set=True
            )

        mock_save.assert_called_once()
        saved_refs = {o.reference_number for o in mock_save.call_args.args[0]}
        assert saved_refs == {"FA-OLD", "FA-NEW"}


class TestBadCsvDoesNotPoisonGoodOnes:
    def test_bad_file_reported_good_file_still_saved(self) -> None:
        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"},
            uploaded_files=[
                _FakeUploadedFile("bad.csv", "not,a,valid,ubs,export\n"),
                _FakeUploadedFile("good.csv", _order_csv("FA-GOOD")),
            ],
            button_clicks={"import_ubs_csv_btn"},
        )

        with ExitStack() as stack:
            stack.enter_context(patch.object(sync_scan_mod, "st", mock_st))
            stack.enter_context(patch.object(sync_scan_mod, "load_ubs_orders", return_value=[]))
            mock_save = stack.enter_context(patch.object(sync_scan_mod, "save_ubs_orders"))
            _patch_recompute_path(stack)
            sync_scan_mod._render_ubs_csv_uploader(
                ledger={"koinly": {}, "brokerage": {}}, identity_set=True
            )

        errors = [c.args[0] for c in mock_st.error.call_args_list]
        assert any("bad.csv" in e for e in errors), errors

        mock_save.assert_called_once()
        saved_refs = {o.reference_number for o in mock_save.call_args.args[0]}
        assert saved_refs == {"FA-GOOD"}
