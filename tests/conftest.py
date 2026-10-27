import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from billing.api import create_app  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    # A real file, not :memory:, so concurrent connections share state.
    return str(tmp_path / "billing_test.db")


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


def make_invoice(client, amounts=(6000, 4000), key="inv-key-1", currency="INR"):
    resp = client.post(
        "/invoices",
        json={
            "customer_id": "cus_1",
            "currency": currency,
            "line_items": [
                {"description": f"item {i}", "amount_minor": a}
                for i, a in enumerate(amounts)
            ],
        },
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def pay(client, invoice_id, amount, key):
    return client.post(
        f"/invoices/{invoice_id}/payments",
        json={"amount_minor": amount},
        headers={"Idempotency-Key": key},
    )


def credit(client, invoice_id, amount, key, reason="goodwill"):
    return client.post(
        f"/invoices/{invoice_id}/credits",
        json={"amount_minor": amount, "reason": reason},
        headers={"Idempotency-Key": key},
    )
