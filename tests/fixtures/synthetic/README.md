# Synthetic import fixtures

Invented statements and a backup bundle for testing the import pages. These
files are not real documents. Every name ("Pat Example", "Sam Example"),
account number, amount and ticker (ACME) in them is made up. The repo is
public, so never add text copied from a real statement here.

`tests/test_synthetic_import_fixtures.py` runs every file through the real
code path: pdfplumber extraction, content classification, then parse and
apply. It also fails if a committed file no longer matches what the generator
writes.

## Regenerate

```
pixi run -e ci python tests/fixtures/synthetic/generate.py
```

The PDFs and `synthetic_backup_v5.json` come out byte-for-byte identical every
time. `synthetic_backup_v5.json.enc` comes out different each time, because
sealed-box encryption is randomised, but it always decrypts to the same
bundle.

## Files

| File | What it is | Parsed as |
|---|---|---|
| `vanguard_roth_ira_2026q2.pdf` | Vanguard Roth IRA, Pat | roth_ira, $50,000 ending value |
| `fidelity_traditional_ira_2026q2.pdf` | Fidelity Rollover IRA, Pat | traditional_ira, $400,000 |
| `ibkr_traditional_ira_2026q2.pdf` | IBKR Rollover IRA, Pat | traditional_ira, $100,000 |
| `fidelity_taxable_2025.pdf` | Fidelity taxable account, Pat, period ends 2025-12-31 | taxable, dividends $3,000 (prior year) |
| `netbenefits_403b_2026h1.pdf` | NetBenefits 403(b), Sam | 403(b), $100,000 |
| `ssa_statement_2026.pdf` | SSA statement, Pat | FRA 67, $2,000/mo at FRA |
| `form_1040_2025.pdf` | 2025 Form 1040 | AGI $150,000, MAGI $151,000 |
| `synthetic_backup_v5.json` | v5 backup, plaintext | 4 accounts, 2 ACME grants, YTD |
| `synthetic_backup_v5.json.enc` | the same backup, encrypted | needs the test key below |
| `synthetic_backup_v5_holdings.json` | v5 backup, plaintext, with brokerage holdings | as above, plus 3 brokerage positions and YTD qualified dividends |
| `synthetic_backup_v5_holdings.json.enc` | the same holdings backup, encrypted | needs the test key below |

**Test-only data-bridge private key** (base64). This key protects nothing:
it only exists to open the `.enc` file above. Never use it for real data.

```
AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA=
```

## Browser checks (stlite build)

Start from a fresh session, or use **Reset to demo**. Set **Command Center**
identity to **Me**.

0. **Other-year note (W9)** (Data > Import Statements). Upload only
   `fidelity_taxable_2025.pdf` with a 2026 YTD snapshot, then **Scan uploaded
   PDFs**.
   - Expect: a note saying the Fidelity statement for account Z12-345678 ends
     2025-12-31 and its 2025 figures were not counted.
   - Expect: the account is NOT listed under "Counted toward YTD income".
   - Expect: **Apply to YTD snapshot** leaves interest and dividends at 0.

1. **Import Statements queue** (📥 Import Statements). Upload the six
   2026/2025 statement PDFs at once (all but `fidelity_taxable_2025.pdf`).
   - Expect: the queue lists the SSA statement, the NetBenefits 403(b), the
     2025 1040 and three IRA statements, with nothing unrecognised and no
     errors.
   - Expect: **Clear queued statements** empties the queue.
2. **Holdings** (💼 Holdings). After step 1, apply the three IRA statements
   as "you" and the 403(b) as "spouse".
   - Expect: Pat's traditional total is $500,000 (Fidelity $400,000 + IBKR
     $100,000) and Roth is $50,000.
   - Expect: Sam's 403(b) is $100,000.
   - Expect: each account shows as of 2026-06-30, source statement.
3. **Command Center card** (🎛️ Review Changes). After step 2, plus
   applying the SSA statement as "you".
   - Expect: cards for `your_roth` ($50,000), `spouse_ira` ($100,000, from
     the 403(b)), `your_ss_fra` ($2,000) and the 2025 prior-year MAGI
     ($151,000).
   - `your_ira` gets a card only if the statement total ($500,000) differs
     from the value already committed. On a fresh demo session it is
     already $500,000, so no card appears.
4. **Backup & Transfer** (🔐 Backup & Transfer).
   - Paste the test key into **🔑 V2 private key** and Save. In a hidden or
     background browser tab, Save may not register; bring the window to the
     front, or set the value by script.
   - Upload `synthetic_backup_v5.json.enc`. The uploader accepts `.enc` files
     only, so `synthetic_backup_v5.json` is for tests and inspection, not for
     this check.
   - Expect: the preview shows slot "you" (settings `your_age`/`your_ira`,
     2 accounts, YTD figures, grants), slot "spouse" (1 account) and
     1 household account, each section on its own line.
   - Apply. Expect: Holdings shows the four "Example …" accounts, and its
     grant comparison labels the imported rows "Backup import".
4b. **Per-ticker qualified/ordinary table** (🔐 Backup & Transfer, then
   💵 This Year's Income). Same steps as 4, but upload
   `synthetic_backup_v5_holdings.json.enc` and apply.
   - Expect: This Year's Income shows YTD qualified dividends $1,500 (ordinary
     dividends and interest are household fields that a backup never carries,
     so they stay $0) and a "Qualified vs ordinary by ticker" expander.
   - Expect rows: BND $2,100 / $0 qualified; VNQ $1,600 / $0; VTI $1,300
     all qualified (100%); Total $5,000, qualified $1,300, ordinary $3,700
     (26%). No `.dividend_rates.json` overrides may be present.
5. **Option grid refill** (📝 Option Exercises). After step 4.
   - Imported grants are NOT adopted on their own (audit-1008 F-06): they
     wait in 🎛️ Review Changes as a `grants` card. Until it is confirmed,
     the grid keeps the current grants.
   - Confirm the `grants` card (use the file's value).
   - Expect: the grid re-seeds with two lots: 2019 · $50 · 1,000 sh and
     2020 · $60 · 2,000 sh. Neither row should be the demo grants, and no
     cell should be stuck at 0 from the previous layout.
