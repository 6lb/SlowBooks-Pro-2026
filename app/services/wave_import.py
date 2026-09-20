# ============================================================================
# Wave import dialect.
#
#   - Accounts export: "Account Name" / "Account Type" (Wave's types are
#     descriptive: "Cash and Bank", "Operating Expense", "Retained
#     Earnings: Profit", ... — mapped explicitly, then by keyword).
#   - Accounting Transactions export: one row per line, grouped by
#     "Transaction ID"; carries either Debit/Credit columns or a single
#     signed Amount column (both supported).
#   - Trial balance report for verification.
#   - Dates are ISO in Wave exports; US-style accepted as fallback.
# ============================================================================

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.services.migration_common import (
    dry_run_bundle,
    skip_report_preamble,
    field,
    make_classifier,
    parse_amount,
    parse_date,
    run_import_bundle,
    sniff_reader,
    strip_code_suffix,
)

logger = logging.getLogger(__name__)

LABEL = "Wave"

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y")

_WAVE_TYPE_MAP = {
    "cash and bank": AccountType.ASSET,
    "money in transit": AccountType.ASSET,
    "expected payments from customers": AccountType.ASSET,
    "inventory": AccountType.ASSET,
    "property, plant, equipment": AccountType.ASSET,
    "depreciation and amortization": AccountType.ASSET,
    "vendor prepayments": AccountType.ASSET,
    "other short-term asset": AccountType.ASSET,
    "other long-term asset": AccountType.ASSET,
    "credit card": AccountType.LIABILITY,
    "loan and line of credit": AccountType.LIABILITY,
    "expected payments to vendors": AccountType.LIABILITY,
    "sales taxes": AccountType.LIABILITY,
    "due for payroll": AccountType.LIABILITY,
    "due to you and other business owners": AccountType.LIABILITY,
    "other short-term liability": AccountType.LIABILITY,
    "other long-term liability": AccountType.LIABILITY,
    "business owner contribution and drawing": AccountType.EQUITY,
    "retained earnings: profit": AccountType.EQUITY,
    "income": AccountType.INCOME,
    "discount": AccountType.INCOME,
    "other income": AccountType.INCOME,
    "uncategorized income": AccountType.INCOME,
    "cost of goods sold": AccountType.COGS,
    "operating expense": AccountType.EXPENSE,
    "payment processing fee": AccountType.EXPENSE,
    "payroll expense": AccountType.EXPENSE,
    "uncategorized expense": AccountType.EXPENSE,
    "loss on foreign exchange": AccountType.EXPENSE,
}

# Keyword fallback for Wave type strings we haven't enumerated — their
# labels are self-describing enough that this rarely misfires, and the
# dry-run surfaces anything that lands on None.
_KEYWORD_RULES = (
    (("cost of goods",), AccountType.COGS),
    (("income", "discount"), AccountType.INCOME),
    (("expense", "fee", "loss"), AccountType.EXPENSE),
    (("equity", "owner", "retained"), AccountType.EQUITY),
    (
        ("liability", "credit card", "loan", "tax", "payroll", "due "),
        AccountType.LIABILITY,
    ),
    (
        ("asset", "cash", "bank", "inventory", "transit", "prepayment", "plant"),
        AccountType.ASSET,
    ),
)


def _map_type(raw: str):
    lowered = raw.lower().strip()
    if lowered in _WAVE_TYPE_MAP:
        return _WAVE_TYPE_MAP[lowered]
    for keywords, acct_type in _KEYWORD_RULES:
        if any(k in lowered for k in keywords):
            return acct_type
    return None


classify_filename = make_classifier()


def _has_two_column_pair(row: dict) -> bool:
    """True when the file has its own debit and credit columns."""
    heads = {(k or "").strip().lstrip("\ufeff").lower() for k in row}
    return any(h.startswith("debit") for h in heads) and any(
        h.startswith("credit") for h in heads
    )


def parse_coa(csv_text: str) -> tuple[list[dict], list[str]]:
    accounts, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account name", "name", "account")
        if not name:
            continue
        raw_type = field(row, "account type", "type", "account group")
        acct_type = _map_type(raw_type)
        if not acct_type:
            errors.append(f"COA row {i}: unmapped Wave account type {raw_type!r}")
            continue
        accounts.append(
            {
                "code": field(row, "account id", "account code") or None,
                "name": name,
                "type": acct_type,
                "description": field(row, "description") or None,
            }
        )
    return accounts, errors


def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        # Wave's "Account Transactions" report — a plain export any Wave
        # user can pull with no plan restrictions — uses "ACCOUNT NUMBER"
        # as the header over what is actually the account name for every
        # data row (the numeric-code use of that column is vanishingly
        # rare in practice), and suffixes its money columns with
        # "(In Business Currency)". Neither matched below, so every row
        # silently parsed as account="" / debit=credit=0: the dry-run
        # "balanced" only because everything was zero, and the GL-account
        # check failed once, deduped, on the empty name.
        account = field(row, "account name", "account", "account number")
        date_raw = field(row, "transaction date", "date")
        if not account and not date_raw:
            continue
        try:
            # Wave's full export ("Get all transactions" -> accounting.csv)
            # heads its sides "Debit Amount (Two Column Approach)" and
            # "Credit Amount (Two Column Approach)". Neither was an alias, so
            # every line read 0 / 0 (#169).
            debit = parse_amount(
                field(
                    row,
                    "debit amount (two column approach)",
                    "debit amount",
                    "debit",
                    "debit (in business currency)",
                )
            )
            credit = parse_amount(
                field(
                    row,
                    "credit amount (two column approach)",
                    "credit amount",
                    "credit",
                    "credit (in business currency)",
                )
            )
            # accounting.csv ALSO carries "Amount (One column)", signed by
            # what the amount does to the account (a sale is positive on the
            # bank line and on the income line), not by side. Reading it as
            # "positive = debit" made every journal unbalanced by debit +
            # credit. It is only a side-signed fallback for exports that have
            # no two-column pair at all.
            if debit == 0 and credit == 0 and not _has_two_column_pair(row):
                # Single signed Amount column: positive = debit
                signed = parse_amount(
                    field(
                        row,
                        "amount (one column)",
                        "amount",
                        "amount (in business currency)",
                    )
                )
                if signed > 0:
                    debit = signed
                elif signed < 0:
                    credit = -signed
            rows.append(
                {
                    "journal": field(row, "transaction id", "id"),
                    "date": parse_date(date_raw, _DATE_FORMATS),
                    "account": account,
                    "description": field(
                        row,
                        "transaction line description",
                        "transaction description",
                        "description",
                        "notes / memo",
                    ),
                    "reference": field(row, "transaction id", "id"),
                    "debit": debit,
                    "credit": credit,
                }
            )
        except ValueError as exc:
            errors.append(f"GL row {i}: {exc}")

    journals: dict = {}
    for row in rows:
        key = row["journal"] or f"{row['date'].isoformat()}|{row['reference']}"
        journals.setdefault(key, []).append(row)
    return list(journals.values()), errors


def parse_tb(csv_text: str) -> tuple[dict, list[str]]:
    # Wave's trial balance is a report export: title/company/date-range
    # preamble, UPPERCASE headers ("ACCOUNTS"), section rows with no
    # amounts, and "Total …" subtotal rows.
    csv_text = skip_report_preamble(csv_text)
    balances, errors = {}, []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account name", "accounts", "account", "name")
        if not name or name.lower().startswith("total"):
            continue
        raw_debit = field(row, "debit", "debit amount")
        raw_credit = field(row, "credit", "credit amount")
        if not raw_debit and not raw_credit:
            continue  # section header row ("Assets", "Income", …)
        try:
            debit = parse_amount(raw_debit)
            credit = parse_amount(raw_credit)
        except ValueError as exc:
            errors.append(f"TB row {i}: {exc}")
            continue
        key = strip_code_suffix(name)
        balances[key] = balances.get(key, Decimal("0")) + debit - credit
    return balances, errors


_PARSERS = {"coa": parse_coa, "gl": parse_gl, "tb": parse_tb}


def dry_run(db: Session, bundle: dict) -> dict:
    return dry_run_bundle(db, bundle, _PARSERS, LABEL, "wave_import")


def run_import(db: Session, bundle: dict) -> dict:
    return run_import_bundle(db, bundle, _PARSERS, "wave_import", LABEL)
