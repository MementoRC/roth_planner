"""Year-to-date income tracking for mid-year Roth conversion planning.

Captures realized capital gains from stop-loss triggers, wages, and other
income events so the conversion planner can compute accurate remaining
headroom against IRMAA, NIIT, and bracket thresholds.

Key distinction: LTCG affects MAGI (IRMAA/NIIT) but NOT ordinary bracket room.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any


@dataclass
class RealizedGainEvent:
    """Individual sale/stop-loss event for display drill-down."""

    date: str  # ISO date string
    description: str
    proceeds: float
    cost_basis: float
    holding_period: str  # "short" or "long"
    account_name: str = ""

    @property
    def gain_loss(self) -> float:
        return self.proceeds - self.cost_basis

    @property
    def is_ltcg(self) -> bool:
        return self.holding_period == "long"


@dataclass
class IncomeEvent:
    """A single logged Roth conversion or IRA distribution, entered as it happens.

    Custodian statements lag; the user is the only real-time source of truth
    for "I converted/withdrew $X today" — this is an audit-trail log, not a
    derived/synced value.
    """

    date: str  # ISO date string
    amount: float
    kind: str  # "conversion" or "distribution"
    owner: str = "you"  # "you" or "spouse"


# Issue #496 / bridge v5: the non-ledger YTD fields that are per-person but had
# no per-person representation. When ``YTDSnapshot.by_owner`` is non-empty the
# household value of each is a DERIVED SUM of the per-owner entries. IRA
# conversions are deliberately absent: they are already per-person by field
# name (ira_conversions_ytd = you, spouse_ira_conversions_ytd = spouse), and
# scenario_compute subtracts ira_conversions_ytd from YOUR conversion only.
OWNED_YTD_FIELDS: tuple[str, ...] = (
    "wages_ytd",
    "federal_withholding_ytd",
    "qualified_dividends_ytd",
    "nqo_exercise_ytd",
)
YTD_OWNERS: tuple[str, ...] = ("you", "spouse")


def _check_owner(owner: str) -> None:
    if owner not in YTD_OWNERS:
        raise ValueError(f"unknown YTD owner {owner!r}; expected one of {YTD_OWNERS}")


def _check_owned_fields(names: Any) -> None:
    unknown = sorted(set(names) - set(OWNED_YTD_FIELDS))
    if unknown:
        raise ValueError(
            f"not per-owner YTD fields: {unknown}; expected a subset of {OWNED_YTD_FIELDS}"
        )


# Redesign PR 1b: household-wide fields the manual-entry form sets by hand.
# Per-person hand entries use OWNED_YTD_FIELDS keyed by owner instead.
MANUAL_HOUSEHOLD_FIELDS: tuple[str, ...] = (
    "nec_income_ytd",
    "ltcg_ytd",
    "stcg_ytd",
    "ordinary_dividends_ytd",
    "interest_ytd",
    "tax_exempt_interest_ytd",
    "estimated_payments_ytd",
    "hsa_contribution_ytd",
    "deductible_ira_contribution_ytd",
    "crypto_stcg_ytd",
    "crypto_ltcg_ytd",
    "crypto_income_ytd",
    # audit-1008 W16: the lending-interest part of crypto_income_ytd (NII). Without
    # it a hand-typed crypto income kept the last import's interest split.
    "crypto_interest_ytd",
)
HOUSEHOLD = "household"
# Non-field attributes the FinExtract option-exercise sync sets on a snapshot
# (engine/portfolio_sync/exercises.py: per-grant spread and sale info). They are
# transient by design -- never saved -- but every copy helper below carries them,
# because dataclasses.replace() drops non-field attributes (audit-1008 F-87).
TRANSIENT_ATTRS: tuple[str, ...] = ("_option_exercises_by_grant", "_option_exercises_sale_info")
# Two amounts closer than half a cent are the same entered value.
_SAME_VALUE = 0.005


@dataclass(frozen=True)
class ManualMark:
    """Provenance of one hand-entered YTD value (redesign PR 1b).

    ``value`` is what was typed, ``as_of`` the ISO date the figure is current
    to (a paystub's date, not the day it was typed), ``note`` its source.
    """

    value: float
    as_of: str
    note: str = ""


def manual_key(field_name: str, owner: str | None = None) -> str:
    """``"<owner>.<field>"`` for a per-owner field, ``"household.<field>"`` otherwise."""
    if owner is None:
        if field_name not in MANUAL_HOUSEHOLD_FIELDS:
            raise ValueError(
                f"{field_name!r} is not a household manual field; "
                f"expected one of {MANUAL_HOUSEHOLD_FIELDS}"
            )
        return f"{HOUSEHOLD}.{field_name}"
    _check_owner(owner)
    _check_owned_fields([field_name])
    return f"{owner}.{field_name}"


def split_manual_key(key: str) -> tuple[str, str]:
    """Inverse of :func:`manual_key`; raises ValueError for a malformed key."""
    owner, sep, field_name = key.partition(".")
    if not sep:
        raise ValueError(f"malformed manual-entry key {key!r}")
    manual_key(field_name, None if owner == HOUSEHOLD else owner)
    return owner, field_name


def manual_mark_from_dict(raw: object) -> ManualMark | None:
    """A ManualMark from its JSON form, or None when malformed (callers drop it)."""
    if not isinstance(raw, dict):
        return None
    value, as_of, note = raw.get("value"), raw.get("as_of"), raw.get("note", "")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    if not isinstance(as_of, str):
        return None
    try:
        date.fromisoformat(as_of[:10])
    except ValueError:
        return None
    return ManualMark(float(value), as_of[:10], note if isinstance(note, str) else "")


def iso_day(value: object) -> str | None:
    """The YYYY-MM-DD day of an ISO date/timestamp, or None when absent or unparsable.

    Every import's as-of goes through this before the manual-entry supersede
    rule compares it, so a garbled date counts as undated (the import wins)
    instead of being compared as text.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def sum_income_events(events: list[IncomeEvent], *, kind: str, owner: str | None = None) -> float:
    """Sum event amounts matching kind (and owner, if given)."""
    return sum(e.amount for e in events if e.kind == kind and (owner is None or e.owner == owner))


def _net_capital_gain_split(short_term: float, long_term: float) -> tuple[float, float]:
    """audit-0805 C2: IRC §1222 short/long netting + IRC §1211(b) $3,000 loss cap.

    Nets the short-term and long-term capital positions against each other
    (§1222(11) defines "net capital gain" as the excess of net long-term
    gain over net short-term loss; the mirror-image case -- a net short-term
    gain surviving a net long-term loss -- follows the same Schedule D
    cross-netting mechanics). If the netted result is an overall capital
    LOSS, only $3,000 of it may offset ordinary income for the year
    (§1211(b)); the disallowed excess is simply DROPPED here -- there is no
    carryforward field on YTDSnapshot (a single-tax-year object), so this
    intentionally does NOT persist the excess to a future year.

    Returns (ordinary_portion, preferential_portion):
      - Both legs are gains (short_term >= 0 and long_term >= 0): no
        cross-netting needed -- each keeps its own character.
      - Opposite signs, net result >= 0: the loss leg is fully absorbed;
        the surviving gain keeps the character of whichever leg was
        positive.
      - Net result < 0 (an overall net capital loss, from any combination
        of signs): only the ordinary side is nonzero, capped at -$3,000.
    """
    total = short_term + long_term
    if total < 0:
        return max(total, -3_000.0), 0.0
    if short_term >= 0 and long_term >= 0:
        return short_term, long_term
    if short_term < 0:
        # short-term loss absorbed into a long-term gain -- surviving gain is long-term
        return 0.0, total
    # long-term loss absorbed into a short-term gain -- surviving gain is short-term
    return total, 0.0


@dataclass
class YTDSnapshot:
    """Aggregate year-to-date income actuals.

    Used to override base-year projections with real-world events
    (stop-loss triggers, partial-year wages, etc.) so the conversion
    planner shows accurate remaining headroom.
    """

    tax_year: int = 2026
    snapshot_date: str = ""  # ISO date of last update

    # Ordinary income components
    wages_ytd: float = 0.0
    nec_income_ytd: float = 0.0  # 1099-NEC / self-employment
    ira_conversions_ytd: float = 0.0  # conversions already done this year (your side)
    spouse_ira_conversions_ytd: float = 0.0  # spouse's conversions already done this year
    ira_distributions_ytd: float = 0.0  # non-conversion IRA withdrawals
    income_events: list[IncomeEvent] = field(default_factory=list)

    # Investment income components
    ltcg_ytd: float = 0.0  # long-term capital gains (stop-loss triggers)
    stcg_ytd: float = 0.0  # short-term capital gains
    qualified_dividends_ytd: float = 0.0
    ordinary_dividends_ytd: float = 0.0
    interest_ytd: float = 0.0
    tax_exempt_interest_ytd: float = 0.0  # muni bond interest: in MAGI, NOT in ordinary brackets
    nqo_exercise_ytd: float = 0.0  # NQO ordinary-income spread from realized exercises

    # Withholding / payments
    federal_withholding_ytd: float = 0.0  # W-2 federal tax withheld YTD
    estimated_payments_ytd: float = 0.0  # Form 1040-ES quarterly estimated tax payments YTD

    # Above-the-line adjustments
    hsa_contribution_ytd: float = 0.0  # Form 8889 deductible HSA contribution (reduces AGI/MAGI)
    deductible_ira_contribution_ytd: float = (
        0.0  # Sch 1 deductible traditional-IRA contribution (reduces AGI/MAGI)
    )

    # Crypto (Koinly-sourced)
    crypto_stcg_ytd: float = (
        0.0  # crypto short-term capital gains (Koinly): ordinary brackets + MAGI + NIIT
    )
    crypto_ltcg_ytd: float = (
        0.0  # crypto long-term capital gains (Koinly): MAGI + NIIT, not brackets
    )
    crypto_income_ytd: float = (
        0.0  # crypto staking/DeFi/airdrop income (Sch 1 8z): ordinary brackets + MAGI, not NIIT
    )
    crypto_interest_ytd: float = (
        0.0  # the Koinly "Lending interest" PART of crypto_income_ytd: also NII (§1411)
    )

    # Drill-down events
    gain_events: list[RealizedGainEvent] = field(default_factory=list)

    # Metadata
    manually_entered: bool = True

    # Per-owner store for OWNED_YTD_FIELDS (issue #496). Empty = legacy
    # "unattributed" snapshot whose household totals are stored directly.
    # Non-empty = the store is authoritative and the household totals are
    # recomputed from it on every construction (including dataclasses.replace).
    by_owner: dict[str, dict[str, float]] = field(default_factory=dict)

    # Redesign PR 1b: provenance of hand-entered values, keyed by manual_key().
    # A key exists only while the live value IS the hand-entered one: an import
    # that supersedes it removes the mark (see settle_manual_marks).
    manual_marks: dict[str, ManualMark] = field(default_factory=dict)

    # Redesign PR 1b: each person's federal withholding on option exercises,
    # from the UBS ACTIVITY CSV. Kept APART from by_owner[owner]
    # ["federal_withholding_ytd"] -- that is the BASE (paycheck, hand entry,
    # other imports) -- so a UBS re-scan replaces only this component and
    # never doubles. Household federal_withholding_ytd = sum(base) + sum(this).
    # An owner key present means that owner's withholding has been split.
    # Not in OWNED_YTD_FIELDS on purpose: hand entry never edits it, and the
    # v5 bridge carries it as a separate optional key.
    ubs_withholding_by_owner: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Non-negative guard (audit 2026-07-13, R1+R2): these two fields are
        # SUBTRACTED in above_the_line_adjustments_ytd. Without a floor, a
        # negative entry (e.g. from a widget lacking min_value) flips from
        # reducing income to inflating total_ordinary_income/magi_ytd. Clamp
        # at the model level so the invariant holds regardless of widget
        # config. ltcg_ytd/stcg_ytd are intentionally NOT clamped — they can
        # legitimately be negative (losses; see PR #368).
        self.hsa_contribution_ytd = max(0.0, self.hsa_contribution_ytd)
        self.deductible_ira_contribution_ytd = max(0.0, self.deductible_ira_contribution_ytd)
        # Validate and deep-copy the per-owner store so a snapshot produced by
        # dataclasses.replace() never shares inner dicts with its source
        # (set_owned mutates in place).
        normalised: dict[str, dict[str, float]] = {}
        for owner, slot in self.by_owner.items():
            _check_owner(owner)
            _check_owned_fields(slot)
            normalised[owner] = {f: float(v) for f, v in slot.items()}
        self.by_owner = normalised
        for key in self.manual_marks:
            split_manual_key(key)
        self.manual_marks = dict(self.manual_marks)
        for owner in self.ubs_withholding_by_owner:
            _check_owner(owner)
        if self.ubs_withholding_by_owner and not self.by_owner:
            raise ValueError("ubs_withholding_by_owner requires an attributed snapshot (by_owner)")
        self.ubs_withholding_by_owner = {
            o: float(v) for o, v in self.ubs_withholding_by_owner.items()
        }
        self._recompute_owned_totals()

    def _copy(self, **fields: Any) -> YTDSnapshot:
        """dataclasses.replace() that also carries TRANSIENT_ATTRS (audit-1008 F-87)."""
        out = replace(self, **fields)
        for name in TRANSIENT_ATTRS:
            value = getattr(self, name, None)
            if value is not None:
                setattr(out, name, dict(value))
        return out

    def _recompute_owned_totals(self) -> None:
        if not self.by_owner:
            return
        for f in OWNED_YTD_FIELDS:
            setattr(self, f, sum(slot.get(f, 0.0) for slot in self.by_owner.values()))
        # Redesign PR 1b: the slots hold BASE withholding; add the UBS components.
        self.federal_withholding_ytd += sum(self.ubs_withholding_by_owner.values())

    def attribute_unowned(self, owner: str) -> YTDSnapshot:
        """Return a copy whose legacy household totals are attributed to *owner*.

        No-op (returns an equal copy) when the snapshot is already attributed.
        Household totals are unchanged either way.
        """
        _check_owner(owner)
        if self.by_owner:
            return self._copy()
        return self._copy(by_owner={owner: {f: getattr(self, f) for f in OWNED_YTD_FIELDS}})

    def replace_owner_values(self, owner: str, values: dict[str, float]) -> YTDSnapshot:
        """Return a copy whose *owner* slot is REPLACED by *values* (never added).

        Fields absent from *values* become 0 for that owner, so re-applying the
        same values is idempotent. The snapshot must already be attributed --
        on a legacy snapshot this would silently discard the stored totals.
        """
        _check_owner(owner)
        _check_owned_fields(values)
        if not self.by_owner:
            raise ValueError("snapshot is unattributed; call attribute_unowned(owner) first")
        new_store = dict(self.by_owner)
        new_store[owner] = {f: float(values.get(f, 0.0)) for f in OWNED_YTD_FIELDS}
        return self._copy(by_owner=new_store)

    def set_owned(self, owner: str, **values: float) -> None:
        """In-place write of *owner*'s value for owned fields.

        On a legacy (unattributed) snapshot this assigns the household total,
        which is exactly the pre-#496 behaviour of the writers that call it.
        """
        _check_owner(owner)
        _check_owned_fields(values)
        if not self.by_owner:
            for f, v in values.items():
                setattr(self, f, float(v))
            return
        new_store = {o: dict(slot) for o, slot in self.by_owner.items()}
        new_store.setdefault(owner, {}).update({f: float(v) for f, v in values.items()})
        self.by_owner = new_store
        self._recompute_owned_totals()

    def owner_value(self, owner: str, field_name: str) -> float:
        """*owner*'s value of a per-owner field; the household total when unattributed.

        For federal_withholding_ytd this is the BASE (paycheck, hand entry,
        other imports); see withholding_total for base + UBS component.
        """
        _check_owner(owner)
        _check_owned_fields([field_name])
        if not self.by_owner:
            return float(getattr(self, field_name))
        return float(self.by_owner.get(owner, {}).get(field_name, 0.0))

    def withholding_total(self, owner: str) -> float:
        """*owner*'s federal withholding: base + UBS option-exercise component."""
        base = self.owner_value(owner, "federal_withholding_ytd")
        return base + self.ubs_withholding_by_owner.get(owner, 0.0)

    def set_ubs_withholding(self, owner: str, amount: float) -> None:
        """IN PLACE: REPLACE *owner*'s UBS withholding component (never add).

        Attributes a legacy snapshot to *owner* first (its totals unchanged).
        """
        _check_owner(owner)
        if not self.by_owner:
            self.by_owner = {owner: {f: float(getattr(self, f)) for f in OWNED_YTD_FIELDS}}
        self.ubs_withholding_by_owner = {**self.ubs_withholding_by_owner, owner: float(amount)}
        self._recompute_owned_totals()

    def with_manual_values(
        self,
        *,
        owner: str,
        owned: dict[str, float],
        household: dict[str, float],
        as_of: str,
        note: str = "",
    ) -> YTDSnapshot:
        """Return a copy with hand-entered values written and marked (redesign PR 1b).

        Only values that differ from what is stored are written and marked.
        *owned* lands in *owner*'s slot exactly as typed -- never "household
        total minus the other owners", so no typed value can be floored away.
        Requires an attributed snapshot when *owned* is non-empty.
        """
        _check_owner(owner)
        _check_owned_fields(owned)
        unknown = sorted(set(household) - set(MANUAL_HOUSEHOLD_FIELDS))
        if unknown:
            raise ValueError(f"not household manual fields: {unknown}")
        if owned and not self.by_owner:
            raise ValueError("snapshot is unattributed; call attribute_unowned(owner) first")
        date.fromisoformat(as_of)
        # dict[str, Any]: mypy cannot match **dict[str, float] to replace()'s
        # mixed-type keyword parameters; every key here is a float field.
        changed: dict[str, Any] = {
            f: float(v)
            for f, v in household.items()
            if abs(getattr(self, f) - float(v)) >= _SAME_VALUE
        }
        out = self._copy(**changed)  # re-runs __post_init__ (HSA/IRA clamp)
        for f in changed:
            if abs(getattr(self, f) - getattr(out, f)) >= _SAME_VALUE:
                out.manual_marks[manual_key(f)] = ManualMark(getattr(out, f), as_of, note)
        for f, v in owned.items():
            if abs(out.owner_value(owner, f) - float(v)) >= _SAME_VALUE:
                out.set_owned(owner, **{f: float(v)})
                out.manual_marks[manual_key(f, owner)] = ManualMark(float(v), as_of, note)
        return out

    def manual_value(self, key: str) -> float:
        """The live value a manual_key() refers to (the BASE for withholding)."""
        owner, field_name = split_manual_key(key)
        if owner == HOUSEHOLD:
            return float(getattr(self, field_name))
        return self.owner_value(owner, field_name)

    def _write_manual_value(self, key: str, value: float) -> None:
        owner, field_name = split_manual_key(key)
        if owner == HOUSEHOLD:
            setattr(self, field_name, value)
        else:
            self.set_owned(owner, **{field_name: value})

    def settle_manual_marks(self, *, as_of: str | None) -> tuple[str, ...]:
        """Reconcile hand-entered values with what an import just wrote. IN PLACE.

        Call right after an import writer, with the date the imported data is
        current to. For each marked value the import changed: if the hand
        entry's as-of is strictly later than *as_of*, the hand-entered value
        is restored and keeps its mark; otherwise the import supersedes it and
        the mark is dropped. An undated import (None/"") supersedes, which is
        the pre-PR-1b behaviour; so does an unparsable date. Returns the keys
        restored.
        """
        cutoff = iso_day(as_of)
        kept: dict[str, ManualMark] = {}
        restored: list[str] = []
        for key, mark in self.manual_marks.items():
            if abs(self.manual_value(key) - mark.value) < _SAME_VALUE:
                kept[key] = mark
            elif cutoff and mark.as_of > cutoff:
                self._write_manual_value(key, mark.value)
                kept[key] = mark
                restored.append(key)
        self.manual_marks = kept
        return tuple(restored)

    @property
    def dividends_ytd(self) -> float:
        """Total YTD dividends (qualified + ordinary). Backward-compat."""
        return self.qualified_dividends_ytd + self.ordinary_dividends_ytd

    @property
    def net_short_term_capital_ytd(self) -> float:
        """IRC §1222(1)/(2): net short-term capital gain or loss, before
        cross-netting against the long-term position."""
        return self.stcg_ytd + self.crypto_stcg_ytd

    @property
    def net_long_term_capital_ytd(self) -> float:
        """IRC §1222(3)/(4): net long-term capital gain or loss, before
        cross-netting against the short-term position."""
        return self.ltcg_ytd + self.crypto_ltcg_ytd

    @property
    def ordinary_capital_gain_ytd(self) -> float:
        """audit-0805 C2: short-term-character portion of YTD realized
        capital gain/loss after IRC §1222 netting and the IRC §1211(b)
        $3,000 loss cap -- this is what stacks into ORDINARY tax brackets
        (see ``_net_capital_gain_split``)."""
        return _net_capital_gain_split(
            self.net_short_term_capital_ytd, self.net_long_term_capital_ytd
        )[0]

    @property
    def preferential_capital_gain_ytd(self) -> float:
        """audit-0805 C2: long-term-character portion of YTD realized
        capital gain after IRC §1222 netting -- this is what reaches the
        PREFERENTIAL (0/15/20%) rate stack. Never negative: a net capital
        LOSS is entirely characterized as the (capped) ordinary portion
        above, since §1211(b)'s cap applies to the ordinary-income offset,
        not to a preferential-rate amount."""
        return _net_capital_gain_split(
            self.net_short_term_capital_ytd, self.net_long_term_capital_ytd
        )[1]

    @property
    def above_the_line_adjustments_ytd(self) -> float:
        """HSA + deductible-IRA contributions; above-the-line, reduce AGI (hence MAGI and ordinary bracket base)."""
        return self.hsa_contribution_ytd + self.deductible_ira_contribution_ytd

    @property
    def total_ordinary_income(self) -> float:
        """Income that stacks into ordinary tax brackets.

        Includes: wages, NEC, STCG, conversions, distributions, ordinary dividends, interest.
        Excludes: LTCG and qualified dividends (taxed at preferential rates, not in brackets).
        Ordinary (non-qualified) dividends and interest are taxed as ordinary income and must
        count toward bracket headroom and SS taxation.
        Net of above-the-line HSA/IRA adjustments — this is the AGI-basis figure used for
        the ordinary bracket walk, since those deductions apply before brackets are computed.
        Crypto STCG and crypto income (staking/DeFi/airdrops, Sch 1 8z) are ordinary income
        and stack into brackets; crypto LTCG does not (preferential rate, see magi_ytd).

        audit-0805 C2: the STCG/crypto-STCG contribution is ``ordinary_capital_gain_ytd``
        (IRC §1222-netted against the long-term position, then IRC §1211(b)-capped at
        -$3,000 if the net result is a loss) -- NOT the raw ``stcg_ytd + crypto_stcg_ytd``
        sum. Without netting, a short-term GAIN would hit ordinary brackets undiminished
        even when a same-size (or larger) long-term LOSS exists to offset it; without the
        cap, a large net capital loss would be free to wipe out ordinary income far beyond
        the $3,000/year statutory ceiling.
        """
        return (
            self.wages_ytd
            + self.nec_income_ytd
            + self.ordinary_capital_gain_ytd
            + self.ira_conversions_ytd
            + self.spouse_ira_conversions_ytd
            + self.ira_distributions_ytd
            + self.ordinary_dividends_ytd
            + self.interest_ytd
            + self.nqo_exercise_ytd
            + self.crypto_income_ytd
            - self.above_the_line_adjustments_ytd
        )

    @property
    def total_investment_income(self) -> float:
        """Net investment income for NIIT calculation.

        NIIT applies to: LTCG + STCG + dividends + interest.
        Does NOT include wages, SS, or IRA distributions.
        Crypto STCG/LTCG count as capital gains and are included. Crypto staking/DeFi/
        airdrop income (crypto_income_ytd) is deliberately excluded — staking-as-NII
        is unsettled, so it is conservatively treated as non-investment income here.
        The exception is crypto lending interest (crypto_interest_ytd, the Koinly
        "Lending interest" part of crypto_income_ytd): interest is NII by statute,
        IRC §1411(c)(1)(A)(i) (audit-1008 F-54). It is capped at crypto_income_ytd so
        a hand-lowered crypto income never leaves a larger NII part behind.

        audit-0809 Class A (site 5): the capital-gain contribution is
        ``ordinary_capital_gain_ytd + preferential_capital_gain_ytd`` (IRC §1222-netted,
        IRC §1211(b)-capped at -$3,000 if the net result is a loss) -- NOT the raw
        ``ltcg_ytd + stcg_ytd + crypto_stcg_ytd + crypto_ltcg_ytd`` sum. This is a
        DELIBERATE reversal of an audit-0805 note that called the raw sum correct for
        the NII base specifically; that note is wrong against the regulation. Per
        Reg. §1.1411-4(d)(2), net gain for NII purposes "may not be less than zero,"
        and per Reg. §1.1411-4(f)(4) (Example 1), the §1211(b) $3,000 allowed capital
        loss IS a properly allocable deduction against NII -- it is not simply added,
        unfloored, into the NII base. Form 8960 line 5a starts from the Schedule D
        figure already capped at -$3,000, and line 5d (which carries that capped loss
        into the NII computation) has no zero floor of its own. Worked case: a $50,000
        net capital LOSS + $40,000 ordinary dividends nets to NII $37,000 (= $0 floored
        gain + $40,000 dividends - $3,000 allowed loss deduction), not the raw sum's
        -$10,000 (which then gets floored to $0 by niit.py, silently erasing $37,000 of
        real NII). For any net capital GAIN this substitution is numerically identical
        to the raw sum (netting a gain against nothing is a no-op); it only differs --
        correctly -- when a net capital LOSS exists.
        """
        return (
            self.ordinary_capital_gain_ytd
            + self.preferential_capital_gain_ytd
            + self.dividends_ytd
            + self.interest_ytd
            + min(max(self.crypto_interest_ytd, 0.0), max(self.crypto_income_ytd, 0.0))
        )

    @property
    def magi_ytd(self) -> float:
        """Modified AGI for IRMAA/ACA threshold checks.

        Includes ALL income: ordinary + LTCG + dividends + interest.
        LTCG is in MAGI even though it's not in ordinary brackets.
        Tax-exempt interest (muni bonds) is included: IRMAA MAGI under SSA
        §1839(i)(4) (42 U.S.C. §1395r(i)(4)) = AGI + tax-exempt interest +
        excluded foreign income (§§911/931/933, which this snapshot does not
        track; engine.tax_return_pdf.compute_irmaa_magi adds the FEIE back).
        Untaxed Social Security is NOT part of IRMAA MAGI -- it belongs to ACA
        MAGI (IRC §36B(d)(2)(B)), and ACA callers add it themselves.
        MAGI is AGI-basis: above-the-line HSA/IRA adjustments reduce AGI and are
        therefore subtracted here too.
        Crypto STCG, crypto LTCG, and crypto income (staking/DeFi/airdrops) are all
        included in MAGI regardless of their bracket/NIIT treatment.

        audit-0805 C2: the STCG+LTCG contribution is
        ``ordinary_capital_gain_ytd + preferential_capital_gain_ytd`` (IRC §1222-netted,
        IRC §1211(b)-capped at -$3,000 if the net result is a loss) rather than the
        raw ``stcg_ytd + ltcg_ytd + crypto_*_ytd`` sum. For any net capital GAIN this is
        numerically identical to the raw sum (netting a gain against nothing is a no-op);
        it only differs -- correctly -- when a net capital LOSS exceeds the $3,000
        statutory cap, mirroring how the capped (not raw) loss is what actually reaches
        AGI/MAGI on Form 1040.
        """
        return (
            self.wages_ytd
            + self.nec_income_ytd
            + self.ordinary_capital_gain_ytd
            + self.ira_conversions_ytd
            + self.spouse_ira_conversions_ytd
            + self.ira_distributions_ytd
            + self.preferential_capital_gain_ytd
            + self.dividends_ytd
            + self.interest_ytd
            + self.tax_exempt_interest_ytd
            + self.nqo_exercise_ytd
            + self.crypto_income_ytd
            - self.above_the_line_adjustments_ytd
        )

    @property
    def niit_magi_ytd(self) -> float:
        """MAGI variant for NIIT (IRC §1411(d) modified AGI): excludes tax-exempt interest.

        NIIT MAGI differs from IRMAA MAGI in one respect: tax-exempt (muni bond)
        interest is excluded. Not because §1411(d) carves it out, but because
        it is excluded from gross income entirely under IRC §103 -- it was
        never in AGI (and therefore never in §1411(d)'s MAGI) to begin with.
        Use this when computing NIIT liability; use magi_ytd for IRMAA/ACA
        threshold checks.
        """
        return self.magi_ytd - self.tax_exempt_interest_ytd

    def with_snapshot_date(self) -> YTDSnapshot:
        """Set snapshot_date to today (in place) and return self for chaining."""
        self.snapshot_date = date.today().isoformat()
        return self

    def overlay(self, **fields: Any) -> YTDSnapshot:
        """Return a NEW snapshot equal to ``self`` except for ``fields``.

        Every write site (manual entry, FinExtract sync, PDF-folder scan,
        etc.) should start from the previously-persisted snapshot and call
        ``prev.overlay(**computed_fields)`` instead of constructing a fresh
        ``YTDSnapshot(...)`` from scratch. Fields NOT passed are carried
        forward unchanged from ``self`` -- including list fields
        (income_events/gain_events) and the metadata fields (tax_year,
        snapshot_date, manually_entered) when a site does not touch them.

        A field passed explicitly is ALWAYS applied, even when the value is
        the type's zero/default (e.g. ``wages_ytd=0.0``) -- this method must
        never fall back to "if computed value == default, keep self's"
        semantics, since that would make it impossible for a user to
        legitimately zero out a field they cleared on the form (audit-0805
        C42/C32/C96: the recurring bug this helper replaces was exactly the
        opposite failure mode -- a fresh ``YTDSnapshot()`` silently dropping
        fields the call site never touched).

        Redesign PR 1b: on an ATTRIBUTED snapshot a per-owner field has no
        single household value to overlay. This used to attribute "typed
        total minus the other owners" to one owner, floored at 0, which
        silently discarded a total typed below the other owner's share. It
        now raises: write per-owner values with ``with_manual_values(owner=...)``
        or ``set_owned(owner, ...)``. On a legacy snapshot this is a plain replace.
        """
        owned = sorted(k for k in fields if k in OWNED_YTD_FIELDS)
        if self.by_owner and owned:
            raise ValueError(
                f"overlay() cannot write per-owner YTD fields {owned} on an attributed "
                "snapshot; use with_manual_values(owner=...) or set_owned(owner, ...)"
            )
        return self._copy(**fields)


def describe_kept_manual(ytd: YTDSnapshot, keys: Iterable[str]) -> list[str]:
    """One sentence per hand-entered value an import did not replace."""
    messages = []
    for key in keys:
        owner, field_name = split_manual_key(key)
        mark = ytd.manual_marks[key]
        label = field_name.removesuffix("_ytd").replace("_", " ")
        whose = "" if owner == HOUSEHOLD else f" ({owner})"
        messages.append(
            f"Kept your hand-entered {label}{whose} of {mark.value:,.2f} "
            f"(as of {mark.as_of}): the imported figure is older."
        )
    return messages
