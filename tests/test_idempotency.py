import sqlite3

from fastapi.testclient import TestClient

from billing.api import create_app
from conftest import credit, make_invoice, pay


def balance(client, invoice_id):
    return client.get(f"/invoices/{invoice_id}").json()["balance_minor"]


def test_same_key_same_body_applies_once(client):
    inv = make_invoice(client)
    first = pay(client, inv["id"], 2500, "pay-1")
    second = pay(client, inv["id"], 2500, "pay-1")

    assert first.status_code == second.status_code == 201
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    assert first.json() == second.json()
    assert balance(client, inv["id"]) == 7500
    assert len(client.get(f"/invoices/{inv['id']}").json()["ledger"]) == 1


def test_same_key_different_body_is_rejected(client):
    inv = make_invoice(client)
    pay(client, inv["id"], 2500, "pay-1")
    resp = pay(client, inv["id"], 9999, "pay-1")

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "idempotency_key_reused"
    assert balance(client, inv["id"]) == 7500


def test_replay_after_invoice_fully_paid_returns_original_result_not_409(client):
    # The retry arrives after the first call already settled the invoice.
    # A naive check of "is there a balance?" would answer 409 and confuse the caller.
    inv = make_invoice(client)
    first = pay(client, inv["id"], 10000, "pay-1")
    retry = pay(client, inv["id"], 10000, "pay-1")

    assert first.status_code == 201
    assert retry.status_code == 201
    assert retry.headers["idempotent-replayed"] == "true"
    assert retry.json() == first.json()


def test_same_key_on_different_invoices_is_independent(client):
    a = make_invoice(client, key="inv-a")
    b = make_invoice(client, key="inv-b")
    pay(client, a["id"], 1000, "shared-key")
    resp = pay(client, b["id"], 1000, "shared-key")

    assert resp.status_code == 201
    assert "idempotent-replayed" not in resp.headers
    assert balance(client, a["id"]) == balance(client, b["id"]) == 9000


def test_same_key_on_payment_and_credit_is_independent(client):
    inv = make_invoice(client)
    assert pay(client, inv["id"], 1000, "k").status_code == 201
    resp = credit(client, inv["id"], 500, "k")
    assert resp.status_code == 201
    assert "idempotent-replayed" not in resp.headers
    assert balance(client, inv["id"]) == 8500


def test_create_invoice_twice_with_same_key_returns_same_invoice(client):
    first = make_invoice(client, key="create-1")
    second = make_invoice(client, key="create-1")
    assert first["id"] == second["id"]


def test_failed_request_is_not_locked_in_by_its_key(client):
    # Only successful results are stored. A rejected overpayment can be
    # corrected and resent under the same key.
    inv = make_invoice(client)
    bad = pay(client, inv["id"], 99999, "pay-1")
    assert bad.status_code == 409
    good = pay(client, inv["id"], 1000, "pay-1")
    assert good.status_code == 201
    assert balance(client, inv["id"]) == 9000


def test_credit_is_idempotent_too(client):
    inv = make_invoice(client)
    credit(client, inv["id"], 2000, "c-1")
    credit(client, inv["id"], 2000, "c-1")
    assert balance(client, inv["id"]) == 8000


def test_locked_database_returns_retryable_503_and_retry_succeeds(db_path):
    app = create_app(db_path, busy_timeout_s=0.05)
    client = TestClient(app)
    inv = make_invoice(client)

    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # another writer holds the lock
    try:
        resp = pay(client, inv["id"], 1000, "pay-1")
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    assert resp.status_code == 503
    err = resp.json()["error"]
    assert err["code"] == "database_busy"
    assert err["retryable"] is True
    assert balance(client, inv["id"]) == 10000  # nothing was written

    retry = pay(client, inv["id"], 1000, "pay-1")  # same key, as the error says
    assert retry.status_code == 201
    assert balance(client, inv["id"]) == 9000
