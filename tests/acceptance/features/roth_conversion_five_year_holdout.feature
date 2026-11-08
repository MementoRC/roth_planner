Feature: 10% additional tax on converted Roth amounts withdrawn within five years (held-out boundary)

  # Held out from the implementer (T1 phase 2b); added to the PR only after implementation.

  # Source: IRS Publication 590-B (2025), Ch. 2 "Roth IRAs", "Additional Tax on Early Distributions" > "Distributions of conversion and certain rollover contributions within 5-year period". https://www.irs.gov/publications/p590b
  # Evidence: (b) rule text + worked example. The period is "the 5-year period starting with the first day of your tax year in which you convert"; a 2026 conversion's period is tax years 2026-2030, so 2030 is its LAST year and a 2030 distribution is still inside it. The owner (54) is under 59 1/2.
  Scenario: Converted money drawn in the last year of the five-year period
    Given a single filer aged 50 in 2026 with a $200,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert the whole IRA in 2026
    When the years through 2030 are projected
    Then in 2030 they draw from the Roth IRA and not from the traditional IRA
    And the 2030 early-withdrawal additional tax is 10% of that year's Roth draw
