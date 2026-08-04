"""Household data model — single source of truth for all personal inputs."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Literal

from config.loader import load_defaults
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant

_D = load_defaults()


def default_rmd_age(birth_year: int) -> int:
    """Statutory RMD start age by birth year (SECURE 2.0 §107 / IRC §401(a)(9)(C)(v)):
    born 1951-1959 → 73; born 1960+ → 75. Cohorts born ≤1950 are already past RMD and
    outside this forward planner's scope, so they also resolve to 75."""
    return 73 if 1951 <= birth_year <= 1959 else 75


@dataclass
class InheritedIRA:
    """A non-spousal inherited IRA subject to the SECURE Act 10-year rule.

    The beneficiary (owner) must fully distribute the balance within 10 years
    of inherited_year. Drain formula: balance_start_of_year / years_remaining
    (1/10 first year, 1/9, ..., 1/1 final year — effectively a front-end-loaded
    drain that fully empties at year 10).

    Drained amount adds to the owner's ordinary income (combined_gross + MAGI).
    Inherited IRAs are NOT eligible for QCD. Balance grows at `growth_rate`
    between draws (default 0.07 to match the engine's IRA growth heuristic).

    NOT MODELED:
    - Eligible Designated Beneficiary (EDB) exceptions — spouse, minor child,
      disabled/chronically ill, less-than-10-years-younger. These can still
      stretch. The planner assumes non-EDB (most common adult-child case).
    - Year-by-year required minimum distribution within the 10-year window
      when the original owner was already past RMD age (2024 IRS guidance).
      Even drain is the planning heuristic; user can mimic balloon-strategies
      manually by setting a 10-year inheritance and converting in low-MAGI years.
    - Years-1-9 single-life RMD floor (audit C10 / rmd-2): For a
      non-eligible designated beneficiary (NEDB) whose decedent died ON OR AFTER
      their Required Beginning Date (RBD), the 2024 final regulations (T.D. 10001,
      effective for distribution years >= 2025) require annual minimum
      distributions in years 1-9 of the 10-year window, based on the
      beneficiary's single life expectancy (IRS Single Life Table / Table I,
      Treas. Reg. §1.401(a)(9)-9) reduced by 1 each year. This model instead
      uses an even-drain (balance / years_remaining) that fully distributes by
      year 10 but does NOT enforce that years-1-9 floor. Effect: for an OLDER
      beneficiary (short life expectancy) the even-drain can under-distribute
      in the early years relative to the required RMD; younger beneficiaries
      are typically unaffected because the even-drain already exceeds the
      single-life RMD. If the decedent died BEFORE their RBD, no annual RMD is
      required in years 1-9 and the even-drain model is already correct.
    """

    balance: float
    inherited_year: int  # calendar year owner takes possession
    owner: Literal["you", "spouse"]
    growth_rate: float = 0.07

    @classmethod
    def from_dict(cls, data: dict) -> InheritedIRA | None:
        """Build from a session/upload dict, tolerating malformed entries.

        Returns None (skip, don't crash) when the entry lacks a positive balance
        or a usable owner, or carries a non-numeric year — a malformed upload_merge
        entry must not raise KeyError on every render (audit-0802 F9).
        """
        try:
            balance = float(data.get("balance", 0) or 0)
        except (TypeError, ValueError):
            return None
        if balance <= 0:
            return None
        owner = data.get("owner")
        if owner not in ("you", "spouse"):
            return None
        try:
            inherited_year = int(data["inherited_year"])
        except (KeyError, TypeError, ValueError):
            return None
        try:
            growth_rate = float(data.get("growth_rate", 0.07))
        except (TypeError, ValueError):
            growth_rate = 0.07
        return cls(
            balance=balance,
            inherited_year=inherited_year,
            owner=owner,
            growth_rate=growth_rate,
        )


@dataclass
class SurvivorScenario:
    """When one spouse dies during the projection.

    Survivor files Single from death_year + 1 onward (IRS allows MFJ for the
    year of death itself, treated as the last MFJ year). Deceased spouse's
    SS payments end, their IRA rolls into the survivor's IRA, their QCD
    allowance ends, only the survivor's senior bonus applies.

    MODELED:
    - SS survivor benefit step-up: survivor keeps max(your_ss, spouse_ss) each year
    """

    who_dies: Literal["you", "spouse"]
    death_year: int  # calendar year of death; survivor files Single from death_year + 1


@dataclass
class GrowthProfile:
    """Per-account growth rate with optional yield/appreciation split.

    default_rate: TOTAL annual return (yield + appreciation), e.g. 0.07 for 7%
    yearly_overrides: {year: total_rate} for years with known/historical returns
    yield_rate: dividend yield component (taxable brokerage only; 0.0 for IRAs)
    qualified_fraction: share of yield_rate that is qualified dividends (LTCG-rate)
    yield_overrides: {year: yield_rate} for known-yield years
    """

    default_rate: float = 0.07
    yearly_overrides: dict[int, float] = field(default_factory=dict)
    yield_rate: float = 0.0
    qualified_fraction: float = 1.0
    yield_overrides: dict[int, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Defensive bounds (audit 2026-07-13 growthprofile-bounds-1): an
        # out-of-range qualified_fraction (e.g. 1.5 from a bad dividend-forecast
        # blend) silently drives ordinary_div_for/qualified_div_for negative.
        # Clamp rather than raise, matching this module's existing convention
        # of silently correcting invalid inputs (see Household.__post_init__'s
        # RMD start-age correction below).
        self.qualified_fraction = max(0.0, min(1.0, self.qualified_fraction))
        self.yield_rate = max(0.0, self.yield_rate)

    def rate_for(self, year: int) -> float:
        return self.yearly_overrides.get(year, self.default_rate)

    def yield_for(self, year: int) -> float:
        return self.yield_overrides.get(year, self.yield_rate)

    def appreciation_for(self, year: int) -> float:
        return self.rate_for(year) - self.yield_for(year)

    def qualified_div_for(self, year: int, balance: float) -> float:
        return balance * self.yield_for(year) * self.qualified_fraction

    def ordinary_div_for(self, year: int, balance: float) -> float:
        return balance * self.yield_for(year) * (1.0 - self.qualified_fraction)


def project_price(base: float, base_year: int, growth: GrowthProfile, year: int) -> float:
    """Compound *base* forward from *base_year* to *year* using *growth*.

    Pure module-level extraction of ``Household.projected_txn_price``'s
    compounding loop so it can be reused with an overridable base (e.g. a
    freshly fetched-but-not-yet-committed live TXN quote on the exercise
    page) without a second, divergent implementation. Years at or before
    ``base_year`` return ``base`` unchanged; each subsequent year's rate is
    looked up via ``rate_for(y)`` so per-year overrides are honored.
    """
    if year <= base_year:
        return base
    price = base
    for y in range(base_year, year):
        price *= 1 + growth.rate_for(y)
    return price


@dataclass
class Household:
    """All inputs for the Roth conversion model."""

    # Ages (in base_year)
    your_age: int = _D["your_age"]
    spouse_age: int = _D["spouse_age"]
    your_has_workplace_plan: bool = _D["your_has_workplace_plan"]
    spouse_has_workplace_plan: bool = _D["spouse_has_workplace_plan"]
    base_year: int = 2026

    # IRA balances (beginning of base_year)
    your_ira: float = _D["your_ira"]
    spouse_ira: float = _D["spouse_ira"]
    your_roth: float = _D["your_roth"]
    spouse_roth: float = _D["spouse_roth"]

    # Social Security (monthly at FRA 67)
    your_ss_fra: float = _D["your_ss_fra"]  # $/month at FRA
    spouse_ss_fra: float = _D["spouse_ss_fra"]
    ss_start_age: int = 70  # DEPRECATED — use your_ss_start_age / spouse_ss_start_age
    your_ss_start_age: int = 70  # age you begin claiming SS (62–70)
    spouse_ss_start_age: int = 70  # age spouse begins claiming SS (62–70)
    ss_cola: float = 0.025  # 2.5% annual COLA

    # Growth & inflation
    growth_rate: float = 0.07  # legacy flat rate (used as default for all accounts)
    expense_inflation: float = 0.03

    # Per-account growth profiles (None = use growth_rate as default)
    your_ira_growth: GrowthProfile | None = None
    spouse_ira_growth: GrowthProfile | None = None
    brokerage_growth: GrowthProfile | None = None
    your_roth_growth: GrowthProfile | None = None
    spouse_roth_growth: GrowthProfile | None = None

    # Living expenses (annual, today's dollars)
    living_expenses: float = _D["living_expenses"]

    # Tax parameters (2026 TCJA/OBBBA permanent)
    std_deduction: float = 32_200  # MFJ
    senior_extra: float = 1_650  # per person 65+
    filing_status: str = "MFJ"

    # Brokerage assumptions
    brokerage_start: float = 0.0  # beginning-of-base-year taxable brokerage balance
    brok_turnover: float = 0.30  # 30% annual turnover
    ltcg_rate: float = 0.15

    # Stock option grants
    # audit-0721 C23: deep-copy (not list(...)) — the shallow copy shared the
    # contained StockGrant instances by identity across every
    # default-constructed Household, so in-place mutation of one household's
    # grant would leak into all the others.
    grants: list[StockGrant] = field(default_factory=lambda: copy.deepcopy(_D["grants"]))
    txn_price_now: float = _D["stock_price_now"]  # current stock price
    txn_price_late: float = _D["stock_price_late"]  # projected price at expiry
    # Growth profile for projecting txn_price_now forward to future exercise
    # years (default 7%, matching the other account growth defaults). Always
    # present (not Optional) so legacy/loaded households get a real projection
    # rather than a flat price by omission.
    txn_price_growth: GrowthProfile = field(
        default_factory=lambda: GrowthProfile(default_rate=0.07)
    )

    # Per-grant/per-year exercise decision. None (or empty) falls back to
    # default_from_legacy(), which reproduces the old early-exercise output
    # (see effective_schedule() / option_income() below).
    exercise_schedule: ExerciseSchedule | None = None

    # FRA (Full Retirement Age for SS benefit calculation)
    your_fra_age: int = 67  # 67 for 1960+ cohort; 66 or 66+N/12 for earlier cohorts
    spouse_fra_age: int = 67  # 67 for 1960+ cohort; 66 or 66+N/12 for earlier cohorts

    # RMD
    your_rmd_start_age: int = (
        75  # SECURE 2.0 default; 1951-1959 cohort uses 73 per IRC §401(a)(9)(C)(v)(I)
    )
    spouse_rmd_start_age: int = (
        75  # SECURE 2.0 default; 1951-1959 cohort uses 73 per IRC §401(a)(9)(C)(v)(I)
    )

    your_defer_first_rmd: bool = False  # IRC §401(a)(9)(C)(ii): defer first RMD to April 1 of following year (two RMDs land in year 2)
    spouse_defer_first_rmd: bool = False  # IRC §401(a)(9)(C)(ii): defer spouse's first RMD likewise

    # M3 (audit-0720): household-level (not per-person) toggle. When True AND
    # the beneficiary spouse is more than 10 years younger than the IRA owner,
    # RMDs use the IRS Joint & Last Survivor Table (Table II) instead of the
    # Uniform Lifetime Table (Table III) — see engine/ira.py rmd_divisor().
    # Default False preserves today's Table-III-only behavior exactly.
    spouse_is_sole_beneficiary: bool = _D["spouse_is_sole_beneficiary"]

    # Healthcare coverage
    your_aca_enrolled: bool = False  # you on ACA marketplace (vs employer plan)
    spouse_aca_enrolled: bool = False  # spouse on ACA marketplace
    aca_benchmark_premium_annual: float = 21_600.0  # 2nd-lowest-cost Silver plan annual cost (household-level; varies by state/county/age)
    aca_enhanced_subsidies_active: bool = False  # law toggle for sensitivity analysis: True = ARP/IRA-style enhanced subsidies; False = current law (ARP expired Dec 31, 2025)
    advance_aptc_annual: float = 0.0  # Annual APTC pre-paid by IRS to your insurer based on projected MAGI; reconciled on Form 8962 at year-end. Set to 0 if not enrolled in marketplace insurance or pay full premium upfront.
    medicare_part_b_base_monthly: float = 202.90  # standard Part B monthly premium (CMS-published); IRMAA surcharge is computed on top

    # CPI indexing assumption for tax constants (brackets, IRMAA tiers, FPL, etc.)
    # Default 0.0 = no indexing (2026 base values frozen). Set to 0.025 in the UI
    # via session_state seed to enable 2.5% annual inflation for live projections.
    cpi_assumption: float = 0.0

    # IRMAA lookback anchor
    prior_year_magi: dict[int, float] = field(default_factory=dict)
    """Year-keyed sparse map of FILED MAGI values for IRMAA anchor.

    Overrides the engine's projected MAGI for the first 2 projection years
    (IRMAA has 2-year lookback). E.g., {2024: 285000.0, 2025: 290000.0}
    locks in 2026 + 2027 IRMAA from actual filed values. Years not in the
    map fall through to magi_history (projection-year accumulated) or
    to a same-year approximation."""

    # QCD
    qcd_limit: float = 111_000  # 2026 annual limit per person (inflation-indexed)

    # Survivor scenario (optional sensitivity analysis)
    survivor: SurvivorScenario | None = None
    """Optional survivor scenario for sensitivity analysis. When set, the
    projection switches the surviving spouse to single-filer status starting
    death_year + 1, transfers the deceased's IRA to the survivor (spousal
    rollover), zeroes deceased's SS, and uses single-filer tax brackets,
    std deduction, and senior bonus.

    Default None = baseline MFJ projection where both spouses survive to end_age."""

    inherited_iras: list[InheritedIRA] = field(default_factory=list)
    """List of non-spousal inherited IRAs subject to the 10-year rule.

    Empty list (default) = no inherited IRAs in the plan.
    Multiple entries supported (e.g., user inherits from a parent in 2027 AND
    spouse inherits from a sibling in 2030).
    See InheritedIRA docstring for drain formula and scope."""

    @property
    def age_gap(self) -> int:
        return self.your_age - self.spouse_age

    @property
    def your_conv_window(self) -> int:
        """Years you can convert (age now through your_rmd_start_age - 1)."""
        return max(self.your_rmd_start_age - 1 - self.your_age + 1, 0)

    @property
    def spouse_conv_window(self) -> int:
        """Years spouse can convert (age now through spouse_rmd_start_age - 1)."""
        return max(self.spouse_rmd_start_age - 1 - self.spouse_age + 1, 0)

    def your_age_in(self, year: int) -> int:
        return self.your_age + (year - self.base_year)

    def spouse_age_in(self, year: int) -> int:
        return self.spouse_age + (year - self.base_year)

    def your_ss_at_70(self) -> float:
        """Annual SS benefit at the configured claim age, capped at age 70.

        DRC stops accruing at 70; claiming later yields no additional credit.
        Delegates to the canonical engine formula (monthly reduction/DRC schedule).
        """
        from engine.ira import ss_benefit_at_age

        effective_age = min(self.your_ss_start_age, 70)
        return ss_benefit_at_age(self.your_ss_fra, effective_age, self.your_fra_age)

    def spouse_ss_at_70(self) -> float:
        """Annual SS benefit at the configured claim age, capped at age 70.

        DRC stops accruing at 70; claiming later yields no additional credit.
        Delegates to the canonical engine formula (monthly reduction/DRC schedule).
        """
        from engine.ira import ss_benefit_at_age

        effective_age = min(self.spouse_ss_start_age, 70)
        return ss_benefit_at_age(self.spouse_ss_fra, effective_age, self.spouse_fra_age)

    def your_ira_rate(self, year: int) -> float:
        """Growth rate for your IRA in a given year."""
        if self.your_ira_growth is not None:
            return self.your_ira_growth.rate_for(year)
        return self.growth_rate

    def spouse_ira_rate(self, year: int) -> float:
        """Growth rate for spouse's IRA in a given year."""
        if self.spouse_ira_growth is not None:
            return self.spouse_ira_growth.rate_for(year)
        return self.growth_rate

    def your_roth_rate(self, year: int) -> float:
        """Growth rate for your Roth IRA in a given year."""
        if self.your_roth_growth is not None:
            return self.your_roth_growth.rate_for(year)
        return self.growth_rate

    def spouse_roth_rate(self, year: int) -> float:
        """Growth rate for spouse's Roth IRA in a given year."""
        if self.spouse_roth_growth is not None:
            return self.spouse_roth_growth.rate_for(year)
        return self.growth_rate

    def brokerage_rate(self, year: int) -> float:
        """Growth rate for brokerage in a given year."""
        if self.brokerage_growth is not None:
            return self.brokerage_growth.rate_for(year)
        return self.growth_rate

    def projected_txn_price(self, year: int) -> float:
        """TXN price projected forward from ``txn_price_now`` (as of
        ``base_year``) to ``year`` using ``txn_price_growth``.

        Mirrors the year-by-year balance-compounding convention used
        elsewhere (e.g. ``yr.your_ira_end = balance * (1 + hh.your_ira_rate(year))``
        in engine/scenario.py): each year's rate — looked up via
        ``rate_for(y)`` so per-year overrides are honored — grows the price
        from the start of year ``y`` to the start of year ``y + 1``. Years at
        or before ``base_year`` return ``txn_price_now`` unchanged.
        """
        return project_price(self.txn_price_now, self.base_year, self.txn_price_growth, year)

    def __post_init__(self) -> None:
        # Derive statutory RMD start age from birth year unless already set to the valid
        # 1951-1959 cohort value (73). The default (75) acts as a sentinel that triggers
        # derivation, and any invalid in-between value (e.g. 74, reachable via old JSON seeds)
        # is also corrected. After derivation the result is always 73 or 75.
        # SECURE 2.0 §107 / IRC §401(a)(9)(C)(v): born 1951-1959 → 73; born 1960+ → 75.
        # Cohort is derived from (base_year - age); a manual override for miscategorized
        # users is available via the RMD-start-age selectbox in views/setup/parameters.py.
        if self.your_rmd_start_age != 73:
            # 73 is the only stable explicit choice (1951-1959 cohort); derive for all else.
            self.your_rmd_start_age = default_rmd_age(self.base_year - self.your_age)
        if self.spouse_rmd_start_age != 73:
            self.spouse_rmd_start_age = default_rmd_age(self.base_year - self.spouse_age)

    def effective_schedule(self) -> ExerciseSchedule:
        """The stored per-grant/per-year exercise schedule, or a synthesized
        default that reproduces the historical early-exercise behavior when
        none is stored (or the stored one has no entries).

        default_at_expiry places each grant's full shares in its expiry_year
        (recomputed fresh from self.grants on every call, never cached), so
        it stays correct regardless of FinExtract list reordering/compaction
        (audit 2026-07-13 household-grant-match-1 / PR #369).

        Each expiry-year's price is ``self.projected_txn_price(year)`` (grown
        forward from ``txn_price_now`` at ``txn_price_growth``), not a flat
        current price, so the baseline plan (scenario.py) values future-year
        exercises identically to the exercise page and optimizer.
        """
        if self.exercise_schedule is not None and not self.exercise_schedule.is_empty():
            # Migrate any legacy year:strike fallback keys (pre audit-0720 H10
            # expiry-year enrichment) so they keep matching self.grants.
            self.exercise_schedule.migrate_keys(self.grants)
            return self.exercise_schedule
        return ExerciseSchedule.default_at_expiry(
            self.grants, self.base_year, self.txn_price_now, self.projected_txn_price
        )

    def option_income(self, year: int) -> float:
        """Ordinary income from option exercises scheduled in ``year``.

        Sourced solely from ``effective_schedule()`` — see models/exercise_schedule.py.
        A per-year price missing from the stored schedule (e.g. an unedited
        default persisted with an empty ``price_by_year`` -- see the save-side
        drop-filter in views/option_exercise.py) is re-projected via
        ``projected_txn_price`` rather than falling back to 0.0 (audit-0722b).
        """
        return self.effective_schedule().income_for(year, self.grants, self.projected_txn_price)
