"""The other end of the vocabulary chain: the sentences the server sends.

tests/test_terminology.py proves every page label goes through the
dictionary. The vocabulary audit's walk of a running server
(scripts/audit/vocab_walk.py) found what that could not: 42 HTTP error
sentences, the control-account purposes, the AI-action labels and one
analytics empty state still said "Invoice" and "Customer" to a nonprofit
whose screens say Pledge and Donor. These pin the connections.
"""

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services.terminology import NONPROFIT

BUSINESS = ("Customer", "Invoice", "Job", "customers", "invoices", "P&L", "A/R")


def _nonprofit(client):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "path, business, nonprofit",
    [
        ("/api/invoices/999999", "Invoice not found", "Pledge not found"),
        ("/api/customers/999999", "Customer not found", "Donor not found"),
        ("/api/jobs/999999", "Job not found", "Grant not found"),
    ],
)
def test_error_sentences_follow_the_company_type(client, path, business, nonprofit):
    """One place, every HTTPException: the same 404 says Invoice to a
    business and Pledge to a nonprofit, and switching back restores it."""
    assert client.get(path).json()["detail"] == business
    _nonprofit(client)
    assert client.get(path).json()["detail"] == nonprofit
    client.put("/api/settings", json={"company_type": "business"})
    assert client.get(path).json()["detail"] == business


def test_protected_words_survive_the_chokepoint(client, monkeypatch):
    """The dictionary's own rule — "Sales Tax" never becomes anything —
    holds at the boundary too, and a sentence with nothing to swap is
    passed to the default handler untouched (status and headers kept)."""
    from app import main as m

    _nonprofit(client)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [],
        "query_string": b"",
    }
    req = Request(scope)
    exc = HTTPException(
        status_code=422, detail="Sales Tax rate is required", headers={"X-Probe": "1"}
    )
    resp = asyncio.run(m._method_not_allowed_handler(req, exc))
    assert resp.status_code == 422
    assert b"Sales Tax rate is required" in resp.body
    assert resp.headers.get("x-probe") == "1"


def test_missing_control_account_speaks_the_company_words(
    client, db_session, seed_accounts
):
    """MissingControlAccount's sentence says "what customers owe — every
    invoice and payment"; a nonprofit reads donors and pledge."""
    _nonprofit(client)
    c = client.post("/api/customers", json={"name": "Grant Foundation"})
    assert c.status_code in (200, 201), c.text
    seed_accounts["1100"].account_number = "1105"
    db_session.commit()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": c.json()["id"],
            "date": "2026-01-06",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 40, "line_order": 0}],
        },
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "1100 Accounts Receivable" in detail  # the account's own name is data
    assert "what donors owe" in detail and "every pledge and payment" in detail
    assert "customers" not in detail and "invoice" not in detail.lower()


def test_control_purpose_on_the_chart_follows_the_company_type(client, seed_accounts):
    def purpose(number):
        return next(
            a
            for a in client.get("/api/accounts").json()
            if a["account_number"] == number
        )["control_purpose"]

    assert purpose("1100") == "what customers owe — every invoice and payment"
    _nonprofit(client)
    assert purpose("1100") == "what donors owe — every pledge and payment"
    one = client.get(f"/api/accounts/{seed_accounts['1100'].id}").json()
    assert one["control_purpose"] == "what donors owe — every pledge and payment"


def test_ai_action_labels_follow_the_company_type(client):
    groups = client.get("/api/analytics/ai-actions").json()["groups"]
    assert any(g["category"] == "Customers & Sales" for g in groups)
    _nonprofit(client)
    groups = client.get("/api/analytics/ai-actions").json()["groups"]
    texts = [g["category"] for g in groups] + [
        a["label"] for g in groups for a in g["actions"]
    ]
    assert "Donors & Contributions" in texts
    leaks = [t for t in texts if any(w in t for w in BUSINESS)]
    assert leaks == [], leaks


def test_analytics_empty_state_is_wrapped_on_page_and_pdf():
    """The 2.9.1 sweep is case-sensitive, so "No paid invoices this period."
    slipped it on both surfaces. Pinned here rather than by loosening the
    sweep, which flags 136 lines case-insensitively — that is its own pass."""
    from pathlib import Path

    from app.services.pdf_service import _jinja_env
    from app.services.terminology import Terms

    js = Path("app/static/js/analytics.js").read_text(encoding="utf-8")
    assert "Terms.text('No paid invoices this period.')" in js

    class _Empty:
        """Every attribute and item is another empty thing: enough to walk
        the template's structure without a real dashboard."""

        def __getattr__(self, _k):
            return _Empty()

        def __getitem__(self, _k):
            return _Empty()

        def __iter__(self):
            return iter(())

        def __len__(self):
            return 0

        def __bool__(self):
            return False

        def __str__(self):
            return ""

        def __format__(self, _spec):
            return ""

        def __call__(self, *_a, **_k):
            return _Empty()

        def items(self):
            return []

        def get(self, *_a):
            return _Empty()

    html = _jinja_env.get_template("analytics_pdf.html").render(
        dashboard=_Empty(),
        period=_Empty(),
        company=_Empty(),
        terms=Terms("nonprofit"),
        company_logo_data_uri=None,
    )
    assert "No paid pledges this period." in html and "No paid invoices" not in html


def test_every_dictionary_key_is_reachable_through_the_chokepoint():
    """A sentence made of each key swaps under the handler's own helper —
    the boundary uses the same text() every page uses, nothing bespoke."""
    from app.services.terminology import Terms

    t = Terms("nonprofit")
    for key, value in NONPROFIT.items():
        assert t.text(f"{key} not found") == f"{value} not found", key
    # the walk found the case rule treating an upper-case KEY as shouting:
    # "P&L analysis" came out "ACTIVITIES analysis"
    assert t.text("P&L analysis") == "Activities analysis"
    assert (
        t.text("SEE THE INVOICE") == "SEE THE PLEDGE"
    )  # a shouted sentence still shouts


def test_the_untagged_job_bucket_is_named_in_the_company_words(client, seed_accounts):
    """The job-profitability report labels untagged activity "No job"; the
    walk found it reaching a nonprofit unchanged on two routes."""
    _nonprofit(client)
    for path in ("/api/jobs/profitability", "/api/reports/job-profitability"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
        assert "No job" not in r.text, path


def test_a_lowercase_word_takes_a_wholly_lowercase_phrase():
    """'equity' in running text became 'net Assets' — only the first letter of
    a multi-word replacement was lowered. Found on the 2.16.0 gate in a card
    description. Python and JS carry the same rule."""
    from pathlib import Path

    from app.services.terminology import Terms

    t = Terms("nonprofit")
    assert t.text("Assets, liabilities and equity at each month end") == (
        "Assets, liabilities and net assets at each month end"
    )
    assert t.text("Equity at month end") == "Net Assets at month end"
    assert t.text("income for the year") == "revenue & support for the year"
    js = (Path(__file__).parents[1] / "app/static/js/terms.js").read_text(
        encoding="utf-8"
    )
    assert "w.toLowerCase()).join(' ')" in js
