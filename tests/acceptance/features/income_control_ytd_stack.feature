Feature: Income Control stack reconciles with the projection's taxed income

  # Every household is a single filer with a traditional IRA, no Social Security,
  # no brokerage account and no living expenses to fund. "This year's actuals"
  # are the year-to-date wages and conversions already done.

  # Source: Income Control design, docs/superpowers/specs/2026-11-14-income-control-slice2-ytd-design.md ("Totals guard"); 26 U.S.C. 61(a) (gross income from all sources: wages, and income from conversions of a traditional IRA, 26 U.S.C. 408A(d)(3)(A)).
  # Evidence: (c) internal consistency, issue #123 for owner review. The stack shows what is already done plus what is still to come for each line; its ordinary lines must add up to the gross income the projection taxes, so the stack total cannot disagree with the rooms beside it. Long-term gains and qualified dividends are outside gross-income brackets and are excluded.
  Scenario: With this year's actuals on, the stack's ordinary lines add up to the projection's gross income
    Given a single filer aged 61 in 2026 with a $500,000 traditional IRA
    And this year's actuals show $30,000 of wages and $20,000 already converted
    And the plan converts $50,000 in 2026
    When the Income Control stack for 2026 is built with this year's actuals
    Then the ordinary lines of the stack add up to the gross income of the 2026 projection
    And the 2026 conversion line is $20,000 done plus $30,000 still to come
