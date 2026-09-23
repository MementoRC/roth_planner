"""Wire parsed UBS ACTIVITY CSV orders into a :class:`YTDSnapshot` and correct
the phantom short-term-capital-gain double-count that results from importing
a UBS brokerage statement's reported STCG alongside them.

Domain background (verified to the cent on real data)
-------------------------------------------------------
UBS reports an NQO same-day-sale using the STRIKE PRICE as cost basis, so the
statement's "YTD realized short-term capital gain" contains the ENTIRE
bargain element, not just any genuine short-term trading gain:

    statement YTD realized STCG   24,365.65
    CSV bargain element           24,396.92
    CSV total fees                    31.27
    24,396.92 - 31.27 = 24,365.65

The correct basis is FMV at exercise (== gross proceeds), so the REAL
short-term capital gain from the exercise itself is just ``-fees``. The
24,396.92 is ordinary W-2 income (:func:`apply_ubs_exercise_income`), not a
capital gain -- importing the statement figure as-is alongside it
double-counts the bargain element against itself.
:func:`correct_ubs_option_basis` subtracts it back out.

Pure functions only -- no Streamlit, no I/O. Warnings are returned as plain
strings for a caller to surface however it likes; nothing here ever raises
or blocks on a data-quality concern that is expected to occur legitimately.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from engine.pdf_ledger import PdfLedger
from engine.ubs_activity_csv import (
    UbsActivityOrder,
    federal_withholding,
    withholding_matches_expected_blend,
)
from models.ytd_income import YTDSnapshot


def apply_ubs_exercise_income(ytd: YTDSnapshot, orders: Sequence[UbsActivityOrder]) -> list[str]:
    """Land *orders*' NQO exercise income and federal withholding onto *ytd*
    IN PLACE, returning human-readable warnings (``[]`` when clean).

    The UBS ACTIVITY CSV is the SOLE source for NQO exercise income by
    project decision -- both ``nqo_exercise_ytd`` and
    ``federal_withholding_ytd`` are ASSIGNED (never accumulated with
    ``+=``), deliberately overwriting whatever another source (e.g.
    FinExtract's ``apply_option_exercises``) had already put there.

    ``federal_withholding_ytd`` receives ONLY the summed 22% federal
    supplemental-wage portion (:func:`engine.ubs_activity_csv.federal_withholding`),
    never the lots' raw ``total_taxes``. ``total_taxes`` is a ~29.65% blend
    that also includes Social Security and Medicare, and FICA does not
    reduce an IRC Section 6654 underpayment-penalty safe-harbor calculation
    -- feeding the whole blend in would overstate federal withholding by
    roughly 35% and fail in the PENALTY direction (looks safe, isn't).

    Empty *orders* leaves *ytd* completely untouched and returns ``[]``: an
    empty CSV scan means "no data available", not "zero exercises this
    year" -- zeroing the fields here would be actively wrong the moment a
    caller re-scans without a fresh CSV present.
    """
    if not orders:
        return []

    warnings: list[str] = []
    new_bargain_total = sum(order.bargain_element for order in orders)

    if ytd.wages_ytd != 0.0:
        warnings.append(
            f"wages_ytd is already {ytd.wages_ytd:.2f} (nonzero): the sole-source "
            "assumption for UBS NQO exercise income may be broken here -- a "
            "paystub's YTD gross typically already contains the NQO spread, and "
            "its withholding runs through payroll onto the W-2. Applying the UBS "
            f"CSV's ordinary income ({new_bargain_total:.2f}) on top may double-count "
            "both the ordinary income and the safe-harbor federal withholding "
            "figure -- verify wages_ytd does not already include this exercise "
            "before trusting the combined total."
        )

    old_bargain_total = ytd.nqo_exercise_ytd
    if old_bargain_total != 0.0 and abs(old_bargain_total - new_bargain_total) > 0.01:
        warnings.append(
            f"nqo_exercise_ytd was already {old_bargain_total:.2f} before this scan "
            f"(from another source, e.g. FinExtract's apply_option_exercises) and "
            f"disagrees with the UBS CSV total of {new_bargain_total:.2f} by more "
            "than $0.01 -- the UBS CSV value wins and has overwritten it."
        )

    for order in orders:
        if not withholding_matches_expected_blend(order):
            warnings.append(
                f"Order {order.reference_number}: observed withholding rate does "
                "not match the full federal+SS+Medicare blend. This is EXPECTED "
                "when the Social Security wage base was already reached elsewhere "
                "in the filer's income before this exercise, and is NOT by itself "
                "a defect."
            )

    ytd.nqo_exercise_ytd = new_bargain_total
    ytd.federal_withholding_ytd = sum(federal_withholding(order) for order in orders)

    return warnings


def correct_ubs_option_basis(
    totals: dict[str, float],
    ledger: PdfLedger,
    orders: Sequence[UbsActivityOrder],
) -> tuple[dict[str, float], list[str]]:
    """Return a NEW totals dict (never mutates *totals*) with ``stcg_ytd``
    reduced by the covered bargain element, plus warnings.

    Corrects the TOTALS DICT before ``apply_brokerage_totals`` assigns it,
    rather than adjusting the ``YTDSnapshot`` afterwards. There are two
    independent call sites that do ``derive_brokerage_totals(ledger)`` then
    ``apply_brokerage_totals(...)`` (``views/ytd_income/_partials/_sync_scan.py``
    lines ~151 and ~769), and ``apply_brokerage_totals`` does a plain
    ``ytd.stcg_ytd = totals["stcg_ytd"]`` assignment. Correcting after the
    fact would have to be duplicated at both call sites and would be
    silently undone by any future third site; correcting the input dict is
    idempotent by construction because ``totals`` is re-derived from the
    ledger on every scan.

    Date gate
    ---------
    Only an order whose ``execution_date`` is on or before the LATEST
    ``statement_period_end`` among UBS taxable statement records in the
    ledger is subtracted. Rationale: against, say, a July UBS statement and
    a September exercise, the July statement's reported STCG never
    contained the September exercise's phantom bargain element in the first
    place -- an unconditional subtraction would invent a spurious ~$24K
    short-term LOSS that isn't there. This failure mode understates income
    while LOOKING like a benefit (a smaller STCG number), which is exactly
    the shape of bug nobody notices until it's audited.

    If the ledger holds NO UBS taxable record at all, the correction is 0.0
    and every order is reported as uncovered (not silently dropped) --
    there is no statement period to gate against, so nothing can be safely
    confirmed as double-counted yet.

    UBS records are identified by the record dict's ``broker`` field,
    matched case-insensitively (observed spelling in the ledger:
    ``broker="ubs"``, lowercase, set by
    ``engine/brokerage_statement_pdf.py``'s ``parse_ubs_statement``). Only
    records with ``account_type == "taxable"`` are considered, mirroring
    the rest of the YTD pipeline (``partition_by_account_type``) -- a UBS
    record defaults to ``account_type="unknown"`` until a user confirms it
    via the account-type override UI, and an unconfirmed record must not
    silently feed this correction either.
    """
    corrected = dict(totals)
    warnings: list[str] = []

    ubs_period_ends: list[date] = []
    for owner_accounts in ledger.get("brokerage", {}).values():
        for record in owner_accounts.values():
            if str(record.get("broker", "")).lower() != "ubs":
                continue
            if record.get("account_type") != "taxable":
                continue
            period_end_raw = record.get("statement_period_end")
            if not period_end_raw:
                continue
            ubs_period_ends.append(date.fromisoformat(str(period_end_raw)))

    if not ubs_period_ends:
        for order in orders:
            warnings.append(
                f"UBS exercise order {order.reference_number} (executed "
                f"{order.execution_date.isoformat()}) not covered: no UBS taxable "
                "brokerage statement found in the ledger, so its bargain element "
                "could not be confirmed as included in any statement's reported "
                "STCG -- stcg_ytd was NOT corrected for this order."
            )
        return corrected, warnings

    latest_period_end = max(ubs_period_ends)
    covered_bargain_total = 0.0
    for order in orders:
        if order.execution_date <= latest_period_end:
            covered_bargain_total += order.bargain_element
        else:
            warnings.append(
                f"UBS exercise order {order.reference_number} (executed "
                f"{order.execution_date.isoformat()}) not covered: the latest UBS "
                f"taxable statement period end in the ledger is "
                f"{latest_period_end.isoformat()}, before this order's execution "
                "date, so its bargain element was NOT subtracted from stcg_ytd "
                "(the statement could not have reported it yet)."
            )

    corrected["stcg_ytd"] = corrected.get("stcg_ytd", 0.0) - covered_bargain_total
    return corrected, warnings
