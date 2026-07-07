"""Net Investment Income Tax (NIIT) — 3.8% surtax on investment income.

The NIIT applies to the LESSER of:
  (a) net investment income, OR
  (b) MAGI exceeding the threshold ($250K MFJ)

Investment income includes: capital gains, dividends, interest, rental income,
and passive business income. It does NOT include wages, SS, or IRA distributions.

Roth conversions increase MAGI, which can push brokerage gains into NIIT territory
even though conversion income itself is not "investment income."
"""

# IRC §1411 thresholds — statutory, non-indexed (fixed since ACA 2013)
NIIT_THRESHOLD_MFJ = 250_000
NIIT_THRESHOLD_SINGLE = 200_000
NIIT_THRESHOLD_HOH = 200_000  # Head of Household — same as Single per IRC §1411(b)(3)
NIIT_THRESHOLD_MFS = 125_000  # Married Filing Separately — half of MFJ per IRC §1411(b)(2)
NIIT_RATE = 0.038

_NIIT_THRESHOLDS: dict[str, int] = {
    "MFJ": NIIT_THRESHOLD_MFJ,
    "Single": NIIT_THRESHOLD_SINGLE,
    "HoH": NIIT_THRESHOLD_HOH,
    "MFS": NIIT_THRESHOLD_MFS,
}


def niit(magi: float, net_investment_income: float, filing_status: str = "MFJ") -> float:
    """
    Calculate Net Investment Income Tax.

    Args:
        magi: Modified Adjusted Gross Income
        net_investment_income: Capital gains + dividends + interest + rental income
        filing_status: "MFJ" ($250K), "Single"/"HoH" ($200K), or "MFS" ($125K)

    Returns:
        NIIT amount (3.8% on lesser of NII or MAGI excess over threshold)
    """
    threshold = _NIIT_THRESHOLDS.get(filing_status, NIIT_THRESHOLD_MFJ)
    if magi <= threshold or net_investment_income <= 0:
        return 0.0
    excess = magi - threshold
    taxable_nii = min(net_investment_income, excess)
    return taxable_nii * NIIT_RATE
