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

    @property
    def fra_monthly(self) -> float:
        return self.estimates[self.fra_age]


# Both markers are required: a 1040 or a letter can mention the statement by
# name, but only the statement itself carries the SSA-7005 form footer.
_TITLE_RE = re.compile(r"Your Social Security Statement")
_FORM_RE = re.compile(r"Form SSA-7005")


def is_ssa_statement(text: str) -> bool:
    """True when *text* is an SSA "Your Social Security Statement"."""
    return bool(_TITLE_RE.search(text) and _FORM_RE.search(text))


_DATE = r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}"
_HEADER_RE = re.compile(rf"^Your Social Security Statement\n(?:(.+?)\s+)?({_DATE})$", re.MULTILINE)
_FRA_RE = re.compile(r"Your full retirement age is (\d{2})\b")
_ESTIMATE_RE = re.compile(r"^(6[2-9]|70) \$([\d,]+)$", re.MULTILINE)

_STALE_AFTER_DAYS = 365


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

    estimates: dict[int, float] = {}
    for m in _ESTIMATE_RE.finditer(page_1):
        estimates.setdefault(int(m.group(1)), float(m.group(2).replace(",", "")))
    fra_age = int(fra.group(1))
    if fra_age not in estimates:
        raise SsaStatementParseError(
            f"SSA statement: could not find the benefit estimate at age {fra_age}."
        )

    holder = header.group(1)
    return SsaStatement(
        owner_key=holder.strip() if holder else None,
        statement_date=datetime.strptime(header.group(2), "%B %d, %Y").date().isoformat(),
        fra_age=fra_age,
        estimates=estimates,
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
    """Keep the newest statement (by statement date) per holder."""
    latest: dict[str | None, SsaStatement] = {}
    for stmt in statements:
        key = stmt.owner_key
        if key not in latest or stmt.statement_date > latest[key].statement_date:
            latest[key] = stmt
    return list(latest.values())


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
        "estimates": {str(age): amount for age, amount in stmt.estimates.items()},
    }
    write_pii_json(target, existing)


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
        )
    except (json.JSONDecodeError, OSError, AttributeError, KeyError, TypeError, ValueError):
        return None
