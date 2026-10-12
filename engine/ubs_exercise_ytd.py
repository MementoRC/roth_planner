"""Wire parsed UBS ACTIVITY CSV orders into a :class:`YTDSnapshot` and correct
the phantom short-term-capital-gain double-count that results from importing
a UBS brokerage statement's reported STCG alongside them.

Domain background (reconciles to the cent)
------------------------------------------
UBS reports an NQO same-day-sale using the STRIKE PRICE as cost basis, so the
statement's "YTD realized short-term capital gain" contains the ENTIRE
bargain element, not just any genuine short-term trading gain. Illustrative
(synthetic) figures:

    statement YTD realized STCG   21,500.00
    CSV bargain element           21,537.50
    CSV total fees                    37.50
    21,537.50 - 37.50 = 21,500.00

The correct basis is FMV at exercise (== gross proceeds), so the TRUE
short-term capital gain from the exercise itself is just ``-fees``. The
bargain element is ordinary W-2 income (:func:`apply_ubs_exercise_income`), not a
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
from models.ytd_income import YTDSnapshot, manual_key


def apply_ubs_exercise_income(
    ytd: YTDSnapshot, orders: Sequence[UbsActivityOrder], *, owner: str = "you"
) -> list[str]:
    """Land *orders*' NQO exercise income and federal withholding onto *ytd*
    IN PLACE, returning human-readable warnings (``[]`` when clean).

    Issue #496: on an attributed snapshot both fields are written to *owner*'s
    slot only; on an unattributed one they are the household totals, as before.

    The UBS ACTIVITY CSV is the SOLE source for NQO exercise income by
    project decision -- ``nqo_exercise_ytd`` is ASSIGNED (never accumulated
    with ``+=``), deliberately overwriting whatever another source (e.g.
    FinExtract's ``apply_option_exercises``) had already put there. Federal
    withholding (redesign PR 1b) is the owner's UBS *component*
    (``YTDSnapshot.set_ubs_withholding``), ADDED to their base withholding
    (paycheck, hand entry, other imports) and replaced on each scan, so the
    same CSV twice never doubles it.

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

    Only orders EXECUTED in ``ytd.tax_year`` count (audit-1008 F-27): the
    order store is append-only across years, and a December exercise is that
    year's W-2 income and withholding, not next year's. Skipped orders are
    named in a warning. When every stored order is from another year, *ytd*
    is left alone except for UBS-owned figures an earlier, ungated apply
    left behind (the owner's UBS withholding component, and an NQO figure
    equal to those orders' bargain total), which are reset to zero.
    """
    if not orders:
        return []

    warnings: list[str] = []
    in_year = [order for order in orders if order.execution_date.year == ytd.tax_year]
    other_year = [order for order in orders if order.execution_date.year != ytd.tax_year]
    if other_year:
        warnings.append(
            f"{len(other_year)} UBS exercise order(s) executed outside {ytd.tax_year} "
            f"were not counted as {ytd.tax_year} income or withholding: "
            + ", ".join(
                f"{o.reference_number} ({o.execution_date.isoformat()})" for o in other_year
            )
            + "."
        )
    if not in_year:
        _clear_stale_ubs_figures(ytd, other_year, owner=owner)
        return warnings
    orders = in_year
    new_bargain_total = sum(order.bargain_element for order in orders)

    if ytd.wages_ytd != 0.0:
        warnings.append(
            f"wages_ytd is already {ytd.wages_ytd:.2f} (nonzero): the sole-source "
            "assumption for UBS NQO exercise income may be broken here -- a "
            "paystub's YTD gross typically already contains the NQO spread, and "
            "its withholding runs through payroll onto the W-2. Applying the UBS "
            f"CSV's ordinary income ({new_bargain_total:.2f}) on top may double-count "
            "the ordinary income -- verify wages_ytd does not already include this "
            "exercise before trusting the combined total. UBS withholding is now "
            "ADDED to your other withholding: verify your paycheck withholding does "
            "not already include this exercise's withholding."
        )

    old_bargain_total = (
        ytd.by_owner.get(owner, {}).get("nqo_exercise_ytd", 0.0)
        if ytd.by_owner
        else ytd.nqo_exercise_ytd
    )
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

    # Redesign PR 1b: withholding is a separate component ADDED to the owner's
    # base (paycheck/hand entry/other imports) and REPLACED on every scan, so
    # the same CSV twice never doubles. Set it first: it attributes a legacy
    # snapshot, so the NQO write below lands in the owner's slot.
    ytd.set_ubs_withholding(owner, sum(federal_withholding(order) for order in orders))
    # nqo_exercise_ytd keeps REPLACE semantics: it is purely exercise-derived
    # (UBS CSV, or FinExtract's apply_option_exercises for the same quantity).
    ytd.set_owned(owner, nqo_exercise_ytd=new_bargain_total)

    return warnings


def _clear_stale_ubs_figures(
    ytd: YTDSnapshot, other_year: Sequence[UbsActivityOrder], *, owner: str
) -> None:
    """Reset UBS-owned figures that only other-year orders could have produced.

    Before the tax-year gate, an apply summed every stored order, so a
    snapshot can still carry a prior year's exercise. The UBS withholding
    component is UBS-owned, so it is zeroed outright. nqo_exercise_ytd is
    shared with FinExtract's exercise sync, so it is zeroed only when it
    equals those orders' bargain total (i.e. UBS put it there).
    """
    if ytd.ubs_withholding_by_owner.get(owner):
        ytd.set_ubs_withholding(owner, 0.0)
    stale_total = sum(order.bargain_element for order in other_year)
    current = (
        ytd.by_owner.get(owner, {}).get("nqo_exercise_ytd", 0.0)
        if ytd.by_owner
        else ytd.nqo_exercise_ytd
    )
    if current and abs(current - stale_total) <= 0.01:
        ytd.set_owned(owner, nqo_exercise_ytd=0.0)


# Pre-1b values can sit up to $1 from the order sum: the old form truncated
# withholding to whole dollars (int) and the old overlay stored
# int(total) - others (redesign PR 1b plan, decision 10).
_LEGACY_MATCH = 1.0


def split_legacy_ubs_withholding(
    ytd: YTDSnapshot,
    applied_orders: Sequence[UbsActivityOrder],
    stored_orders: Sequence[UbsActivityOrder],
    *,
    owner: str,
) -> list[str]:
    """One-time migration of pre-1b data before the first additive apply. IN PLACE.

    Before redesign PR 1b a UBS apply ASSIGNED the owner's withholding to the
    sum of federal_withholding(order) over the orders it applied. When the
    owner has no UBS component yet, their base is within $1 of that sum
    (over the orders LAST applied, or over all stored orders), and the base
    is not a post-1b hand entry, the base IS UBS withholding: move it into
    the component (total unchanged). Otherwise the base is kept and, if
    non-zero, a warning says UBS is now added on top of it. Returns warnings.
    """
    if owner in ytd.ubs_withholding_by_owner or not (applied_orders or stored_orders):
        return []
    base = ytd.owner_value(owner, "federal_withholding_ytd")
    marked = manual_key("federal_withholding_ytd", owner) in ytd.manual_marks
    for orders in (applied_orders, stored_orders):
        legacy = sum(federal_withholding(order) for order in orders)
        if orders and not marked and abs(base - legacy) < _LEGACY_MATCH:
            ytd.set_ubs_withholding(owner, legacy)
            ytd.set_owned(owner, federal_withholding_ytd=0.0)
            return []
    if base > 0.0:
        return [
            f"UBS option-exercise withholding is now ADDED to your federal withholding of "
            f"{base:,.2f}. If {base:,.2f} already included it (e.g. a paystub total you "
            "typed), lower it in manual entry."
        ]
    return []


def correct_ubs_option_basis(
    totals: dict[str, float],
    ledger: PdfLedger,
    orders: Sequence[UbsActivityOrder],
    *,
    tax_year: int | None = None,
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

    Tax year (audit-1008 F-27)
    --------------------------
    With *tax_year* (pass the YTD snapshot's), only that year's UBS
    statements and orders are considered: *totals* then hold only that
    year's statements (derive_brokerage_totals with the same tax_year), and
    a statement's YTD STCG can only contain exercises from its own year.
    Orders from other years are skipped silently here --
    apply_ubs_exercise_income already names them.
    """
    corrected = dict(totals)
    warnings: list[str] = []
    if tax_year is not None:
        orders = [order for order in orders if order.execution_date.year == tax_year]

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
            period_end = date.fromisoformat(str(period_end_raw))
            if tax_year is not None and period_end.year != tax_year:
                continue
            ubs_period_ends.append(period_end)

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
