"""Fidelity NetBenefits workplace-plan statement parser (403(b) balance source).

NetBenefits "Statement Details" is a web page; what lands in the PDF folder is
a browser print of it, not a mailed statement. It reports the plan BALANCE --
ending and vested balance, the balance by money source, and the fund holdings
-- for one participant in one plan.

It is a balance source only. Nothing here may ever feed a YTD income field:
the "Dividends & Interest" it reports were earned inside the plan and are
tax-deferred, so counting them as taxable dividends would overstate income.
The parser therefore does not even extract them.

The bare "Fidelity" broker match in engine/brokerage_statement_pdf.py would
claim this document and then fail to find any brokerage account section in
it, so engine/pdf_import.py checks :func:`is_netbenefits_statement` before
that match runs.

Pure: works on already-extracted page strings, no pdfplumber, no Streamlit.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from engine.pdf_owner import resolve_owner
from engine.portfolio_sync.as_of import account_key, is_newer_or_equal
from engine.portfolio_sync.shapes import AccountSummary, Holding, PortfolioSnapshot
from models.ytd_income import iso_day


class NetBenefitsParseError(ValueError):
    """A NetBenefits statement was recognized but a required field is missing."""


@dataclass
class NetBenefitsHolding:
    """One fund position at the statement period end."""

    description: str
    shares: float
    price: float
    value: float
    asset_class: str  # "equity", "bond", "cash", "target_date"


@dataclass
class NetBenefitsStatement:
    """One participant's plan balance, as of the statement period end."""

    plan_name: str
    owner_key: str | None  # printed holder name; None when not found (never guessed)
    period_start: str  # ISO date
    period_end: str  # ISO date -- the as-of date of every balance below
    ending_balance: float
    vested_balance: float
    source_balances: dict[str, float] = field(default_factory=dict)
    holdings: list[NetBenefitsHolding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# Both markers are required: "NetBenefits" alone can appear in an ordinary
# Fidelity brokerage statement's marketing text.
_NETBENEFITS_RE = re.compile(r"NetBenefits")
_RETIREMENT_STATEMENT_RE = re.compile(r"Retirement Savings Statement")


def is_netbenefits_statement(text: str) -> bool:
    """True when *text* is a NetBenefits retirement savings statement."""
    return bool(_NETBENEFITS_RE.search(text) and _RETIREMENT_STATEMENT_RE.search(text))


_MONEY = r"-?\$[\d,]+\.\d{2}"
_PERIOD_RE = re.compile(
    r"Statement Period:\s*(\d{2})/(\d{2})/(\d{4})\s+to\s+(\d{2})/(\d{2})/(\d{4})"
)
_ENDING_RE = re.compile(rf"^Ending Balance ({_MONEY})$", re.MULTILINE)
_VESTED_RE = re.compile(rf"^Vested Balance ({_MONEY})$", re.MULTILINE)
_PLAN_RE = re.compile(r"^(.+)\nRetirement Savings Statement$", re.MULTILINE)
_HOLDER_RE = re.compile(r"^Retirement Plan\n(.+?)\s+Customer Service:", re.MULTILINE)

_FUND_ROW_RE = re.compile(
    r"^(?P<prefix>.*?)\s*"
    r"(?P<sh_begin>[\d,]+\.\d+)\s+(?P<sh_end>[\d,]+\.\d+)\s+"
    r"\$(?P<px_begin>[\d,]+\.\d+)\s+\$(?P<px_end>[\d,]+\.\d+)\s+"
    r"\$(?P<val_begin>[\d,]+\.\d+)\s+\$(?P<val_end>[\d,]+\.\d+)$"
)
_SOURCE_ROW_RE = re.compile(
    rf"^(?P<prefix>.*?)\s*(?P<contrib>{_MONEY})\s+\d+%\s+(?P<balance>{_MONEY})\s+(?P<vested>{_MONEY})$"
)

# Lines inside the holdings/contribution tables that are neither a row nor
# part of a fund/source name: page chrome, column headers, category labels and
# category subtotals. A missed label here only pollutes a display name; values
# come from the row regexes alone.
_NOISE_RE = re.compile(
    r"^(?:"
    r"\d{1,2}/\d{1,2}/\d{2}, .*NetBenefits.*"  # browser print header
    r"|https?://\S+.*"  # browser print footer
    r"|Shares/Units.*|Price as of.*|Investment as of.*|[\d/ ]+"  # column headers
    r"|Total Account Total Vested|Contributions Period to date.*|Balance Balance"
    r"|Statement Period:.*"
    r"|[A-Za-z/ -]+ " + _MONEY + r"(?: " + _MONEY + r")?"  # category subtotal
    r"|Stock|Stocks|International|Domestic|Small Cap|Mid Cap|Large Cap|Bond|Bonds"
    r"|Stable Value|Short-Term|Specialty|Blended Investments|Money Market"
    r")$"
)
_CORE_FUNDS = "CORE FUNDS"
_TARGET_DATE_FUNDS = "TARGET DATE FUNDS"
_HOLDINGS_END = "Account Totals"
_SOURCES_START = "Your Contribution Summary"
_SOURCES_END = "Your Account Activity"

# Bare "income" is NOT a bond word: Equity-Income and Growth & Income funds
# are stock funds (audit-1008 F-09). "Fixed income" is still caught by "fixed".
_BOND_WORDS = re.compile(r"\b(?:bond|bd|stable|fixed|treasury|tips)\b", re.IGNORECASE)
_CASH_WORDS = re.compile(r"\b(?:money market|mm|cash|government portfolio)\b", re.IGNORECASE)
# Target-date "income" vintages (e.g. Freedom Income, Target Retirement Income)
# sometimes sit outside the TARGET DATE FUNDS section.
_TARGET_DATE_WORDS = re.compile(r"\b(?:freedom|retirement) income\b", re.IGNORECASE)

# The statement's own category labels (printed as a label line or a category
# subtotal line above the funds they group). Sub-labels of the "Stock" block
# (International, Small Cap, ...) also mean equity. "Blended Investments" and
# "Specialty" say nothing about the class, so they clear the category and the
# fund name decides.
_CATEGORY_CLASSES: dict[str, str | None] = {
    "stock": "equity",
    "stocks": "equity",
    "international": "equity",
    "domestic": "equity",
    "small cap": "equity",
    "mid cap": "equity",
    "large cap": "equity",
    "bond": "bond",
    "bonds": "bond",
    "stable value": "bond",
    "short-term": "cash",
    "money market": "cash",
    "blended investments": None,
    "specialty": None,
}
_CATEGORY_LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z/ -]+?)(?: " + _MONEY + r"(?: " + _MONEY + r")?)?$"
)

# Holdings may differ from the ending balance by rounding; anything larger
# means a row was missed or misread.
_RECONCILE_TOLERANCE = 1.00


def _money(raw: str) -> float:
    return float(raw.replace("$", "").replace(",", ""))


def _number(raw: str) -> float:
    return float(raw.replace(",", ""))


def _first(pattern: re.Pattern[str], text: str, what: str) -> re.Match[str]:
    match = pattern.search(text)
    if match is None:
        raise NetBenefitsParseError(f"NetBenefits statement: could not find {what}.")
    return match


def _rejoin_wrapped_rows(
    lines: Iterable[str], row_re: re.Pattern[str]
) -> list[tuple[str, re.Match[str]]]:
    """Pair each table row with its full label, rejoining wrapped labels.

    NetBenefits wraps a long label around its numeric row: part of the label
    can sit on the line above the row, on the row itself, and on the line
    below. The rule that fits every sampled table: the FIRST label line after
    a row continues that row's label; any further label lines before the next
    row start the next row's label. A noise line (a category label, page
    chrome) ends the continuation, since a category label opens a new block.
    """
    rows: list[tuple[list[str], re.Match[str]]] = []
    pending: list[str] = []
    trailing_slot_open = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if _NOISE_RE.match(line):
            trailing_slot_open = False
            continue
        match = row_re.match(line)
        if match:
            parts = [*pending, match.group("prefix")]
            rows.append(([p for p in parts if p], match))
            pending = []
            trailing_slot_open = True
        elif trailing_slot_open and rows:
            rows[-1][0].append(line)
            trailing_slot_open = False
        else:
            pending.append(line)
    return [(" ".join(parts), match) for parts, match in rows]


def _section(text: str, start: str, end: str) -> list[str]:
    """Lines strictly between the first *start* line and the next *end* line."""
    lines = text.splitlines()
    try:
        first = next(i for i, ln in enumerate(lines) if ln.strip().startswith(start))
    except StopIteration:
        return []
    out: list[str] = []
    for ln in lines[first + 1 :]:
        if ln.strip().startswith(end):
            break
        out.append(ln)
    return out


def _classify_fund(
    description: str, in_target_date_section: bool, category: str | None = None
) -> str:
    """Asset class of one fund: target-date section, then the statement's own
    category label, then the fund name."""
    if in_target_date_section:
        return "target_date"
    if category is not None:
        return category
    if _CASH_WORDS.search(description):
        return "cash"
    if _TARGET_DATE_WORDS.search(description):
        return "target_date"
    if _BOND_WORDS.search(description):
        return "bond"
    return "equity"


def _category_of(line: str) -> tuple[bool, str | None]:
    """``(is_category_line, asset_class)`` for a statement category label or
    category subtotal line such as ``Stock $3,700.00 $4,000.00``."""
    m = _CATEGORY_LINE_RE.match(line.strip())
    if m is None:
        return False, None
    label = m.group("label").strip().lower()
    if label not in _CATEGORY_CLASSES:
        return False, None
    return True, _CATEGORY_CLASSES[label]


def _parse_holdings(text: str) -> list[NetBenefitsHolding]:
    holdings: list[NetBenefitsHolding] = []
    in_target_date = False
    category: str | None = None
    block: list[str] = []

    def flush() -> list[str]:
        """Emit the block's rows; return the label lines left pending for the
        next row (``_rejoin_wrapped_rows`` would have carried them over)."""
        for description, m in _rejoin_wrapped_rows(block, _FUND_ROW_RE):
            holdings.append(
                NetBenefitsHolding(
                    description=description,
                    shares=_number(m.group("sh_end")),
                    price=_number(m.group("px_end")),
                    value=_number(m.group("val_end")),
                    asset_class=_classify_fund(description, in_target_date, category),
                )
            )
        lines = [ln.strip() for ln in block if ln.strip()]
        block.clear()
        last_row = max((i for i, ln in enumerate(lines) if _FUND_ROW_RE.match(ln)), default=-1)
        tail = lines[last_row + 1 :]
        if last_row >= 0 and tail and not _NOISE_RE.match(tail[0]):
            tail = tail[1:]  # the first label line after a row continues that row
        return [ln for ln in tail if not _NOISE_RE.match(ln)]

    for ln in _section(text, _CORE_FUNDS, _HOLDINGS_END):
        if ln.strip() == _TARGET_DATE_FUNDS:
            flush()
            in_target_date = True
            continue
        is_category, cls = _category_of(ln)
        if is_category:
            # A category line is noise to the row rejoiner, so splitting the
            # block here (carrying any pending label lines over) changes no
            # row label -- it only scopes the class.
            block.extend(flush())
            # Sub-labels inside the Stock block (International, Small Cap, ...)
            # keep it equity; a top-level label sets or clears the class.
            category = cls
            continue
        block.append(ln)
    flush()
    return holdings


def _parse_source_balances(text: str) -> dict[str, float]:
    return {
        label: _money(m.group("balance"))
        for label, m in _rejoin_wrapped_rows(
            _section(text, _SOURCES_START, _SOURCES_END), _SOURCE_ROW_RE
        )
    }


def parse_netbenefits_text(pages: Sequence[str]) -> NetBenefitsStatement:
    """Parse a NetBenefits statement from its per-page text.

    Raises :class:`NetBenefitsParseError` when the statement period or the
    ending balance is missing -- without those there is no balance to import
    and no date to order it by. Everything else is best-effort: a missing
    holder name is ``None`` (the importer asks), and holdings that do not add
    up to the ending balance are dropped with a warning rather than imported
    wrong.
    """
    text = "\n".join(pages)

    period = _first(_PERIOD_RE, text, "the Statement Period")
    m1, d1, y1, m2, d2, y2 = period.groups()
    ending = _money(_first(_ENDING_RE, text, "the Ending Balance").group(1))
    vested_match = _VESTED_RE.search(text)
    plan_match = _PLAN_RE.search(text)
    holder_match = _HOLDER_RE.search(text)

    warnings: list[str] = []
    holdings = _parse_holdings(text)
    held = sum(h.value for h in holdings)
    if holdings and abs(held - ending) > _RECONCILE_TOLERANCE:
        warnings.append(
            f"Fund holdings (${held:,.2f}) do not add up to the ending balance "
            f"(${ending:,.2f}); holdings were not imported."
        )
        holdings = []

    return NetBenefitsStatement(
        plan_name=plan_match.group(1).strip() if plan_match else "NetBenefits plan",
        owner_key=holder_match.group(1).strip() if holder_match else None,
        period_start=f"{y1}-{m1}-{d1}",
        period_end=f"{y2}-{m2}-{d2}",
        ending_balance=ending,
        vested_balance=_money(vested_match.group(1)) if vested_match else ending,
        source_balances=_parse_source_balances(text),
        holdings=holdings,
        warnings=warnings,
    )


def pick_latest_netbenefits(
    statements: Iterable[NetBenefitsStatement],
) -> list[NetBenefitsStatement]:
    """Keep the newest statement (by period end) per plan and holder."""
    latest: dict[tuple[str, str | None], NetBenefitsStatement] = {}
    for stmt in statements:
        key = (stmt.plan_name, stmt.owner_key)
        if key not in latest or stmt.period_end > latest[key].period_end:
            latest[key] = stmt
    return list(latest.values())


def to_account_summary(stmt: NetBenefitsStatement, owner: str) -> AccountSummary:
    """Build the 403(b) ``AccountSummary`` this statement describes for *owner*.

    When the holdings were dropped (they did not reconcile), the whole balance
    is carried as ``target_date`` -- the blended class -- so the account still
    contributes a neutral growth rate instead of none.
    """
    if owner not in ("you", "spouse"):
        raise ValueError(f"403(b) owner must be 'you' or 'spouse', got {owner!r}")
    acct = AccountSummary(
        account_type="403b",
        owner=owner,
        account_name=stmt.plan_name,
        total_value=stmt.ending_balance,
        as_of=iso_day(stmt.period_end) or "",
        as_of_source="statement",
    )
    for h in stmt.holdings:
        acct.holdings.append(
            Holding(
                symbol=h.description,
                description=h.description,
                quantity=h.shares,
                market_value=h.value,
                account_name=stmt.plan_name,
                asset_class=h.asset_class,
            )
        )
        attr = f"{h.asset_class}_value"
        setattr(acct, attr, getattr(acct, attr) + h.value)
    if not stmt.holdings:
        acct.target_date_value = stmt.ending_balance
    return acct


def replace_owner_403b(
    snap: PortfolioSnapshot, account: AccountSummary
) -> tuple[PortfolioSnapshot, list[AccountSummary]]:
    """Return a copy of *snap* whose 403(b) for ``account.owner`` is *account*.

    Every existing 403(b) account of that owner is removed and returned so the
    caller can show what was replaced. Other owners' accounts and every other
    account type (the Rollover IRA, Roth, brokerage) are untouched. *snap*
    itself is not modified.
    """
    replaced = [a for a in snap.accounts if a.account_type == "403b" and a.owner == account.owner]
    kept = [a for a in snap.accounts if not (a.account_type == "403b" and a.owner == account.owner)]
    return dataclasses.replace(snap, accounts=[*kept, account]), replaced


def default_netbenefits_owner(
    stmt: NetBenefitsStatement, owner_map: dict[str, str], instance_owner: str
) -> str:
    """Pre-select whose 403(b) this is: the learned holder-name mapping first,
    then this planner instance's owner, then "you".

    A 403(b) belongs to one person, so a "household" answer from either
    source is not usable and falls through.
    """
    named = resolve_owner(stmt.owner_key, owner_map)
    for candidate in (named, instance_owner):
        if candidate in ("you", "spouse"):
            return candidate
    return "you"


@dataclass(frozen=True)
class NetBenefitsApplyOutcome:
    """What applying one statement to the portfolio snapshot produces."""

    snapshot: PortfolioSnapshot
    replaced: list[AccountSummary]
    field_key: str  # "your_ira" or "spouse_ira"
    ira_total: float  # that owner's new pre-tax total (Rollover IRA + 403(b) ...)
    detail: str  # provenance detail for the balance candidate
    # The cached 403(b) that is newer than the statement; set (and *snapshot*
    # left unchanged) when the apply was refused. ``new_account`` is what the
    # statement would have written, for a "Use anyway" force-apply.
    kept_newer: AccountSummary | None = None
    new_account: AccountSummary | None = None


def apply_netbenefits_statement(
    snap: PortfolioSnapshot, stmt: NetBenefitsStatement, owner: str, *, force: bool = False
) -> NetBenefitsApplyOutcome:
    """Replace *owner*'s 403(b) in *snap* with *stmt* and total their pre-tax
    balance -- the value the ``your_ira``/``spouse_ira`` candidate carries.

    Newer as-of wins: when the cached 403(b) for *owner* is dated after the
    statement's period end, nothing is replaced and ``kept_newer`` is set,
    unless ``force`` (the user's "Use anyway").
    """
    new_acct = to_account_summary(stmt, owner)
    detail = f"NetBenefits 403(b) PDF — {stmt.plan_name}, as of {stmt.period_end}"
    field_key = "your_ira" if owner == "you" else "spouse_ira"
    cached = [a for a in snap.accounts if account_key(a) == account_key(new_acct)]
    newest = max(cached, key=lambda a: a.as_of, default=None)
    if newest is not None and not force and not is_newer_or_equal(new_acct, newest):
        return NetBenefitsApplyOutcome(
            snapshot=snap,
            replaced=[],
            field_key=field_key,
            ira_total=_owner_pretax_total(snap, owner),
            detail=detail,
            kept_newer=newest,
            new_account=new_acct,
        )
    new_snap, replaced = replace_owner_403b(snap, new_acct)
    return NetBenefitsApplyOutcome(
        snapshot=new_snap,
        replaced=replaced,
        field_key=field_key,
        ira_total=_owner_pretax_total(new_snap, owner),
        detail=detail,
    )


def _owner_pretax_total(snap: PortfolioSnapshot, owner: str) -> float:
    return float(sum(a.total_value for a in snap.accounts if a.owner == owner and a.is_pretax))
