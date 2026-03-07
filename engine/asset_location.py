"""Asset location analysis — equity-first vs proportional vs bond-first conversion."""

from __future__ import annotations

from dataclasses import dataclass

from engine.ira import calc_rmd
from models.household import Household


@dataclass
class AssetLocationYear:
    """Single year in the asset location projection."""

    year: int
    your_age: int

    # IRA composition (beginning of year)
    ira_equity: float = 0.0
    ira_bond: float = 0.0
    ira_total: float = 0.0

    # Roth composition (beginning of year)
    roth_equity: float = 0.0
    roth_bond: float = 0.0
    roth_total: float = 0.0

    # Flows
    conversion: float = 0.0
    conv_equity: float = 0.0  # equity portion converted
    conv_bond: float = 0.0  # bond portion converted
    rmd: float = 0.0

    # End of year
    ira_total_end: float = 0.0
    roth_total_end: float = 0.0

    # IRA blended growth rate (weighted)
    ira_growth_rate: float = 0.0


@dataclass
class AssetLocationResult:
    """Full projection for one conversion strategy."""

    name: str
    years: list[AssetLocationYear]
    total_converted: float = 0.0
    ira_at_75: float = 0.0
    ira_at_85: float = 0.0
    rmd_at_75: float = 0.0
    rmd_at_85: float = 0.0
    ira_growth_at_75: float = 0.0  # blended growth rate at 75


def project_asset_location(
    hh: Household,
    annual_conversions: dict[int, float],
    equity_pct: float = 0.60,
    equity_return: float = 0.09,
    bond_return: float = 0.04,
    strategy: str = "equity_first",
    end_age: int = 95,
) -> AssetLocationResult:
    """
    Project IRA/Roth with asset-class-aware conversions.

    strategy:
        "equity_first" — convert equities before bonds (maximize Roth growth)
        "proportional" — convert in proportion to current allocation
        "bond_first" — convert bonds before equities (minimize Roth growth)
    """
    # Split initial IRA into equity/bond
    total_ira = hh.your_ira + hh.spouse_ira
    ira_eq = total_ira * equity_pct
    ira_bd = total_ira * (1 - equity_pct)
    roth_eq = 0.0
    roth_bd = 0.0

    years = []
    total_conv = 0.0

    for yr_idx in range(end_age - hh.your_age + 1):
        year = hh.base_year + yr_idx
        ya = hh.your_age + yr_idx
        ira_total = ira_eq + ira_bd

        yr = AssetLocationYear(
            year=year,
            your_age=ya,
            ira_equity=ira_eq,
            ira_bond=ira_bd,
            ira_total=ira_total,
            roth_equity=roth_eq,
            roth_bond=roth_bd,
            roth_total=roth_eq + roth_bd,
        )

        # Blended IRA growth rate
        if ira_total > 0:
            yr.ira_growth_rate = (ira_eq * equity_return + ira_bd * bond_return) / ira_total
        else:
            yr.ira_growth_rate = 0.0

        # Conversion
        conv = annual_conversions.get(year, 0.0)
        conv = min(conv, ira_total)
        yr.conversion = conv
        total_conv += conv

        # Allocate conversion by strategy
        conv_eq, conv_bd = _allocate_conversion(
            conv, ira_eq, ira_bd, strategy
        )
        yr.conv_equity = conv_eq
        yr.conv_bond = conv_bd

        # RMD (proportional withdrawal from both asset classes)
        rmd = calc_rmd(ira_total, ya, hh.rmd_start_age)
        yr.rmd = rmd

        # RMD is always proportional to current allocation
        if ira_total > 0:
            rmd_eq = rmd * (ira_eq / ira_total)
            rmd_bd = rmd * (ira_bd / ira_total)
        else:
            rmd_eq = rmd_bd = 0.0

        # Update IRA after withdrawals
        ira_eq = max(ira_eq - conv_eq - rmd_eq, 0)
        ira_bd = max(ira_bd - conv_bd - rmd_bd, 0)

        # Grow IRA
        ira_eq *= (1 + equity_return)
        ira_bd *= (1 + bond_return)

        # Update Roth (conversions flow in, then grow)
        roth_eq = (roth_eq + conv_eq) * (1 + equity_return)
        roth_bd = (roth_bd + conv_bd) * (1 + bond_return)

        yr.ira_total_end = ira_eq + ira_bd
        yr.roth_total_end = roth_eq + roth_bd

        years.append(yr)

    # Extract milestones
    def _at_age(age: int):
        return next((y for y in years if y.your_age == age), None)

    y75 = _at_age(75)
    y85 = _at_age(85)

    return AssetLocationResult(
        name=strategy.replace("_", " ").title(),
        years=years,
        total_converted=total_conv,
        ira_at_75=y75.ira_total if y75 else 0,
        ira_at_85=y85.ira_total if y85 else 0,
        rmd_at_75=y75.rmd if y75 else 0,
        rmd_at_85=y85.rmd if y85 else 0,
        ira_growth_at_75=y75.ira_growth_rate if y75 else 0,
    )


def _allocate_conversion(
    amount: float, ira_eq: float, ira_bd: float, strategy: str
) -> tuple[float, float]:
    """Split conversion amount between equity and bond portions."""
    total = ira_eq + ira_bd
    if total <= 0 or amount <= 0:
        return 0.0, 0.0

    if strategy == "equity_first":
        eq = min(amount, ira_eq)
        bd = min(amount - eq, ira_bd)
    elif strategy == "bond_first":
        bd = min(amount, ira_bd)
        eq = min(amount - bd, ira_eq)
    else:  # proportional
        eq = amount * (ira_eq / total)
        bd = amount * (ira_bd / total)

    return eq, bd
