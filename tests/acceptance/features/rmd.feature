Feature: Required minimum distribution for an IRA owner during their lifetime

  # Source: IRS Publication 590-B (2025, for 2025 returns), Ch. 1 "Figuring the Owner's Required Minimum Distribution", Example 1 "Distributions during your lifetime"; Appendix B Table III (Uniform Lifetime). https://www.irs.gov/publications/p590b
  # Evidence: (a) worked example. The publication rounds to the nearest dollar.
  Scenario: Owner turning 75 in 2026, spouse 6 years younger as sole beneficiary
    Given a traditional IRA owner who turns 75 in 2026
    And their spouse is the sole beneficiary and is 6 years younger
    And the IRA balance on December 31, 2025 was $100,000
    When the 2026 required minimum distribution is figured
    Then the distribution period is 24.6
    And the required minimum distribution is $4,065

  # Source: IRS Publication 590-B (2025), Ch. 1 "Miscellaneous Rules for Required Minimum Distributions", Example "More than minimum received" (second-year figure). https://www.irs.gov/publications/p590b
  # Evidence: (a) worked example. The publication's own denominator for age 73 is 26.5; $34,800 / 26.5 = 1,313.21, rounded to $1,313.
  Scenario: Owner aged 73 in 2026
    Given a traditional IRA owner who is 73 in 2026
    And the IRA balance on December 31, 2025 was $34,800
    When the 2026 required minimum distribution is figured
    Then the distribution period is 26.5
    And the required minimum distribution is $1,313
