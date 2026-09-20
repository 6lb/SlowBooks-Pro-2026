"""Wave import: the "Account Transactions" report is a plain export any
Wave user can pull with no plan restrictions, but its headers
("ACCOUNT NUMBER" for what is actually the account name on every data
row, "DEBIT (In Business Currency)" / "CREDIT (In Business Currency)"
for the money columns) didn't match parse_gl()'s alias list. Every row
silently parsed as account="" / debit=credit=0, so the dry-run
"balanced" on all-zero amounts and failed once, deduped, on the GL
account-name check ("GL references account '' not present in the
chart of accounts") — regardless of what the CSV actually contained.
"""

import io

COA_CSV = (
    "Name,Account Code,Account Type,Account Sub-Type,Currency Code,"
    "Is Archived,Description\n"
    "Checking,,Asset,Cash and Bank,USD,FALSE,\n"
    "Office Supplies,,Expense,Operating Expense,USD,FALSE,\n"
)

# Real column headers from Wave's Account Transactions report export.
ACCOUNT_TRANSACTIONS_REPORT_CSV = (
    "ACCOUNT NUMBER,DATE,DESCRIPTION,DEBIT (In Business Currency),"
    "CREDIT (In Business Currency),BALANCE (In Business Currency)\n"
    "Office Supplies,2024-01-15,Staples,$10.00,,\n"
    "Checking,2024-01-15,Staples,,$10.00,\n"
)


def _files(**named):
    return [
        ("files", (name, io.BytesIO(text.encode()), "text/csv"))
        for name, text in named.items()
    ]


def test_account_transactions_report_headers_parse(client, db_session, seed_accounts):
    resp = client.post(
        "/api/migration/wave/dry-run",
        files=_files(
            **{
                # Wave's actual export filenames for this bundle - not a
                # renamed workaround. "Account Transactions.csv" contains
                # both "account" and "transaction"; this must classify as
                # the GL file, not collide with the chart of accounts.
                "Chart of Accounts.csv": COA_CSV,
                "Account Transactions.csv": ACCOUNT_TRANSACTIONS_REPORT_CSV,
            }
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True, data
    assert data["errors"] == []
    assert data["accounts"] == 2
    assert data["journals"] == 1


# --- #169: Wave's full export, accounting.csv ---------------------------------
#
# Header names as the reporter gave them from a real "Get all transactions"
# export (rcavatar1-debug, 14,372 journals): the two-column pair is headed
# "... (Two Column Approach)", and the same file carries "Amount (One column)",
# signed by what the amount does to the account, not by side.

ACCOUNTING_COA = (
    "Number,Name,Type,Parent,Description,Active\n"
    ",Checking,Cash and Bank,,,true\n"
    ",Sales,Income,,,true\n"
    ",Rent Expense,Operating Expense,,,true\n"
)

_HEAD = (
    "Transaction ID,Transaction Date,Account Name,Transaction Description,"
    "Transaction Line Description,{one}Debit Amount (Two Column Approach),"
    "Credit Amount (Two Column Approach),Notes / Memo\n"
)


def _accounting_csv(one_column: bool) -> str:
    head = _HEAD.format(one="Amount (One column)," if one_column else "")

    def one(v: str) -> str:
        return f"{v}," if one_column else ""

    return head + (
        f"T1,2025-01-05,Checking,Sale,Sale,{one('100.00')}100.00,,\n"
        f"T1,2025-01-05,Sales,Sale,Sale,{one('100.00')},100.00,\n"
        f"T2,2025-01-09,Rent Expense,Rent,Rent,{one('750.00')}750.00,,\n"
        f"T2,2025-01-09,Checking,Rent,Rent,{one('-750.00')},750.00,\n"
    )


def _wave(client, path, gl):
    return client.post(
        f"/api/migration/wave/{path}",
        files=_files(
            **{"chart_of_accounts.csv": ACCOUNTING_COA, "general_ledger.csv": gl}
        ),
    )


def test_accounting_csv_two_column_headers_import_what_the_dry_run_promised(
    client, db_session, seed_accounts
):
    from app.models.transactions import Transaction, TransactionLine

    for one_column in (False, True):
        gl = _accounting_csv(one_column)
        dry = _wave(client, "dry-run", gl).json()
        assert dry["ok"], dry["errors"]
        assert dry["journals"] == 2
    done = _wave(client, "import", _accounting_csv(True)).json()
    assert (
        done["ok"] and done["imported_journals"] == 2 and done["skipped_journals"] == 0
    )
    db_session.expire_all()
    txns = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "wave_import")
        .all()
    )
    assert len(txns) == 2
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id.in_([t.id for t in txns]))
        .all()
    )
    assert sorted(str(ln.debit) for ln in lines if ln.debit) == ["100.00", "750.00"]
    assert sum(ln.debit for ln in lines) == sum(ln.credit for ln in lines)


def test_a_ledger_whose_amounts_all_read_zero_is_refused_by_name(
    client, db_session, seed_accounts
):
    """The hole behind #169 and behind 2.11.1's fix before it: 0 == 0 balances,
    so an unrecognised amount column passed the dry run and imported nothing."""
    from app.models.transactions import Transaction

    gl = (
        "Transaction ID,Transaction Date,Account Name,Money In,Money Out\n"
        "T1,2025-01-05,Checking,100.00,\n"
        "T1,2025-01-05,Sales,,100.00,\n"
    )
    dry = _wave(client, "dry-run", gl).json()
    assert dry["ok"] is False
    msg = " ".join(dry["errors"])
    assert "came out as 0.00" in msg and "Money In" in msg and "not recognised" in msg
    done = _wave(client, "import", gl).json()
    assert done["ok"] is False and done["imported_journals"] == 0
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "wave_import")
        .count()
        == 0
    )


def test_a_few_empty_journals_are_a_warning_not_a_refusal(
    client, db_session, seed_accounts
):
    gl = _accounting_csv(False) + "T3,2025-01-10,Checking,Memo only,Memo only,,,\n"
    dry = _wave(client, "dry-run", gl).json()
    assert dry["ok"], dry["errors"]
    assert any(
        "1 journal(s) have no amounts" in w and "T3" in w for w in dry["warnings"]
    )
    done = _wave(client, "import", gl).json()
    assert done["imported_journals"] == 2 and done["skipped_journals"] == 1
