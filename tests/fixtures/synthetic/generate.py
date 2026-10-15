"""Regenerate the synthetic import fixtures in this directory.

    pixi run -e ci python tests/fixtures/synthetic/generate.py

Every name, account number and amount below is INVENTED ("Pat Example",
"Sam Example", round dollar figures, ticker ACME). The layouts copy what each
parser reads -- the anchors in engine/*_pdf.py and the page-text excerpts in
the matching tests -- and nothing is taken from a real statement. The repo is
public: never paste real statement text in here.

The PDFs are written by a small built-in writer (one Helvetica text line per
statement line, no images, no timestamps), so they need no extra dependency,
regenerate byte-for-byte, and still go through pdfplumber's real text
extraction when the app reads them.

The backup bundle is built with the app's own v5 builder
(engine.bridge_bundle.build_bundle_v5). The ``.json.enc`` copy is sealed to
the TEST-ONLY key pair below; a sealed box is randomised, so that one file
differs on every regeneration while decrypting to the same bundle.
"""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

# TEST-ONLY data-bridge private key (32 bytes, base64). It protects nothing --
# it exists so the synthetic .json.enc can be opened in tests and in the
# browser check. Never use it for real data.
TEST_PRIVKEY_B64 = base64.b64encode(bytes(range(1, 33))).decode("ascii")

# Wide pages keep long statement lines on the page; 12pt keeps a space glyph
# wider than pdfplumber's 3pt x-tolerance so words stay separated.
_PAGE_W, _PAGE_H = 1400, 1000
_FONT_SIZE, _LEADING, _MARGIN = 12, 15, 40


def _pdf_string(line: str) -> bytes:
    raw = line.encode("cp1252")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _content_stream(text: str) -> bytes:
    lines = text.split("\n")
    if len(lines) * _LEADING > _PAGE_H - 2 * _MARGIN:
        raise ValueError(f"page has too many lines ({len(lines)})")
    out = [b"BT", f"/F1 {_FONT_SIZE} Tf".encode()]
    y = _PAGE_H - _MARGIN
    for line in lines:
        out.append(f"1 0 0 1 {_MARGIN} {y} Tm".encode())
        out.append(_pdf_string(line) + b" Tj")
        y -= _LEADING
    out.append(b"ET")
    return b"\n".join(out)


def build_pdf(pages: Sequence[str]) -> bytes:
    """A minimal, deterministic PDF with one text line per line of each page."""
    n = len(pages)
    # Object numbers: 1 catalog, 2 pages, 3 font, then (page, content) pairs.
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
            + f"] /Count {n} >>"
        ).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for i, text in enumerate(pages):
        stream = _content_stream(text)
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
            ).encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")

    body = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{num} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        body += f"{off:010d} 00000 n \n".encode()
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(body)


# --- Statement page text ----------------------------------------------------

VANGUARD_ROTH_IRA = [
    """Roth IRA brokerage account—XXXX0001 Vanguard Personal Investor
Pat Example 800-555-0100
Account overview $50,000.00
Total account value as of June 30, 2026
June 30, 2026, quarter-to-date statement
Activity summary for statement period Retirement summary
Value on March 31, 2026 $48,000.00 2026 contributions $0.00
Deposits and withdrawals* 0.00 2026 distributions $0.00
Change in value** 1,700.00
Dividends, interest, and capital gains 300.00
Value on June 30, 2026 $50,000.00
Year-to-date income
Taxable income $0.00
Nontaxable income 600.00
Total $600.00
Balances and holdings for Vanguard Brokerage Account—XXXX0001""",
    """Roth IRA brokerage account—XXXX0001 Vanguard Personal Investor
Pat Example 800-555-0100
June 30, 2026, quarter-to-date statement Page 2 of 2
Account activity for Vanguard Brokerage Account—XXXX0001
This section shows transactions that have settled by June 30, 2026.
Income summary
Dividends Interest Tax-exempt interest Short-term capital gains Long-term capital gains Other income
June $300.00 $0.00 $0.00 $0.00 $0.00 $0.00
Year-to-date 600.00 0.00 0.00 0.00 0.00 0.00""",
]

FIDELITY_TRADITIONAL_IRA = [
    """1 FIDELITY ROLLOVER IRA PAT EXAMPLE - ROLLOVER IRA - FIDELITY 999-000001 $396,000.00 $400,000.00
INVESTMENT REPORT
June 1, 2026 - June 30, 2026
Account # 999-000001
Account Summary
PAT EXAMPLE - ROLLOVER IRA
Account Value: $400,000.00 Account Holdings
Change in Account Value $4,000.00
Income Summary
This Period Year-to-Date
Tax-deferred $1,000.00 $5,000.00
Total $1,000.00 $5,000.00
Contributions and Distributions
This Period Year-to-Date
2026 Contributions $0.00 $0.00""",
]

IBKR_TRADITIONAL_IRA = [
    """Activity Statement - June 30, 2026 Page: 1 of 1
Interactive Brokers LLC, Two Pickwick Plaza, Greenwich, CT 06830
Account Information
Name Pat EXAMPLE Rollover IRA, Interactive Brokers LLC Custodian
Account Alias IRA
Account U00000001
Account Type Individual
Customer Type IRA-Traditional Rollover
Account Capabilities Margin
Net Asset Value
Asset Class Prior Total Current Long Current Short Current Total Change
Stocks 98,000.00 100,000.00 0.00 100,000.00 2,000.00
Total 98,000.00 100,000.00 0.00 100,000.00 2,000.00
Ending Value 100,000.00
Month & Year to Date Performance Summary
Mark-to-Market Realized S/T Realized L/T
Symbol Description MTD YTD MTD YTD MTD YTD
Stocks
ACME ACME CORP 2,000.00 5,000.00 0.00 0.00 0.00 0.00
Total Stocks 2,000.00 5,000.00 0.00 0.00 0.00 0.00
Cash Report
Total Securities Futures Month to Date Year to Date
Base Currency Summary
Starting Cash 0.00 0.00 0.00
Dividends 0.00 0.00 0.00 0.00 400.00
Ending Cash 0.00 0.00 0.00
Ending Settled Cash 0.00 0.00 0.00""",
]

_NB_HEADER = "1/02/27, 8:15 AM Fidelity NetBenefits - Statement Details"
_NB_FOOTER = "https://workplaceservices.fidelity.com/mybenefits/savings2/sod/soddetail {n}/3"
_NB_TABLE_HEADER = (
    "Shares/Units Shares/Units Account Account\n"
    "Price as of Price as of\n"
    "Investment as of as of Value as of Value as of\n"
    "12/31/2025 06/30/2026\n"
    "12/31/2025 06/30/2026 12/31/2025 06/30/2026"
)

NETBENEFITS_403B = [
    f"""{_NB_HEADER}
Statement Details
Example College 403b
Retirement Savings Statement
Retirement Plan
SAM EXAMPLE Customer Service: (800) 555-
1 EXAMPLE ROAD 0100
NOWHERE, ZZ 00000- Fidelity Brokerage Services LLC
1 Example Street, Nowhere, ZZ
00000
Your Account Summary
Statement Period: 01/01/2026 to 06/30/2026
Beginning Balance $90,000.00
Fees -$0.00
Change in Account Value $10,000.00
Ending Balance $100,000.00
Additional Information
Vested Balance $100,000.00
Dividends & Interest $1,000.00
Your Personal Rate of Return
This Period 10.0%
Account Value
Statement Period: 01/01/2026 to 06/30/2026
CORE FUNDS
{_NB_TABLE_HEADER}
Stock $18,000.00 $20,000.00
International
{_NB_FOOTER.format(n=1)}""",
    f"""{_NB_HEADER}
{_NB_TABLE_HEADER}
Acme Intl 100.000 100.000 $90.00 $100.00 $9,000.00 $10,000.00
Equity IS
Bigco US 100.000 100.000 $90.00 $100.00 $9,000.00 $10,000.00
Small Value
TARGET DATE FUNDS
{_NB_TABLE_HEADER}
Blended Investments $72,000.00 $80,000.00
Example Target
Ret 2035 1,000.000 1,000.000 $72.00 $80.00 $72,000.00 $80,000.00
Account Totals $90,000.00 $100,000.00
{_NB_FOOTER.format(n=2)}""",
    f"""{_NB_HEADER}
Your Contribution Summary
Statement Period: 01/01/2026 to 06/30/2026
Total Account Total Vested
Contributions Period to date Vested Percent
Balance Balance
Voluntary Pretax
$0.00 100% $70,000.00 $70,000.00
Deferral
Employer Match $0.00 100% $30,000.00 $30,000.00
Your Account Activity
Statement Period: 01/01/2026 to 06/30/2026
Activity Total
Ending Balance $100,000.00
Dividends & Interest $1,000.00
{_NB_FOOTER.format(n=3)}""",
]

SSA_STATEMENT = [
    """Your Social Security Statement
PAT EXAMPLE March 2, 2026
Personalized Monthly Retirement Benefit Estimates
Retirement Benefits
(Depending on the Age You Start)
You have earned enough credits to qualify for retirement
benefits.
Your full retirement age is 67, based on your date of
birth: January 2, 1965. As shown in the chart, you can
start your benefits at any time between ages 62 and
70.
These personalized estimates are based on your
earnings to date and assume you continue to earn
$50,000 per year until you start your benefits.
Disability Benefits
be about $2,000. Learn more at ssa.gov/disability.
Survivors Benefits
Minor child: $1,500
Spouse, if benefits start at full retirement age: $2,000
Total family benefits cannot be more than: $3,500
tratS
stifeneB
tnemeriteR
egA
62 $1,400
63 $1,500
64 $1,600
65 $1,700
66 $1,850
67 $2,000
68 $2,150
69 $2,300
70 $2,500
Monthly Benefit Amount
Medicare
You have enough credits to qualify for Medicare at age
65.""",
    """Earnings Record Important Things to Know about Your Social
Work Year Social Security (began 1966)
2023 $40,000 $40,000 fairness-act.html .
2024 $45,000 $45,000
2025 $50,000 $50,000
Taxes Paid
You paid: $10,000 You paid: $2,000
Form SSA-7005-SM-OL (04/25) | Enclosures: Publication No. 05-10706""",
]

FORM_1040_2025 = [
    """Department of the Treasury - Internal Revenue Service
Form 1040 (2025) U.S. Individual Income Tax Return
Filing Status Single Married filing jointly Head of household (HOH)
2a Tax-exempt interest . . 2a 1,000 b Taxable interest 2b 2,000
3a Qualified dividends . . 3a 3,000 b Ordinary dividends 3b 4,000
6 Social security benefits 6b 0
11 Subtract line 10 from line 9. This is your adjusted gross income 150,000""",
    """Schedule 1 (Form 1040) Additional Income and Adjustments""",
]

# A TAXABLE account whose period ends in 2025: against a 2026 YTD snapshot its
# figures are "another year" and must not be counted (audit-1008 W9, issue #12).
FIDELITY_TAXABLE_2025 = [
    """Fidelity Brokerage Services LLC
INVESTMENT REPORT
December 1, 2025 - December 31, 2025
Account # Z12-345678
PAT EXAMPLE - INDIVIDUAL
Account Summary
Account Value: $200,000.00 Account Holdings
Change in Account Value $2,000.00
Income Summary
This Period Year-to-Date
Taxable Dividends $300.00 $3,000.00
Tax-exempt Dividends $50.00 $500.00
Total $350.00 $3,500.00""",
]

PDF_FIXTURES: dict[str, list[str]] = {
    "fidelity_taxable_2025.pdf": FIDELITY_TAXABLE_2025,
    "vanguard_roth_ira_2026q2.pdf": VANGUARD_ROTH_IRA,
    "fidelity_traditional_ira_2026q2.pdf": FIDELITY_TRADITIONAL_IRA,
    "ibkr_traditional_ira_2026q2.pdf": IBKR_TRADITIONAL_IRA,
    "netbenefits_403b_2026h1.pdf": NETBENEFITS_403B,
    "ssa_statement_2026.pdf": SSA_STATEMENT,
    "form_1040_2025.pdf": FORM_1040_2025,
}


# --- Backup bundle ----------------------------------------------------------

BUNDLE_NAME = "synthetic_backup_v5.json"
BUNDLE_ENC_NAME = "synthetic_backup_v5.json.enc"
BUNDLE_EXPORTED_AT = "2026-07-01T12:00:00+00:00"


def build_backup_bundle() -> dict:
    """A v5 backup of a fictional two-person household, via the app's builder."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from engine.bridge_bundle import build_bundle_v5
    from engine.portfolio_sync.shapes import AccountSummary, EquityGrant, PortfolioSnapshot
    from models.ytd_income import IncomeEvent, YTDSnapshot

    scalars = {
        "your_age": 60,
        "spouse_age": 58,
        "your_ira": 400_000,
        "spouse_ira": 300_000,
        "filing_status": "MFJ",
        "living_expenses": 80_000,
        "stock_price_now": 100.0,
        "grant_strikes": {"2019": 50.0, "2020": 60.0},
        "prior_year_magi": {"2025": 150_000.0},
    }
    snapshot = PortfolioSnapshot(
        accounts=[
            AccountSummary(
                account_type="trad_ira",
                owner="you",
                account_name="Example Traditional IRA",
                total_value=400_000.0,
                equity_value=400_000.0,
                as_of="2026-06-30",
                as_of_source="statement",
            ),
            AccountSummary(
                account_type="roth_ira",
                owner="you",
                account_name="Example Roth IRA",
                total_value=50_000.0,
                equity_value=50_000.0,
                as_of="2026-06-30",
                as_of_source="statement",
            ),
            AccountSummary(
                account_type="trad_ira",
                owner="spouse",
                account_name="Example Spouse IRA",
                total_value=300_000.0,
                equity_value=300_000.0,
                as_of="2026-06-30",
                as_of_source="statement",
            ),
            AccountSummary(
                account_type="brokerage",
                owner="household",
                account_name="Example Joint Brokerage",
                total_value=200_000.0,
                equity_value=150_000.0,
                cash_value=50_000.0,
                as_of="2026-06-30",
                as_of_source="statement",
            ),
        ],
    )
    grants = [
        EquityGrant("ACME-2019", "NQO", "2019-02-01", 1_000, 1_000, 50_000.0),
        EquityGrant("ACME-2020", "NQO", "2020-02-01", 2_000, 2_000, 80_000.0),
    ]
    ytd = YTDSnapshot(
        snapshot_date="2026-06-30",
        by_owner={
            "you": {"wages_ytd": 50_000.0, "federal_withholding_ytd": 8_000.0},
            "spouse": {"wages_ytd": 40_000.0},
        },
        ira_conversions_ytd=20_000.0,
        income_events=[IncomeEvent("2026-03-01", 20_000.0, "conversion", "you")],
    )
    return build_bundle_v5(
        setup_scalars=scalars,
        snapshot=snapshot,
        ledger={"koinly": {}, "brokerage": {}},
        ytd=ytd,
        grants=grants,
        exporter="you",
        slot_captured_at={},
        now=BUNDLE_EXPORTED_AT,
    )


def bundle_bytes() -> bytes:
    return json.dumps(build_backup_bundle(), indent=2, sort_keys=True).encode("utf-8") + b"\n"


def main() -> None:
    for name, pages in PDF_FIXTURES.items():
        (HERE / name).write_bytes(build_pdf(pages))

    plaintext = bundle_bytes()  # puts the repo root on sys.path
    (HERE / BUNDLE_NAME).write_bytes(plaintext)

    from engine.data_bridge_crypto import derive_pubkey, seal

    pubkey = derive_pubkey(base64.b64decode(TEST_PRIVKEY_B64))
    (HERE / BUNDLE_ENC_NAME).write_bytes(seal(plaintext, pubkey))
    print(f"wrote {len(PDF_FIXTURES) + 2} files to {HERE}")


if __name__ == "__main__":
    main()
