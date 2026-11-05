Feature: Medicare Part B income-related premium (IRMAA) and its 2-year lookback

  # Source: CMS notice "Medicare Program; Medicare Part B Monthly Actuarial Rates, Premium Rates, and Annual Deductible Beginning January 1, 2026", 90 FR (Nov 19, 2025), doc 2025-20251, section II.A table. https://www.govinfo.gov/content/pkg/FR-2025-11-19/html/2025-20251.htm
  # Evidence: (b) table lookup. Single row "> $137,000 and <= $171,000": IRMAA $202.90, total $405.80. Joint row "> $274,000 and <= $342,000": same amounts. Joint figures are per person. $109,001 assumes whole-dollar MAGI.
  Scenario Outline: 2026 Part B premium tier from MAGI
    Given a Medicare beneficiary filing <status> with 2024 MAGI of <magi>
    When the 2026 Part B premium is determined
    Then the income-related monthly adjustment is <irmaa>
    And the total monthly Part B premium is <total>

    Examples:
      | status                 | magi     | irmaa   | total   |
      | single                 | $109,000 | $0.00   | $202.90 |
      | single                 | $109,001 | $81.20  | $284.10 |
      | single                 | $150,000 | $202.90 | $405.80 |
      | married filing jointly | $300,000 | $202.90 | $405.80 |
      | married filing jointly | $410,000 | $324.60 | $527.50 |

  # Source: SSA POMS HI 01101.010 (12/02/2025), A.2: "2026 premiums are generally based on 2024 tax return information." A.1: MAGI = AGI (Form 1040 line 11) + tax-exempt interest (line 2a). https://secure.ssa.gov/poms.nsf/lnx/0601101010
  # Evidence: (b) rule + table. 2024 MAGI $150,000 falls in the single row > $137,000 and <= $171,000, giving $405.80.
  Scenario: The 2026 premium follows 2024 income, not 2026 income
    Given a single Medicare beneficiary with 2024 MAGI of $150,000
    And 2026 MAGI of $90,000
    When the 2026 Part B premium is determined
    Then the total monthly Part B premium is $405.80
