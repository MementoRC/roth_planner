Feature: Income Harvest

  # Evidence: IRC §1(h)(1)(B) (0% bracket), §86 (Social Security taxability).
  Background:
    Given a married-filing-jointly household, both under 65, with no Social Security
    And no inflation, and a brokerage account with no growth, no dividends and no turnover
    And ACA coverage is off

  Scenario: The 0% bracket sets the harvest
    Given a 2026 conversion of $60,000 is the only income
    And the brokerage cost basis is well below its balance
    And the conversion tax is paid from cash, not the brokerage account
    When I view Income Control for 2026
    Then the 0% LTCG harvest is $71,100
    And it stops at the top of the 0% LTCG bracket

  Scenario: The unrealized gain caps the harvest
    Given a 2026 conversion of $60,000 is the only income
    And the brokerage holds $20,000 of unrealized gain
    And the conversion tax is paid from cash, not the brokerage account
    When I view Income Control for 2026
    Then the 0% LTCG harvest is $20,000
    And it stops at the unrealized gain in the brokerage account

  Scenario: The conversion already used the 0% room
    Given a 2026 conversion of $140,000 is the only income
    When I view Income Control for 2026
    Then the 0% LTCG harvest is $0
    And the tab says this year's planned conversion already uses the 0% LTCG room

  Scenario: No cost basis entered
    Given a 2026 conversion of $60,000 is the only income
    And no brokerage cost basis is entered
    When I view Income Control for 2026
    Then the 0% LTCG harvest is $71,100
    And the tab asks me to enter my brokerage cost basis in Profile

  Scenario: Social Security makes the harvest smaller than the 0% room
    Given a married-filing-jointly household, both 70, receiving $30,000 of Social Security
    And a $20,000 IRA distribution in 2026
    When I view Income Control for 2026
    Then the 0% LTCG harvest is $100,900
    And taxable Social Security costs $25,500 of that room, against $126,400 if it were untaxed
