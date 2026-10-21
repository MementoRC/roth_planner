"""SSA "Your Social Security Statement" (Form SSA-7005) parser.

The statement's page 1 prints SSA's personalized monthly retirement-benefit
estimate for each claiming age 62-70, plus the holder's full retirement age.
The FRA estimate is the figure the planner models with (``your_ss_fra`` /
``spouse_ss_fra``); it is recorded as a ``Source.PDF`` candidate, which the
resolver ladder ranks above the FinExtract-synced estimate.

The per-age figures are kept for DISPLAY only, next to what the planner's own
reduction/delayed-credit formula (engine/ira.py ``ss_benefit_at_age``) derives
from its FRA amount. They never drive the model: SSA's figures assume you keep
earning until you claim, which the planner does not model.

Nothing else on the statement is read -- not the earnings record, the
disability/survivor estimates, or the taxes paid.

pdfplumber extracts the rotated "Age / Retirement Benefits Start" column
heading as reversed words (``egA``, ``tnemeriteR`` ...). The estimate rows are
matched on their own ``NN $X,XXX`` shape, so the heading is simply skipped.

Pure: works on already-extracted page strings, no pdfplumber, no Streamlit.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from engine.ira import ss_benefit_at_age
from engine.pdf_owner import resolve_owner
from engine.secure_io import read_pii_json, write_pii_json

_SSA_PDF_CACHE_PATH = Path(__file__).resolve().parent.parent / ".ssa_pdf_cache.json"


class SsaStatementParseError(ValueError):
    """An SSA statement was recognized but its benefit estimates are missing."""


@dataclass
class SsaStatement:
    """One person's SSA retirement-benefit estimates, as of the statement date."""

    owner_key: str | None  # printed holder name; None when not found (never guessed)
    statement_date: str  # ISO date
    fra_age: int  # whole years ("66 and 10 months" -> 66)
    estimates: dict[int, float] = field(default_factory=dict)  # claim age -> $/month
    fra_months: int = 0  # months past fra_age ("66 and 10 months" -> 10)
    # The printed row for the exact FRA ("66 and 10 months $2,100"), if any.
    fra_estimate: float | None = None

    @property
    def fra_monthly(self) -> float:
        """The benefit at full retirement age.

        A printed exact-FRA row ("66 and 10 months $X") is used as is.
        Otherwise, when FRA is *fra_age* plus some months, the whole-year
        *fra_age* row is a claim that many months EARLY (reduced 5/9 of 1%
        per month), so the FRA amount is derived by undoing that.
        """
        if self.fra_estimate is not None:
            return self.fra_estimate
        at_fra_age = self.estimates[self.fra_age]
        if not self.fra_months:
            return at_fra_age
        return at_fra_age / (1 - self.fra_months * _EARLY_REDUCTION_PER_MONTH)


# Both markers are required: a 1040 or a letter can mention the statement by
# name, but only the statement itself carries the SSA-7005 form footer.
_TITLE_RE = re.compile(r"Your Social Security Statement")
_FORM_RE = re.compile(r"Form SSA-7005")


def is_ssa_statement(text: str) -> bool:
    """True when *text* is an SSA "Your Social Security Statement"."""
    return bool(_TITLE_RE.search(text) and _FORM_RE.search(text))


_DATE = r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}"
_HEADER_RE = re.compile(rf"^Your Social Security Statement\n(?:(.+?)\s+)?({_DATE})$", re.MULTILINE)
_FRA_RE = re.compile(r"Your full retirement age is (\d{2})\b(?: and (\d{1,2}) months?)?")
# "67 $2,027" or, for a months-past-the-year FRA, "66 and 10 months $2,100".
_ESTIMATE_RE = re.compile(r"^(6[2-9]|70)(?: and (\d{1,2}) months?)? \$([\d,]+)$", re.MULTILINE)

_STALE_AFTER_DAYS = 365
_EARLY_REDUCTION_PER_MONTH = 5 / 900  # 5/9 of 1% for each month claimed before FRA


def parse_ssa_statement_text(pages: Sequence[str]) -> SsaStatement:
    """Parse an SSA statement from its per-page text.

    Raises :class:`SsaStatementParseError` when the statement date, the full
    retirement age, or the estimate at that age is missing -- without them
    there is nothing to import. A missing holder name is ``None``.
    """
    page_1 = pages[0] if pages else ""

    header = _HEADER_RE.search(page_1)
    if header is None:
        raise SsaStatementParseError("SSA statement: could not find the statement date.")
    fra = _FRA_RE.search(page_1)
    if fra is None:
        raise SsaStatementParseError("SSA statement: could not find the full retirement age.")

    fra_age = int(fra.group(1))
    fra_months = int(fra.group(2) or 0)
    estimates: dict[int, float] = {}
    fra_estimate: float | None = None
    for m in _ESTIMATE_RE.finditer(page_1):
        age, months = int(m.group(1)), int(m.group(2) or 0)
        amount = float(m.group(3).replace(",", ""))
        if not months:
            estimates.setdefault(age, amount)
        elif (age, months) == (fra_age, fra_months) and fra_estimate is None:
            fra_estimate = amount
        # Any other "N and M months" row has no whole-year slot; it is skipped.
    if fra_age not in estimates and fra_estimate is None:
        raise SsaStatementParseError(
            f"SSA statement: could not find the benefit estimate at age {fra_age}."
        )

    holder = header.group(1)
    return SsaStatement(
        owner_key=holder.strip() if holder else None,
        statement_date=datetime.strptime(header.group(2), "%B %d, %Y").date().isoformat(),
        fra_age=fra_age,
        estimates=estimates,
        fra_months=fra_months,
        fra_estimate=fra_estimate,
    )


def statement_age_notice(stmt: SsaStatement, *, today: date) -> str | None:
    """A notice when the statement is more than a year old, else ``None``."""
    issued = date.fromisoformat(stmt.statement_date)
    if (today - issued).days <= _STALE_AFTER_DAYS:
        return None
    return (
        f"This statement is dated {stmt.statement_date}, more than a year ago. "
        "A current one from my Social Security may show different estimates."
    )


@dataclass(frozen=True)
class BenefitComparisonRow:
    """SSA's printed estimate for one claim age next to the planner's formula."""

    age: int
    ssa_monthly: float
    planner_monthly: float

    @property
    def difference(self) -> float:
        return self.ssa_monthly - self.planner_monthly


def benefit_comparison(
    estimates: dict[int, float], *, fra_monthly: float, fra_age: int
) -> list[BenefitComparisonRow]:
    """One row per claim age SSA printed: SSA's figure and what the planner's
    formula derives from *fra_monthly* (the FRA amount the planner models)."""
    return [
        BenefitComparisonRow(
            age=age,
            ssa_monthly=estimates[age],
            planner_monthly=ss_benefit_at_age(fra_monthly, age, fra_age) / 12,
        )
        for age in sorted(estimates)
    ]


def default_ssa_owner(stmt: SsaStatement, owner_map: dict[str, str], instance_owner: str) -> str:
    """Pre-select whose statement this is: the learned holder-name mapping
    first, then this planner instance's owner, then "you".

    A statement belongs to one person, so a "household" answer from either
    source is not usable and falls through.
    """
    named = resolve_owner(stmt.owner_key, owner_map)
    for candidate in (named, instance_owner):
        if candidate in ("you", "spouse"):
            return candidate
    return "you"


def pick_latest_ssa(statements: Iterable[SsaStatement]) -> list[SsaStatement]:
    """Keep the newest statement (by statement date) per named holder.

    Statements with no printed holder name cannot be told apart, so they are
    never merged: each distinct one stays queued for the user to assign.
    """
    latest: dict[str, SsaStatement] = {}
    nameless: list[SsaStatement] = []
    for stmt in statements:
        key = stmt.owner_key
        if key is None:
            if stmt not in nameless:
                nameless.append(stmt)
        elif key not in latest or stmt.statement_date > latest[key].statement_date:
            latest[key] = stmt
    return [*latest.values(), *nameless]


def save_ssa_pdf_estimates(owner: str, stmt: SsaStatement, *, path: Path | None = None) -> None:
    """Persist *stmt*'s per-age estimates under *owner* for the comparison
    display. The holder name is not stored -- only date, FRA and estimates.

    Kept apart from the FinExtract SSA cache on purpose: app.py re-records
    that cache as a FinExtract candidate on every boot, which would mislabel
    a PDF-sourced figure.
    """
    if owner not in ("you", "spouse"):
        raise ValueError(f"SSA statement owner must be 'you' or 'spouse', got {owner!r}")
    target = path or _SSA_PDF_CACHE_PATH
    existing: dict = {}
    if target.exists():
        try:
            existing = read_pii_json(target)
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing[owner] = {
        "statement_date": stmt.statement_date,
        "fra_age": stmt.fra_age,
        "fra_months": stmt.fra_months,
        "fra_estimate": stmt.fra_estimate,
        "estimates": {str(age): amount for age, amount in stmt.estimates.items()},
    }
    write_pii_json(target, existing)


def _optional_float(value: object) -> float | None:
    return None if value is None else float(str(value))


def load_ssa_pdf_estimates(owner: str, *, path: Path | None = None) -> SsaStatement | None:
    """Load *owner*'s persisted estimates, or ``None`` if absent/unreadable."""
    target = path or _SSA_PDF_CACHE_PATH
    if not target.exists():
        return None
    try:
        entry = read_pii_json(target).get(owner)
        if entry is None:
            return None
        return SsaStatement(
            owner_key=None,
            statement_date=str(entry["statement_date"]),
            fra_age=int(entry["fra_age"]),
            estimates={int(age): float(v) for age, v in entry["estimates"].items()},
            fra_months=int(entry.get("fra_months", 0)),
            fra_estimate=_optional_float(entry.get("fra_estimate")),
        )
    except (json.JSONDecodeError, OSError, AttributeError, KeyError, TypeError, ValueError):
        return None
