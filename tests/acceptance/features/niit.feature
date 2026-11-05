Feature: Net investment income tax (NIIT)

  # Source: IRS Instructions for Form 8960 (2025), "Application to Individuals" and Line 14 threshold table (MFJ $250,000; single $200,000). Rule: 3.8% of the smaller of (a) MAGI over threshold or (b) NII. Thresholds are statutory and not indexed (IRC 1411(b)). https://www.irs.gov/instructions/i8960
  # Evidence: (b) rule + arithmetic.
  # Arithmetic: 280,000 - 250,000 = 30,000; smaller of 50,000 and 30,000 = 30,000; x 3.8% = 1,140.
  #             400,000 - 250,000 = 150,000; smaller = 50,000 (NII); x 3.8% = 1,900.
  #             220,000 - 200,000 = 20,000; smaller = 10,000 (NII); x 3.8% = 380.
  Scenario Outline: NIIT from MAGI and net investment income
    Given a household filing <status> with MAGI of <magi> and net investment income of <nii>
    When the net investment income tax is figured
    Then the net investment income tax is <niit>

    Examples:
      | status                 | magi     | nii     | niit   |
      | married filing jointly | $240,000 | $30,000 | $0     |
      | married filing jointly | $250,000 | $30,000 | $0     |
      | married filing jointly | $280,000 | $50,000 | $1,140 |
      | married filing jointly | $400,000 | $50,000 | $1,900 |
      | single                 | $220,000 | $10,000 | $380   |
