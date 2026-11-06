Feature: Required minimum distribution when the sole beneficiary is a much younger spouse

  # Source: IRS Publication 590-B (2025, for 2025 returns), Ch. 1 "Figuring the Owner's Required Minimum Distribution", Example "Sole beneficiary spouse who is more than 10 years younger"; Appendix B Table II (Joint Life and Last Survivor Expectancy), row 75 / column 64. https://www.irs.gov/publications/p590b
  # Evidence: (a) worked example. Owner turns 75 and spouse turns 64 in 2026 (11 years younger); $100,000 / 25.3 = $3,953. The publication rounds to the nearest dollar.
  Scenario: Spouse 11 years younger uses the joint life table
    Given a traditional IRA owner who turns 75 in 2026
    And their spouse is the sole beneficiary and is 11 years younger
    And the IRA balance on December 31, 2025 was $100,000
    When the 2026 required minimum distribution is figured
    Then the distribution period is 25.3
    And the required minimum distribution is $3,953

  # Source: as above, Appendix B Table II, ages 60-69 block row 75 ("75 28.3 27.5 26.8 26.1 25.3 24.6 ...") and ages 50-59 block row 73 ("73 36.8 36.0 35.1 34.2 33.4 32.6 ..."). https://www.irs.gov/publications/p590b
  # Evidence: (b) table + arithmetic. Ages are as of the birthdays in 2026. Rounded to the nearest dollar as the publication does.
  # Arithmetic: 100,000 / 28.3 = 3,533.57 -> 3,534. 500,000 / 32.6 = 15,337.42 -> 15,337.
  Scenario Outline: Joint life factor for a spouse more than 10 years younger
    Given a traditional IRA owner who turns <owner_age> in 2026
    And their spouse is the sole beneficiary and is <gap> years younger
    And the IRA balance on December 31, 2025 was <balance>
    When the 2026 required minimum distribution is figured
    Then the distribution period is <period>
    And the required minimum distribution is <rmd>

    Examples:
      | owner_age | gap | balance  | period | rmd     |
      | 75        | 15  | $100,000 | 28.3   | $3,534  |
      | 73        | 18  | $500,000 | 32.6   | $15,337 |

  # Source: as above, Ch. 1: Table II is used only "if the sole beneficiary of your IRA is your spouse and your spouse is more than 10 years younger than you"; otherwise Table III (Uniform Lifetime), age 75 = 24.6.
  # Evidence: (b) rule + table. With a second beneficiary the spouse is not the SOLE beneficiary, so Table III applies even though the spouse is 15 years younger (Table II would give 28.3).
  # Arithmetic: 100,000 / 24.6 = 4,065.04 -> 4,065.
  Scenario: A much younger spouse who is not the sole beneficiary does not use the joint life table
    Given a traditional IRA owner who turns 75 in 2026
    And their spouse is 15 years younger and shares the IRA with another beneficiary
    And the IRA balance on December 31, 2025 was $100,000
    When the 2026 required minimum distribution is figured
    Then the distribution period is 24.6
    And the required minimum distribution is $4,065
