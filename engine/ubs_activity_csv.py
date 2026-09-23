"""UBS "ACTIVITY" CSV export parser -- NQO (non-qualified stock option)
same-day-sale exercises.

UBS's brokerage/equity-plan portal offers an "ACTIVITY" CSV export alongside
the PDF statement (see engine/brokerage_statement_pdf.py's UBS section) for
one order at a time. Verified against 3 real exports (2026-09): each file
holds exactly one option-exercise order, laid out as THREE blank-line-
separated sections, each shaped as [COLUMN-HEADER row, SECTION-LABEL row,
one-or-more DATA rows]:

    ACTIVITY
    "Reference Number","Source",...,"Total Shares Delivered"   <- header FIRST
    "General Info",,,,,,,,,,,,,                                 <- label SECOND
    "FA...",...

    "Grant Number",...,"Funds Available Date (m/d/yyyy)"
    "Order Summary",,,,,,,,,,,,,
    "N...",...                                                  <- one row per grant lot

    "Exchange Event Date (m/d/yyyy)",...,"Event Message"
    "Exchange Events",,,,,
    "...",...

The header-row-before-label-row ordering is the OPPOSITE of what a reader
might expect from "General Info" / "Order Summary" / "Exchange Events"
reading like section titles -- they are not; they are the first DATA field
of a one-column-wide phantom row that UBS emits directly under its own
column headers. This module never assumes titles come first.

Column counts differ per section (order info: 13, grant lots: 12, exchange
events: 5) -- confirmed across all 3 real samples -- so nothing here assumes
a fixed row width across sections; each block is parsed using only its own
header's column count.

"Total" rows (an order-level subtotal, distinct from any individual grant
lot) appear INCONSISTENTLY: one real file has one immediately before its
"Order Summary" label, another has one folded into its "Exchange Events"
section, a third has none at all. A "Total" row must never be read as a
grant lot -- it would double-count the order's totals as a fourth (bogus)
lot. Rather than special-case its position, every row across every section
is filtered by a single first-column sentinel check (see
_SENTINEL_FIRST_COLUMNS) that also catches the two section-label rows
("General Info", "Order Summary", "Exchange Events") the same way -- all
four strings only ever appear in that first column, never as real grant-
number/reference-number data.

Only NQO same-day-sale exercises (Transaction Type "SDS") have been observed
in a real export. Any other transaction type is refused outright (see
_KNOWN_TRANSACTION_TYPES) rather than silently run through this module's
same-day-sale bargain-element math, which has not been verified to hold for
a hold-to-expiry or cashless-hold exercise.

Withholding is a BLEND, not federal income tax alone
------------------------------------------------------
Every sampled lot's "Total Taxes" column reconciles to EXACTLY 29.65% of that
lot's bargain element (gross_proceeds - grant_price*execution_quantity) --
confirmed to the cent across all sampled lots. That blend decomposes as:

    22.00% federal supplemental-wage withholding (IRS Pub 15, flat rate for
           supplemental wages <= $1M in the calendar year)
  +  6.20% Social Security (OASDI)
  +  1.45% Medicare (HI)
  = 29.65%

(No state component -- Texas has no state income tax.)

Only the FEDERAL 22.00% portion counts toward the IRC Section 6654
estimated-tax safe harbor; FICA withholding does not reduce an underpayment
penalty. :func:`federal_withholding` isolates that portion.

CRITICAL: the Social Security component STOPS once the filer's cumulative
wages for the calendar year cross the annual SS wage base. An exercise late
in the year, after that cap is already reached elsewhere in the filer's
income, can THEREFORE legitimately withhold only ~23.45% (22.00% federal +
1.45% Medicare, no SS) instead of 29.65% -- this is correct UBS behavior, not
a parsing bug. Never assume every order carries the full 29.65% blend; use
:func:`withholding_matches_expected_blend` to check, don't assume.

Reconciliation invariant
-------------------------
Every real lot sampled satisfies, exact to the cent:

    net_proceeds == gross_proceeds - strike_cost - total_fees - total_taxes

:func:`_build_order` validates this (tolerance 0.01) on every lot at parse
time and raises :class:`UbsActivityParseError` naming the lot and both sides
of the equation on failure -- silently trusting a malformed export's
net_proceeds would misstate a real cashflow.

A separate, looser quirk: reported gross_proceeds can differ from
execution_quantity * execution_price by a few cents (observed: 6,700.95
reported vs. 6,701.10 computed) -- this is normal execution-price rounding
noise, not a parse error. This module treats every REPORTED figure as the
source of truth and never recomputes or overrides one from another; there is
therefore no cross-check against execution_quantity * execution_price here.

Pyodide-safe: this module is pure-Python text processing (the `csv` module
from the standard library only) -- no pdfplumber, no Streamlit, no I/O
inside any `parse_*` function.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# --- Withholding-rate constants ---------------------------------------------
# Each rate is its own named constant (never a single hard-coded 29.65%
# blend) precisely because the blend is not a constant -- see the module
# docstring's Social Security wage-base caveat.
FEDERAL_SUPPLEMENTAL_RATE = 0.22  # IRS Pub 15 supplemental wage rate (<= $1M)
SOCIAL_SECURITY_RATE = 0.062
MEDICARE_RATE = 0.0145

_ACTIVITY_TITLE = "ACTIVITY"

# First-column values that mark a row as a SECTION LABEL or an order-level
# SUBTOTAL, never a real data row -- see module docstring's "Total" row
# paragraph. All four strings are only ever observed in this position.
_SENTINEL_FIRST_COLUMNS = frozenset({"General Info", "Order Summary", "Exchange Events", "Total"})

_ORDER_HEADER_FIRST_COLUMN = "Reference Number"
_LOT_HEADER_FIRST_COLUMN = "Grant Number"

# Transaction types this module knows how to model. "SDS" (same-day sale) is
# the only one observed across 3 real exports -- see module docstring.
_KNOWN_TRANSACTION_TYPES = frozenset({"SDS"})

_NET_PROCEEDS_TOLERANCE = 0.01


class UbsActivityParseError(Exception):
    """Raised when a UBS ACTIVITY CSV export cannot be parsed, carries an
    unmodeled transaction type, or fails its net-proceeds reconciliation
    check (see module docstring)."""


@dataclass(frozen=True)
class UbsExerciseLot:
    """One grant lot within a UBS option-exercise order's "Order Summary"
    section. An order can span several lots (observed: up to 3 in a single
    real export) when its exercised shares are drawn from more than one
    grant.
    """

    grant_number: str
    grant_date: date
    grant_price: float
    grant_quantity: int
    execution_quantity: int
    open_quantity: int
    cancel_quantity: int
    total_fees: float
    total_taxes: float
    gross_proceeds: float
    net_proceeds: float
    funds_available_date: date | None

    @property
    def strike_cost(self) -> float:
        """What exercising this lot cost at the grant's strike price."""
        return self.grant_price * self.execution_quantity

    @property
    def bargain_element(self) -> float:
        """Ordinary income from this lot's exercise: the spread between what
        the shares sold for (gross_proceeds) and what they cost to exercise
        (strike_cost). This is the NQO ordinary-income recognition event,
        not a capital gain."""
        return self.gross_proceeds - self.strike_cost


@dataclass(frozen=True)
class UbsActivityOrder:
    """One UBS option-exercise order -- the "ACTIVITY" section of one
    ACTIVITY CSV export, plus every grant lot it drew from."""

    reference_number: str
    source: str
    entry_date: date
    execution_date: date
    quantity: int
    order_type: str
    transaction_type: str
    limit_price: float | None
    execution_price: float
    entered_through: str
    proceeds_method: str
    funds_available_date: date | None
    total_shares_delivered: int
    lots: tuple[UbsExerciseLot, ...]

    @property
    def bargain_element(self) -> float:
        """Total ordinary income across every lot this order drew from."""
        return sum(lot.bargain_element for lot in self.lots)


# ---------------------------------------------------------------------------
# Field parsing helpers
# ---------------------------------------------------------------------------


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw.strip(), "%m/%d/%Y").date()


def _parse_optional_date(raw: str) -> date | None:
    stripped = raw.strip()
    if not stripped:
        return None
    return _parse_date(stripped)


def _parse_money(raw: str) -> float:
    """Strip quoted thousands-comma formatting (csv already removed the
    quotes; only the comma remains) and convert to float."""
    return float(raw.strip().replace(",", ""))


def _parse_optional_money(raw: str) -> float | None:
    """UBS renders a market order's Limit Price as the literal string "N/A"
    rather than leaving it blank -- both are treated as "no limit price"."""
    stripped = raw.strip()
    if not stripped or stripped.upper() == "N/A":
        return None
    return _parse_money(stripped)


# ---------------------------------------------------------------------------
# Row/section splitting
# ---------------------------------------------------------------------------


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def _split_blocks(rows: list[list[str]]) -> list[list[list[str]]]:
    """Group *rows* into blank-line-separated blocks, dropping the leading
    bare "ACTIVITY" title row (which precedes the first section's header with
    no blank line of its own -- see module docstring's layout diagram).

    ``csv.reader`` yields ``[]`` for a genuinely blank line -- that is the
    section separator this function groups on.
    """
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for row in rows:
        if not row:
            if current:
                blocks.append(current)
                current = []
            continue
        if row == [_ACTIVITY_TITLE]:
            continue
        current.append(row)
    if current:
        blocks.append(current)
    return blocks


def _data_rows(block: list[list[str]]) -> list[list[str]]:
    """A block's rows after its header (block[0]), excluding every
    section-label / "Total" subtotal row (see _SENTINEL_FIRST_COLUMNS)."""
    return [row for row in block[1:] if row and row[0] not in _SENTINEL_FIRST_COLUMNS]


# ---------------------------------------------------------------------------
# Row -> dataclass builders
# ---------------------------------------------------------------------------


def _build_lot(row: list[str]) -> UbsExerciseLot:
    return UbsExerciseLot(
        grant_number=row[0].strip(),
        grant_date=_parse_date(row[1]),
        grant_price=_parse_money(row[2]),
        grant_quantity=int(row[3]),
        execution_quantity=int(row[4]),
        open_quantity=int(row[5]),
        cancel_quantity=int(row[6]),
        total_fees=_parse_money(row[7]),
        total_taxes=_parse_money(row[8]),
        gross_proceeds=_parse_money(row[9]),
        net_proceeds=_parse_money(row[10]),
        funds_available_date=_parse_optional_date(row[11]),
    )


def _validate_lots(order: UbsActivityOrder) -> None:
    """Enforce the reconciliation invariant documented in the module
    docstring, per lot, with a 1-cent tolerance for float rounding."""
    for lot in order.lots:
        expected_net = lot.gross_proceeds - lot.strike_cost - lot.total_fees - lot.total_taxes
        if abs(expected_net - lot.net_proceeds) > _NET_PROCEEDS_TOLERANCE:
            raise UbsActivityParseError(
                f"Net proceeds reconciliation failed for grant {lot.grant_number!r} "
                f"(order {order.reference_number!r}): reported net_proceeds="
                f"{lot.net_proceeds:.2f}, but gross_proceeds({lot.gross_proceeds:.2f}) - "
                f"strike_cost({lot.strike_cost:.2f}) - total_fees({lot.total_fees:.2f}) - "
                f"total_taxes({lot.total_taxes:.2f}) = {expected_net:.2f}."
            )


def _build_order(order_row: list[str], lot_rows: list[list[str]]) -> UbsActivityOrder:
    transaction_type = order_row[6].strip()
    if transaction_type not in _KNOWN_TRANSACTION_TYPES:
        raise UbsActivityParseError(
            f"Unrecognized transaction type {transaction_type!r} for order "
            f"{order_row[0]!r} -- only {sorted(_KNOWN_TRANSACTION_TYPES)} are modeled "
            "by this parser. Do not extend _KNOWN_TRANSACTION_TYPES without first "
            "verifying this module's same-day-sale bargain-element math against a "
            "real export of the new type."
        )

    order = UbsActivityOrder(
        reference_number=order_row[0].strip(),
        source=order_row[1].strip(),
        entry_date=_parse_date(order_row[2]),
        execution_date=_parse_date(order_row[3]),
        quantity=int(order_row[4]),
        order_type=order_row[5].strip(),
        transaction_type=transaction_type,
        limit_price=_parse_optional_money(order_row[7]),
        execution_price=_parse_money(order_row[8]),
        entered_through=order_row[9].strip(),
        proceeds_method=order_row[10].strip(),
        funds_available_date=_parse_optional_date(order_row[11]),
        total_shares_delivered=int(order_row[12]),
        lots=tuple(_build_lot(row) for row in lot_rows),
    )
    _validate_lots(order)
    return order


# ---------------------------------------------------------------------------
# Public parse entry points
# ---------------------------------------------------------------------------


def parse_ubs_activity_text(text: str) -> UbsActivityOrder:
    """Parse one UBS ACTIVITY CSV export's full text into a single
    :class:`UbsActivityOrder`. Pure -- no I/O.

    Locates the order-info section by its header's first column
    ("Reference Number") and the grant-lots section by its header's first
    column ("Grant Number"), rather than assuming a fixed section order --
    an "Exchange Events" section (or any future section this module does not
    model) is silently skipped, never treated as a parse failure.
    """
    try:
        blocks = _split_blocks(_rows(text))
    except csv.Error as exc:
        raise UbsActivityParseError(f"Not valid CSV: {exc}") from exc

    order_block: list[list[str]] | None = None
    lot_block: list[list[str]] | None = None
    for block in blocks:
        header = block[0]
        if not header:
            continue
        if header[0] == _ORDER_HEADER_FIRST_COLUMN:
            order_block = block
        elif header[0] == _LOT_HEADER_FIRST_COLUMN:
            lot_block = block
        # Any other section (e.g. Exchange Events) carries no fields this
        # module's dataclasses model -- intentionally not read further.

    if order_block is None:
        raise UbsActivityParseError(
            "No order-info section found (expected a header row starting with "
            f"{_ORDER_HEADER_FIRST_COLUMN!r})."
        )
    if lot_block is None:
        raise UbsActivityParseError(
            "No Order Summary (grant lots) section found (expected a header row "
            f"starting with {_LOT_HEADER_FIRST_COLUMN!r})."
        )

    order_rows = _data_rows(order_block)
    if len(order_rows) != 1:
        raise UbsActivityParseError(
            f"Expected exactly one order-info data row, found {len(order_rows)}."
        )
    lot_rows = _data_rows(lot_block)
    if not lot_rows:
        raise UbsActivityParseError("Order Summary section has no grant-lot data rows.")

    try:
        return _build_order(order_rows[0], lot_rows)
    except (ValueError, IndexError) as exc:
        raise UbsActivityParseError(f"Malformed data row in UBS ACTIVITY export: {exc}") from exc


def load_ubs_activity_csv(path: Path) -> UbsActivityOrder:
    """Read and parse one UBS ACTIVITY CSV export from disk."""
    return parse_ubs_activity_text(path.read_text())


@dataclass(frozen=True)
class UbsActivityScanResult:
    """Outcome of scanning a folder of UBS ACTIVITY CSV exports.

    Mirrors engine/pdf_import.py::PdfImportResult: one bad file must not kill
    the batch, but it must never vanish either. Every per-file failure is
    reported in :attr:`errors` as ``(filename, reason)`` so a caller can say
    "3 of 4 files imported, 1 failed: ..." instead of quietly importing less
    than the user put in the folder.
    """

    orders: tuple[UbsActivityOrder, ...] = ()
    errors: tuple[tuple[str, str], ...] = ()


def load_ubs_activity_folder(folder: Path) -> UbsActivityScanResult:
    """Parse every ``*.csv`` file in *folder* (sorted) into a
    :class:`UbsActivityScanResult`.

    Browser downloads of repeated exports land as ``name.csv``,
    ``name (1).csv``, ``name (2).csv`` -- these are DISTINCT ORDERS, not
    duplicates of one export, and are never deduped by filename. Dedup
    happens only on ``reference_number``: if two files genuinely describe the
    same order, the first one encountered in sorted-filename order wins.

    A single unparseable file does not abort the scan, but it is never
    silently dropped either -- it is reported in the result's ``errors``.
    Silently skipping would make a foreign file dropped in the folder and a
    real defect in this module's own row parsing indistinguishable, each
    presenting as "fewer orders than expected" with no way to tell which.

    Only two failures are treated as per-file and recoverable: ``OSError``
    (the file could not be read at all) and :class:`UbsActivityParseError`
    (content this module recognizes as something it refuses to model). Any
    OTHER exception is a defect in this module and deliberately propagates
    rather than being logged as if the user's file were at fault.
    """
    orders: dict[str, UbsActivityOrder] = {}
    errors: list[tuple[str, str]] = []
    for csv_path in sorted(folder.glob("*.csv")):
        try:
            order = load_ubs_activity_csv(csv_path)
        except UbsActivityParseError as exc:
            errors.append((csv_path.name, str(exc)))
            continue
        except OSError as exc:
            errors.append((csv_path.name, f"could not read file: {exc}"))
            continue
        orders.setdefault(order.reference_number, order)
    return UbsActivityScanResult(orders=tuple(orders.values()), errors=tuple(errors))


# ---------------------------------------------------------------------------
# Withholding decomposition -- see module docstring
# ---------------------------------------------------------------------------


def federal_withholding(
    order: UbsActivityOrder,
    *,
    supplemental_rate: float = FEDERAL_SUPPLEMENTAL_RATE,
) -> float:
    """Return ONLY the federal-income-tax portion of *order*'s withholding:
    ``bargain_element * supplemental_rate``. This is the figure that counts
    toward the IRC Section 6654 estimated-tax safe harbor -- FICA does not.

    This function deliberately takes NO ``ss_rate``/``medicare_rate``. FICA is
    excluded from a federal-safe-harbor figure by definition, so such a
    parameter could never change the result -- and accepting one only invites
    a caller to believe it had. The concrete trap: passing ``ss_rate=0.0`` to
    model a late-in-year exercise whose Social Security component was already
    capped out (see the module docstring's wage-base caveat) would silently
    return exactly the same number, with nothing to signal that the argument
    was ignored. :data:`SOCIAL_SECURITY_RATE` and :data:`MEDICARE_RATE` remain
    module constants for :func:`withholding_matches_expected_blend`, which
    genuinely uses all three.
    """
    return order.bargain_element * supplemental_rate


def observed_withholding_rate(order: UbsActivityOrder) -> float:
    """Return the BLENDED withholding rate UBS actually applied to *order*:
    total_taxes summed across every lot, divided by the order's total bargain
    element. Compare against ``FEDERAL_SUPPLEMENTAL_RATE + SOCIAL_SECURITY_RATE
    + MEDICARE_RATE`` (or use :func:`withholding_matches_expected_blend`) --
    never assume it equals that sum outright; see the module docstring's
    Social Security wage-base caveat for why it can legitimately differ.
    """
    total_taxes = sum(lot.total_taxes for lot in order.lots)
    return total_taxes / order.bargain_element


def withholding_matches_expected_blend(order: UbsActivityOrder, tolerance: float = 0.005) -> bool:
    """True if *order*'s observed withholding rate matches the full
    federal+SS+Medicare blend within *tolerance* (default 0.5 percentage
    points). False when the Social Security component was capped mid-order
    (see module docstring) or the export is otherwise anomalous -- callers
    should not treat False as necessarily a parse defect.
    """
    expected_blend = FEDERAL_SUPPLEMENTAL_RATE + SOCIAL_SECURITY_RATE + MEDICARE_RATE
    return abs(observed_withholding_rate(order) - expected_blend) <= tolerance
