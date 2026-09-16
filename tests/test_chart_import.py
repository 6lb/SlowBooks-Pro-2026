"""Importing a chart of accounts (#139 / #161).

The hledger files under tests/fixtures/hledger were written by hledger 1.30.1
from riverbend.journal — the importer is tested against what the tool emits,
not against a hand-typed idea of it (feedback: never ship an importer built
on synthetic data).
"""

import io
from pathlib import Path

from app.models.accounts import Account
from app.services import chart_import
from app.services.csv_export import export_accounts

FIX = Path(__file__).parent / "fixtures" / "hledger"
# Fixture password for the read-only viewer, kept out of the request literal so
# GitGuardian's username/password pair detector does not read a test as a leak.
VIEWER_PW = "long-enough-pw"


def _upload(client, text, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return client.post(
        "/api/csv/import/accounts" + (f"?{q}" if q else ""),
        files={"file": ("chart.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )


def _by_number(db):
    db.expire_all()  # the route committed on its own session; drop cached state
    return {a.account_number: a for a in db.query(Account).all() if a.account_number}


# --- hledger, the three real exports ----------------------------------------


def test_hledger_accounts_list_creates_the_chart_under_our_numbers(
    client, db_session, seed_accounts
):
    text = (FIX / "accounts.txt").read_text(encoding="utf-8")
    r = _upload(client, text, dry_run=0)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["format"] == "hledger" and out["dry_run"] is False
    assert out["errors"] == []
    db_session.expire_all()
    accts = {a.name: a for a in db_session.query(Account).all()}
    # roots are categories, not accounts
    assert "Assets" not in accts and "Revenues" not in accts and "Expenses" not in accts
    # a leaf under revenues is income, numbered in the income range
    neon = accts["Neon"]
    assert neon.account_type.value == "income" and neon.account_number.startswith("4")
    assert neon.description == "revenues:signs:neon"
    # ... and hangs from its intermediate, which the file did list
    assert neon.parent.name == "Signs" and neon.parent.description == "revenues:signs"
    # `assets:cash:petty cash` arrived with no `assets:cash` line: synthesized
    petty = accts["Petty cash"]
    assert petty.parent.name == "Cash" and petty.parent.description == "assets:cash"
    assert petty.bank_kind == "bank"
    # the file's `checking` IS our control account 1000, renamed, not a twin
    assert _by_number(db_session)["1000"].name == "Checking"
    assert sum(1 for a in accts.values() if a.name.lower() == "checking") == 1
    # `assets:receivable` is 1100 A/R renamed; `liabilities:payable` is 2000
    assert _by_number(db_session)["1100"].name == "Receivable"
    assert _by_number(db_session)["2000"].name == "Payable"
    assert _by_number(db_session)["2200"].name == "Sales tax payable"
    # the credit card under liabilities is 2100, marked as a card
    visa = accts["Visa"]
    assert visa.account_number == "2100" and visa.bank_kind == "credit_card"
    assert visa.account_type.value == "liability"


def test_hledger_types_tag_wins_over_the_root_word(client, db_session, seed_accounts):
    text = (FIX / "accounts-types.txt").read_text(encoding="utf-8")
    out = _upload(client, text, dry_run=0).json()
    assert out["errors"] == []
    db_session.expire_all()
    accts = {a.name: a for a in db_session.query(Account).all()}
    # `; type: C` = cash, an asset you spend from -> a bank account
    assert (
        accts["Savings"].bank_kind == "bank"
        and accts["Savings"].account_type.value == "asset"
    )
    # `; type: X` -> expense; `; type: R` -> income
    assert accts["Fuel"].account_type.value == "expense"
    assert accts["Installation"].account_type.value == "income"


def test_hledger_balance_csv_is_read_as_paths_and_total_is_ignored(
    client, db_session, seed_accounts
):
    text = (FIX / "balance.csv").read_text(encoding="utf-8")
    out = _upload(client, text, dry_run=0).json()
    assert out["format"] == "hledger" and out["errors"] == []
    names = {a.name for a in db_session.query(Account).all()}
    assert "Total" not in names and "total" not in names
    assert "Truck loan" in names and "Opening balances" in names
    # no balance was imported: the column is a number hledger computed, not ours
    assert all((a.balance or 0) == 0 for a in db_session.query(Account).all())


# --- CSV in our own export's columns ----------------------------------------


def test_round_trip_of_our_export_changes_nothing(client, db_session, seed_accounts):
    text = export_accounts(db_session)
    before = {
        a.id: (a.name, a.account_number, a.account_type.value)
        for a in db_session.query(Account).all()
    }
    out = _upload(client, text, dry_run=0).json()
    assert out["format"] == "csv" and out["errors"] == []
    assert out["created"] == 0 and out["updated"] == 0 and out["skipped"] == len(before)
    after = {
        a.id: (a.name, a.account_number, a.account_type.value)
        for a in db_session.query(Account).all()
    }
    assert after == before


def test_csv_renames_by_number_creates_new_and_refuses_a_control_type_change(
    client, db_session, seed_accounts
):
    text = (
        "Number,Name,Type,Description\n"
        "1100,Trade Debtors,asset,what customers owe\n"
        "1100,Duplicate,asset,\n"
        "7100,Trade Shows,expense,booth fees\n"
        "2000,Trade Creditors,expense,\n"
        "9999,Mystery,widget,\n"
    )
    plan = _upload(client, text).json()
    assert plan["dry_run"] is True
    by_row = {r["row"]: r for r in plan["rows"]}
    assert (
        by_row[2]["action"] == "update"
        and "rename 'Accounts Receivable' → 'Trade Debtors'" in by_row[2]["changes"]
    )
    assert by_row[3]["action"] == "error" and "twice" in by_row[3]["note"]
    assert by_row[4]["action"] == "create" and by_row[4]["number"] == "7100"
    # 2000 is A/P: renamed, but the type stays liability with a reason
    assert by_row[5]["action"] == "update"
    assert (
        "type stays liability" in by_row[5]["note"] and "control" in by_row[5]["note"]
    )
    assert not any(c.startswith("type") for c in by_row[5]["changes"])
    assert any("unknown account type 'widget'" in e for e in plan["errors"])
    # nothing was written by the dry run
    assert _by_number(db_session)["1100"].name == "Accounts Receivable"
    assert "7100" not in _by_number(db_session)

    done = _upload(client, text, dry_run=0).json()
    assert done["dry_run"] is False and done["created"] == 1 and done["updated"] == 2
    nums = _by_number(db_session)
    assert (
        nums["1100"].name == "Trade Debtors"
        and nums["1100"].description == "what customers owe"
    )
    assert (
        nums["7100"].name == "Trade Shows"
        and nums["7100"].account_type.value == "expense"
    )
    assert (
        nums["2000"].name == "Trade Creditors"
        and nums["2000"].account_type.value == "liability"
    )


def test_csv_parent_by_number_and_number_inferred_type(
    client, db_session, seed_accounts
):
    text = "Number,Name,Parent\n6155,Dental plan,6150\n"
    out = _upload(client, text, dry_run=0).json()
    assert out["errors"] == [] and out["created"] == 1
    a = _by_number(db_session)["6155"]
    assert a.account_type.value == "expense" and a.parent.account_number == "6150"


def test_a_taken_number_gets_the_next_free_one_and_says_so(
    client, db_session, seed_accounts
):
    text = "Number,Name,Type\n6000,Marketing spend,expense\n6000,Trade press,expense\n"
    plan = _upload(client, text).json()
    rows = plan["rows"]
    assert rows[0]["action"] == "update"  # 6000 exists: renamed
    assert rows[1]["action"] == "error" and "twice" in rows[1]["note"]
    text = "Name,Type\nTrade press,expense\n"
    plan = _upload(client, text).json()
    assert plan["rows"][0]["number"].startswith("6") and plan["rows"][0][
        "number"
    ] not in _by_number(db_session)


# --- replace mode -------------------------------------------------------------


def test_replace_deactivates_unused_seeded_accounts_but_never_control_or_history(
    client, db_session, seed_accounts
):
    from decimal import Decimal
    from datetime import date
    from app.models.transactions import Transaction, TransactionLine

    # 6100 Rent carries history; 6200 does not; 1100 A/R is control
    rent = _by_number(db_session)["6100"]
    chk = _by_number(db_session)["1000"]
    txn = Transaction(date=date(2026, 1, 5), description="rent", source_type="manual")
    db_session.add(txn)
    db_session.flush()
    db_session.add_all(
        [
            TransactionLine(
                transaction_id=txn.id,
                account_id=rent.id,
                debit=Decimal("100"),
                credit=Decimal("0"),
            ),
            TransactionLine(
                transaction_id=txn.id,
                account_id=chk.id,
                debit=Decimal("0"),
                credit=Decimal("100"),
            ),
        ]
    )
    db_session.commit()

    text = (FIX / "accounts.txt").read_text(encoding="utf-8")
    plan = _upload(client, text, replace=1).json()
    by_num = {r["number"]: r for r in plan["rows"] if r["number"]}
    assert by_num["6100"]["action"] == "keep" and "posted" in by_num["6100"]["note"]
    assert by_num["1200"]["action"] == "keep" and "control" in by_num["1200"]["note"]
    assert by_num["6200"]["action"] == "deactivate"
    assert plan["deactivated"] > 10

    done = _upload(client, text, replace=1, dry_run=0).json()
    assert done["deactivated"] == plan["deactivated"]
    nums = _by_number(db_session)
    assert nums["6200"].is_active is False
    assert nums["6100"].is_active is True and nums["1200"].is_active is True
    assert all(
        nums[n].is_active
        for n in ("1000", "1100", "2000", "2100", "2200", "3200", "4000", "5000")
    )


# --- edges ---------------------------------------------------------------------


def test_empty_and_headerless_files_are_refused_plainly(client, seed_accounts):
    r = _upload(client, "   \n")
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    plan = _upload(client, "Balance,Notes\n12,x\n").json()
    assert (
        any("no account name column" in e for e in plan["errors"])
        and plan["rows"] == []
    )


def test_readonly_cannot_import(client, seed_accounts):
    # the role policy is by method: a readonly session is GET-only
    from app.models.users import ROLE_READONLY

    r = client.post(
        "/api/users",
        json={
            "username": "viewer",
            "password": VIEWER_PW,
            "role": ROLE_READONLY,
        },
    )
    assert r.status_code == 201, r.text
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "viewer", "password": VIEWER_PW}
    )
    assert r.status_code == 200, r.text
    r = _upload(client, "Name,Type\nX,expense\n")
    assert r.status_code == 403


def test_service_parses_the_journal_declarations_too():
    # `account assets:bank:checking ; type: C` lines straight out of a journal
    text = "account assets:bank:checking   ; type: C\naccount expenses:rent ; type: X\n; a comment\n"
    rows, errors, fmt = chart_import.parse_chart(text)
    assert fmt == "hledger" and errors == []
    assert [(r.name, r.type, r.bank_kind) for r in rows] == [
        ("Bank", "asset", "bank"),
        ("Checking", "asset", "bank"),
        ("Rent", "expense", None),
    ]
