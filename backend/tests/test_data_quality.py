"""
Task 16 — Data Quality & Reconciliation tests.

Needs the same disposable Postgres test database as test_audit_logs.py
(see conftest.py). They create a deliberate inconsistency with a direct SQL
UPDATE (the API correctly refuses to create one), then check detection,
de-duplication, auto-resolution, resolution rules and company isolation.
"""
import pytest
from sqlalchemy import text

from tests.conftest import create_product


def _break_inventory(engine, product_id, quantity=999):
    with engine.begin() as conn:
        conn.execute(text("UPDATE inventory SET current_stock = :q WHERE product_id = :p"),
                     {"q": quantity, "p": product_id})


def _run(client, headers):
    resp = client.post("/data-quality/runs", headers=headers)
    assert resp.status_code == 202, resp.text
    return resp.json()


def _issues(client, headers, **params):
    resp = client.get("/data-quality/issues", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_reconciliation_detects_inconsistency(company_a, engine):
    client, headers = company_a["client"], company_a["headers"]
    product = create_product(client, headers, stock=20)
    _break_inventory(engine, product["id"])

    _run(client, headers)
    found = _issues(client, headers, search="PLACEHOLDER-NONE")  # sanity: search filters work
    assert found["total"] == 0

    issues = _issues(client, headers)
    codes = {i["issue_code"] for i in issues["items"]}
    assert "PRODUCT_STOCK_DRIFT" in codes
    assert all(i["status"] == "OPEN" for i in issues["items"] if i["issue_code"] == "PRODUCT_STOCK_DRIFT")


def test_rerunning_does_not_create_duplicates(company_a, engine):
    client, headers = company_a["client"], company_a["headers"]
    product = create_product(client, headers, stock=20)
    _break_inventory(engine, product["id"])

    _run(client, headers)
    first = _issues(client, headers)["total"]
    _run(client, headers)
    assert _issues(client, headers)["total"] == first


def test_fixed_problem_is_auto_resolved_on_next_run(company_a, engine):
    client, headers = company_a["client"], company_a["headers"]
    product = create_product(client, headers, stock=20)
    _break_inventory(engine, product["id"])
    _run(client, headers)

    _break_inventory(engine, product["id"], quantity=20)  # put it back in line
    _run(client, headers)

    drift = [i for i in _issues(client, headers)["items"] if i["issue_code"] == "PRODUCT_STOCK_DRIFT"]
    assert drift and all(i["status"] == "RESOLVED" for i in drift)


def test_resolving_requires_a_note_and_records_who(company_a, engine):
    client, headers = company_a["client"], company_a["headers"]
    product = create_product(client, headers, stock=20)
    _break_inventory(engine, product["id"])
    _run(client, headers)
    issue = next(i for i in _issues(client, headers)["items"] if i["issue_code"] == "PRODUCT_STOCK_DRIFT")

    no_note = client.patch(f"/data-quality/issues/{issue['id']}/status", json={"status": "RESOLVED"}, headers=headers)
    assert no_note.status_code == 400

    ok = client.patch(f"/data-quality/issues/{issue['id']}/status",
                      json={"status": "RESOLVED", "resolution_note": "Corrected after a stock count"}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["status"] == "RESOLVED"
    assert ok.json()["resolved_by_name"]

    detail = client.get(f"/data-quality/issues/{issue['id']}", headers=headers).json()
    assert detail["events"][-1]["previous_status"] == "OPEN"
    assert detail["events"][-1]["new_status"] == "RESOLVED"


def test_company_cannot_see_another_companys_issues(company_a, company_b, engine):
    client = company_a["client"]
    product = create_product(client, company_a["headers"], stock=20)
    _break_inventory(engine, product["id"])
    _run(client, company_a["headers"])

    a_issue = _issues(client, company_a["headers"])["items"][0]
    assert _issues(client, company_b["headers"])["total"] == 0
    assert client.get(f"/data-quality/issues/{a_issue['id']}", headers=company_b["headers"]).status_code == 404
    assert client.patch(f"/data-quality/issues/{a_issue['id']}/status",
                        json={"status": "INVESTIGATING"}, headers=company_b["headers"]).status_code == 404


def test_summary_and_history_reflect_the_run(company_a, engine):
    client, headers = company_a["client"], company_a["headers"]
    product = create_product(client, headers, stock=20)
    _break_inventory(engine, product["id"])
    run = _run(client, headers)

    summary = client.get("/data-quality/summary", headers=headers).json()
    assert summary["unresolved_issues"] >= 1
    assert summary["last_reconciliation_at"] is not None

    history = client.get("/data-quality/runs", headers=headers).json()
    assert any(r["id"] == run["id"] for r in history["items"])
    assert history["items"][0]["status"] in ("COMPLETED", "COMPLETED_WITH_ISSUES")


def test_viewer_role_cannot_use_data_quality(company_a):
    pytest.skip("No endpoint exists to create a non-admin user in this build.")
