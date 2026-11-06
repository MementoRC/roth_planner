Feature: Taxable part of Social Security benefits

  # Source: IRS Publication 915 (2025, for 2025 returns), Worksheet 1 "Figuring Your Taxable Benefits", lines 1-19 (line 9 base $32,000 MFJ / $25,000 single; line 11 $12,000 MFJ / $9,000 single; line 16 85%; line 18 cap at 85% of benefits). https://www.irs.gov/publications/p915
  # Evidence: (b) rule + arithmetic. These are fixed statutory amounts (IRC 86(c)), not inflation-indexed, so the 2025 worksheet applies to 2026. Provisional income = line 3 (pension/IRA income here) + line 4 (tax-exempt interest) + line 2 (half of benefits). No line 5 or line 7 items.
  # Arithmetic (row 1): 15,000 + 0 + 15,000 = 30,000 <= 32,000 -> none taxable.
  #   (row 2): 30,000 + 12,000 = 42,000; line 10 = 10,000; line 12 = 0; line 13 = 10,000; line 14 = 5,000; line 15 = min(12,000, 5,000) = 5,000; line 16 = 0; line 17 = 5,000; line 18 = 20,400 -> 5,000.
  #   (row 3): 50,000 + 20,000 = 70,000; line 10 = 38,000; line 12 = 26,000; line 15 = min(20,000, 6,000) = 6,000; line 16 = 22,100; line 17 = 28,100; line 18 = 34,000 -> 28,100.
  #   (row 4): 100,000 + 20,000 = 120,000; line 10 = 88,000; line 12 = 76,000; line 16 = 64,600; line 17 = 70,600; line 18 = 34,000 -> 34,000 (the 85% cap).
  #   (row 5): 25,000 + 5,000 + 12,000 = 42,000, the same as row 2 -> 5,000. Without the tax-exempt interest it would be 37,000 -> 2,500.
  Scenario Outline: Married couple filing jointly
    Given a married-filing-jointly household with 2026 Social Security benefits of <benefits>
    And other taxable income of <other> and tax-exempt interest of <exempt>
    When the taxable part of their 2026 benefits is figured
    Then <taxable> of the benefits is taxable

    Examples:
      | benefits | other    | exempt | taxable |
      | $30,000  | $15,000  | $0     | $0      |
      | $24,000  | $30,000  | $0     | $5,000  |
      | $40,000  | $50,000  | $0     | $28,100 |
      | $40,000  | $100,000 | $0     | $34,000 |
      | $24,000  | $25,000  | $5,000 | $5,000  |

  # Source: IRS Publication 915 (2025), "Examples", Example 1 (single filer: pension $18,600, wages $9,400, taxable interest $990, net benefits $5,980) and its filled-in Worksheet 1. https://www.irs.gov/publications/p915
  # Evidence: (a) worked example. Worksheet line 19 = $2,990.
  # Arithmetic: 28,990 + 2,990 = 31,980; line 10 = 6,980; line 12 = 0; line 13 = 6,980; line 14 = 3,490; line 15 = min(2,990, 3,490) = 2,990; line 18 = 5,083 -> 2,990.
  Scenario: Single filer from the publication's worked example
    Given a single filer with 2026 Social Security benefits of $5,980
    And other taxable income of $28,990 and tax-exempt interest of $0
    When the taxable part of their 2026 benefits is figured
    Then $2,990 of the benefits is taxable

  # Source: as above, Worksheet 1 with the single-filer amounts (line 9 $25,000, line 11 $9,000).
  # Evidence: (b) rule + arithmetic.
  # Arithmetic: 40,000 + 10,000 = 50,000; line 10 = 25,000; line 12 = 16,000; line 13 = 9,000; line 14 = 4,500; line 15 = min(10,000, 4,500) = 4,500;
  #             line 16 = 13,600; line 17 = 18,100; line 18 = 17,000 -> 17,000 (the 85% cap).
  Scenario: Single filer whose taxable benefits are capped at 85%
    Given a single filer with 2026 Social Security benefits of $20,000
    And other taxable income of $40,000 and tax-exempt interest of $0
    When the taxable part of their 2026 benefits is figured
    Then $17,000 of the benefits is taxable
