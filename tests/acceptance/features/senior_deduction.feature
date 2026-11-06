Feature: Enhanced deduction for seniors (2025-2028)

  # Source: IRS Schedule 1-A (Form 1040) 2025, Part V "Enhanced Deduction for Seniors", lines 31-37, and Part I lines 1-3 (MAGI = Form 1040 line 11b AGI plus excluded Puerto Rico income and Form 2555 / 4563 exclusions). https://www.irs.gov/pub/irs-pdf/f1040s1a.pdf
  #         IRS Publication 6142 "Additional Deduction for Seniors": 2025 through 2028, age 65 or older by the last day of the tax year, joint return required if married. https://www.irs.gov/pub/irs-pdf/p6142.pdf
  # Evidence: (b) rule + arithmetic. The $6,000 and the $75,000 / $150,000 thresholds are not indexed, so the 2025 form applies to 2026 (its "born before January 2, 1961" becomes 65 or older at the end of 2026).
  #           Per the form, the 6% reduction is figured ONCE from the return's MAGI (lines 33-35) and the reduced amount is entered SEPARATELY for each qualifying spouse (lines 36a, 36b), so both spouses' $6,000 shrink by the same amount.
  #           No foreign or Puerto Rico income, so MAGI = AGI.
  # Arithmetic (row 1): 120,000 <= 150,000 -> 6,000 for the one spouse 65+; total 6,000.
  #   (row 2): (180,000 - 150,000) x 6% = 1,800; 6,000 - 1,800 = 4,200 each; total 8,400.
  #   (row 3): (250,000 - 150,000) x 6% = 6,000; 6,000 - 6,000 = 0 each; total 0.
  #   (row 4): neither spouse is 65+, so lines 36a and 36b are blank; total 0.
  Scenario Outline: Married couple filing jointly
    Given a married-filing-jointly household where <seniors> of the two spouses are 65 or older at the end of 2026
    And their 2026 modified adjusted gross income is <magi>
    When their 2026 enhanced deduction for seniors is figured
    Then the deduction for each qualifying spouse is <each>
    And the total enhanced deduction for seniors is <total>

    Examples:
      | seniors | magi     | each   | total  |
      | one     | $120,000 | $6,000 | $6,000 |
      | both    | $180,000 | $4,200 | $8,400 |
      | both    | $250,000 | $0     | $0     |
      | neither | $100,000 | $0     | $0     |

  # Source: as above, Part V with the single-filer threshold (line 32 $75,000).
  # Evidence: (b) rule + arithmetic.
  # Arithmetic: (90,000 - 75,000) x 6% = 900; 6,000 - 900 = 5,100.
  Scenario: Single filer partly phased out
    Given a single filer who is 65 or older at the end of 2026
    And their 2026 modified adjusted gross income is $90,000
    When their 2026 enhanced deduction for seniors is figured
    Then the total enhanced deduction for seniors is $5,100
