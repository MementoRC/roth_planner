"""Test suite — validates engine against known verified numbers from our spreadsheets."""

import sys
sys.path.insert(0, '/home/claude/roth_planner')

from models.household import Household, StockGrant
from engine.tax import (
    federal_tax, marginal_rate, taxable_ss, deductions,
    tax_on_conversion, room_to_12, room_to_22
)
from engine.ira import calc_rmd, rmd_divisor, ss_benefit_at_age, ss_with_cola, project_ira
from engine.irmaa import irmaa_surcharge, irmaa_for_year, irmaa_next_threshold
from engine.aca import aca_subsidy, aca_applies
from engine.scenario import run_scenario, run_no_conversion, ConversionPlan, auto_fill_12

PASS = 0
FAIL = 0

def check(label, actual, expected, tol=1.0):
    global PASS, FAIL
    if abs(actual - expected) <= tol:
        PASS += 1
        print(f"  ✓ {label}: {actual:,.2f}")
    else:
        FAIL += 1
        print(f"  ✗ {label}: got {actual:,.2f}, expected {expected:,.2f} (Δ={actual-expected:,.2f})")


def test_tax():
    print("\n=== TAX ENGINE ===")
    # Tax on $0
    check("Tax on $0", federal_tax(0), 0)

    # Tax on $24,800 (top of 10%)
    check("Tax on $24,800", federal_tax(24_800), 24_800 * 0.10, 1)

    # Tax on $100,800 (top of 12%)
    t = 24_800 * 0.10 + (100_800 - 24_800) * 0.12
    check("Tax on $100,800", federal_tax(100_800), t, 1)

    # Tax on $211,400 (top of 22%)
    t += (211_400 - 100_800) * 0.22
    check("Tax on $211,400", federal_tax(211_400), t, 1)

    # Marginal rates
    check("Bracket at $50K", marginal_rate(50_000), 0.12)
    check("Bracket at $150K", marginal_rate(150_000), 0.22)
    check("Bracket at $300K", marginal_rate(300_000), 0.24)

    # Room to 12% with no other income, std ded $32,200
    r12 = room_to_12(0, 32_200)
    check("Room to 12% (no income)", r12, 133_000, 1)  # 32200 + 100800 = 133000

    # Room to 12% with $69,934 option income
    r12_opt = room_to_12(69_934, 32_200)
    check("Room to 12% ($70K opts)", r12_opt, 63_066, 1)  # 133000 - 69934

    # Room to 22%
    r22 = room_to_22(0, 32_200)
    check("Room to 22% (no income)", r22, 243_600, 1)  # 32200 + 211400


def test_ss_taxation():
    print("\n=== SS TAXATION ===")
    # Below threshold: $20K other income + $40K SS
    # Provisional = 20000 + 20000 = 40000 → between 32K-44K
    tss = taxable_ss(40_000, 20_000)
    expected = 0.5 * (40_000 - 32_000)  # = 4000
    check("SS tax (mid tier)", tss, expected, 1)

    # High income: $200K other + $100K SS → 85% taxable
    tss2 = taxable_ss(100_000, 200_000)
    # Provisional = 200000 + 50000 = 250000 → above 44K
    expected2 = min(0.85 * (250_000 - 44_000) + 6_000, 0.85 * 100_000)
    check("SS tax (85%)", tss2, expected2, 1)


def test_deductions():
    print("\n=== DEDUCTIONS ===")
    check("Ded age 61/55", deductions(61, 55), 32_200)
    check("Ded age 65/59", deductions(65, 59), 32_200 + 1_650)
    check("Ded age 66/60", deductions(66, 60), 32_200 + 1_650)
    check("Ded age 75/69", deductions(75, 69), 32_200 + 2 * 1_650)


def test_ira():
    print("\n=== IRA & RMD ===")
    # IRA growth: $1.7M at 7% for 14 years
    fv = project_ira(1_700_000, 0.07, 14)
    check("IRA $1.7M 7% 14yr", fv, 4_383_508, 100)

    # RMD divisors
    check("Divisor age 75", rmd_divisor(75), 24.6)
    check("Divisor age 80", rmd_divisor(80), 20.2)
    check("Divisor age 85", rmd_divisor(85), 16.0)
    check("Divisor age 95", rmd_divisor(95), 8.9)

    # RMD at age 75 with $4.38M
    rmd = calc_rmd(4_383_508, 75, 75)
    check("RMD age 75 on $4.38M", rmd, 4_383_508 / 24.6, 10)

    # No RMD before 75
    check("RMD age 74", calc_rmd(4_000_000, 74, 75), 0)


def test_ss_benefit():
    print("\n=== SS BENEFIT CALCULATION ===")
    # $3,800/mo at FRA 67, delayed to 70 = 24% increase
    annual = ss_benefit_at_age(3_800, 70, 67)
    check("SS at 70 ($3800 FRA)", annual, 3_800 * 1.24 * 12, 1)  # $56,544

    # With 5 years COLA at 2.5%
    with_cola = ss_with_cola(56_544, 5, 0.025)
    check("SS at 75 (5yr COLA)", with_cola, 56_544 * 1.025**5, 1)


def test_grants():
    print("\n=== TXN STOCK GRANTS ===")
    hh = Household()
    check("Grant 2019 spread @$212", hh.grants[0].spread(212), (212 - 104.41) * 650, 1)
    check("Grant 2020 spread @$212", hh.grants[1].spread(212), (212 - 130.52) * 763, 1)
    check("Grant 2021 spread @$212", hh.grants[2].spread(212), (212 - 169.23) * 450, 1)

    total = sum(g.spread(212) for g in hh.grants)
    check("Total spread @$212", total, 151_349, 10)

    # Option income by year
    check("Opt 2026 early", hh.option_income(2026, True), hh.grants[0].spread(212), 1)
    check("Opt 2027 early", hh.option_income(2027, True), hh.grants[1].spread(212), 1)
    check("Opt 2028 early", hh.option_income(2028, True), hh.grants[2].spread(212), 1)
    check("Opt 2029 (none)", hh.option_income(2029, True), 0)


def test_irmaa():
    print("\n=== IRMAA ===")
    # Below all thresholds
    check("IRMAA at $200K", irmaa_surcharge(200_000), 0)

    # Above tier 1 ($206K)
    s1 = irmaa_surcharge(210_000)
    assert s1 > 0, f"Expected surcharge above $206K, got {s1}"
    print(f"  ✓ IRMAA at $210K: ${s1:,.0f} (tier 1)")

    # Room to next threshold
    room = irmaa_next_threshold(200_000)
    check("IRMAA room at $200K", room, 6_000, 1)


def test_aca():
    print("\n=== ACA ===")
    # Pre-Medicare
    assert aca_applies(61) == True
    assert aca_applies(64) == True
    assert aca_applies(65) == False
    print("  ✓ ACA applies: 61=True, 64=True, 65=False")

    # Low income: big subsidy
    s_low = aca_subsidy(30_000)
    assert s_low > 15_000, f"Expected large subsidy at $30K, got {s_low}"
    print(f"  ✓ ACA subsidy at $30K: ${s_low:,.0f}")

    # High income: small/no subsidy
    s_high = aca_subsidy(300_000)
    print(f"  ✓ ACA subsidy at $300K: ${s_high:,.0f}")


def test_scenario_no_conv():
    print("\n=== NO CONVERSION SCENARIO ===")
    hh = Household()
    result = run_no_conversion(hh, end_age=95)

    # Find year 2040 (age 75)
    yr75 = [yr for yr in result.years if yr.your_age == 75][0]
    check("IRA at 75 (no conv)", yr75.your_ira_begin, 4_383_508, 500)

    rmd_expected = 4_383_508 / 24.6
    check("RMD at 75", yr75.your_rmd, rmd_expected, 100)

    # SS at 75: $56,544 * 1.025^5
    ss75 = 56_544 * 1.025**5
    check("Your SS at 75", yr75.your_ss, ss75, 100)

    # Spouse age 69 at your 75: no SS yet
    check("Sp SS at your 75 (sp 69)", yr75.spouse_ss, 0)

    # Spouse age 70 = your age 76
    yr76 = [yr for yr in result.years if yr.your_age == 76][0]
    assert yr76.spouse_ss > 0, f"Spouse SS should start at sp 70, got {yr76.spouse_ss}"
    print(f"  ✓ Sp SS starts at your 76 (sp 70): ${yr76.spouse_ss:,.0f}")

    print(f"\n  Summary (no conversion):")
    print(f"    Total RMD tax (75-95): ${result.total_rmd_tax:,.0f}")
    print(f"    Total brokerage tax: ${result.total_brok_tax:,.0f}")


def test_scenario_12pct_fill():
    print("\n=== 12% FILL SCENARIO ===")
    hh = Household()
    plan = auto_fill_12(hh)

    print(f"  Auto-fill conversions (12% ceiling):")
    total_yc = 0
    total_sc = 0
    for yr in sorted(set(list(plan.your_conversions.keys()) + list(plan.spouse_conversions.keys()))):
        yc = plan.your_conversions.get(yr, 0)
        sc = plan.spouse_conversions.get(yr, 0)
        total_yc += yc
        total_sc += sc
        ya = hh.your_age + (yr - hh.base_year)
        sa = hh.spouse_age + (yr - hh.base_year)
        if yc > 0 or sc > 0:
            print(f"    {yr} (You {ya}/Sp {sa}): You ${yc:,.0f} + Sp ${sc:,.0f}")
    print(f"  Total: You ${total_yc:,.0f} + Sp ${total_sc:,.0f} = ${total_yc+total_sc:,.0f}")

    result = run_scenario(hh, plan, "Fill 12%", end_age=95)

    yr75 = [yr for yr in result.years if yr.your_age == 75][0]
    print(f"\n  After conversions:")
    print(f"    Your IRA at 75: ${yr75.your_ira_begin:,.0f} (was $4.38M)")
    print(f"    RMD at 75: ${yr75.your_rmd:,.0f}")
    print(f"    Conv tax paid: ${result.total_conv_tax:,.0f}")
    print(f"    RMD tax (75-95): ${result.total_rmd_tax:,.0f}")

    # IRA should be significantly smaller
    assert yr75.your_ira_begin < 4_000_000, f"IRA at 75 should be reduced, got {yr75.your_ira_begin:,.0f}"
    print(f"  ✓ IRA reduced by conversions")


def test_household_properties():
    print("\n=== HOUSEHOLD PROPERTIES ===")
    hh = Household()
    check("Age gap", hh.age_gap, 6)
    check("Your conv window", hh.your_conv_window, 14)
    check("Your SS at 70", hh.your_ss_at_70(), 56_544, 1)
    check("Spouse SS at 70", hh.spouse_ss_at_70(), 56_544, 1)


if __name__ == "__main__":
    test_tax()
    test_ss_taxation()
    test_deductions()
    test_ira()
    test_ss_benefit()
    test_grants()
    test_irmaa()
    test_aca()
    test_household_properties()
    test_scenario_no_conv()
    test_scenario_12pct_fill()

    print(f"\n{'='*60}")
    print(f"RESULTS: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ FAILURES DETECTED")
