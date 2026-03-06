"""Net Investment Income Tax (NIIT) — 3.8% surtax on investment income.

The NIIT applies to the LESSER of:
  (a) net investment income, OR
  (b) MAGI exceeding the threshold ($250K MFJ)

Investment income includes: capital gains, dividends, interest, rental income,
and passive business income. It does NOT include wages, SS, or IRA distributions.

Roth conversions increase MAGI, which can push brokerage gains into NIIT territory
even though conversion income itself is not "investment income."
"""

# MFJ threshold — not inflation-indexed (set by ACA in 2013, unchanged since)
NIIT_THRESHOLD_MFJ = 250_000
NIIT_RATE = 0.038


def niit(magi: float, net_investment_income: float) -> float:
    """
    Calculate Net Investment Income Tax.

    Args:
        magi: Modified Adjusted Gross Income (joint)
        net_investment_income: Capital gains + dividends + interest + rental income

    Returns:
        NIIT amount (3.8% on lesser of NII or MAGI excess over threshold)
    """
    if magi <= NIIT_THRESHOLD_MFJ or net_investment_income <= 0:
        return 0.0
    excess = magi - NIIT_THRESHOLD_MFJ
    taxable_nii = min(net_investment_income, excess)
    return taxable_nii * NIIT_RATE


def niit_from_conversion(
    base_magi: float, conversion: float, net_investment_income: float
) -> float:
    """
    Incremental NIIT caused by a Roth conversion.

    The conversion itself is not investment income, but it raises MAGI,
    which can expose more investment income to the 3.8% tax.
    """
    return niit(base_magi + conversion, net_investment_income) - niit(
        base_magi, net_investment_income
    )
