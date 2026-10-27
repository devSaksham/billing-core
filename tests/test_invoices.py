from conftest import credit, make_invoice, pay


def test_invoice_total_is_sum_of_line_items(client):
    inv = make_invoice(client, amounts=(6000, 4000))
    assert inv["total_minor"] == 10000
    assert inv["balance_minor"] == 10000
    assert inv["status"] == "open"
    assert inv["ledger"] == []


def test_partial_then_full_payment_marks_paid(client):
    inv = make_invoice(client)
    r1 = pay(client, inv["id"], 4000, "p1")
    assert r1.status_code == 201
    assert r1.json()["invoice"]["balance_minor"] == 6000
    assert r1.json()["invoice"]["status"] == "open"

    r2 = pay(client, inv["id"], 6000, "p2")
    assert r2.json()["invoice"]["balance_minor"] == 0
    assert r2.json()["invoice"]["status"] == "paid"


def test_overpayment_is_rejected_and_writes_nothing(client):
    inv = make_invoice(client)
    resp = pay(client, inv["id"], 10001, "p1")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "amount_exceeds_balance"
    assert client.get(f"/invoices/{inv['id']}").json()["ledger"] == []


def test_payment_on_settled_invoice_is_rejected(client):
    inv = make_invoice(client)
    pay(client, inv["id"], 10000, "p1")
    resp = pay(client, inv["id"], 1, "p2")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invoice_settled"


def test_credit_reduces_balance(client):
    inv = make_invoice(client)
    resp = credit(client, inv["id"], 2500, "c1", reason="service outage")
    assert resp.status_code == 201
    body = resp.json()["invoice"]
    assert body["balance_minor"] == 7500
    assert body["credited_minor"] == 2500
    assert body["ledger"][0]["note"] == "service outage"


def test_credit_larger_than_balance_is_rejected(client):
    inv = make_invoice(client)
    pay(client, inv["id"], 9000, "p1")
    resp = credit(client, inv["id"], 1500, "c1")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "amount_exceeds_balance"


def test_credit_then_pay_remainder_ends_paid(client):
    inv = make_invoice(client)
    credit(client, inv["id"], 3000, "c1")
    resp = pay(client, inv["id"], 7000, "p1")
    assert resp.json()["invoice"]["status"] == "paid"
    assert resp.json()["invoice"]["balance_minor"] == 0


def test_fully_credited_invoice_is_credited_not_paid(client):
    inv = make_invoice(client)
    resp = credit(client, inv["id"], 10000, "c1")
    assert resp.json()["invoice"]["status"] == "credited"


def test_unknown_invoice_returns_404(client):
    resp = pay(client, "inv_missing", 100, "p1")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "invoice_not_found"
    assert client.get("/invoices/inv_missing").status_code == 404


def test_money_endpoints_require_idempotency_key(client):
    inv = make_invoice(client)
    for path, body in [
        ("/invoices", {"customer_id": "c", "currency": "INR",
                       "line_items": [{"description": "x", "amount_minor": 1}]}),
        (f"/invoices/{inv['id']}/payments", {"amount_minor": 100}),
        (f"/invoices/{inv['id']}/credits", {"amount_minor": 100, "reason": "x"}),
    ]:
        resp = client.post(path, json=body)
        assert resp.status_code == 400, path
        assert resp.json()["error"]["code"] == "idempotency_key_required"


def test_zero_and_negative_amounts_are_rejected(client):
    inv = make_invoice(client)
    assert pay(client, inv["id"], 0, "p1").status_code == 422
    assert pay(client, inv["id"], -5, "p2").status_code == 422


def test_bad_currency_is_rejected(client):
    resp = client.post(
        "/invoices",
        json={"customer_id": "c", "currency": "rupees",
              "line_items": [{"description": "x", "amount_minor": 1}]},
        headers={"Idempotency-Key": "k"},
    )
    assert resp.status_code == 422


def test_error_bodies_always_say_what_to_do_next(client):
    inv = make_invoice(client)
    errors = [
        pay(client, "inv_missing", 1, "a"),
        pay(client, inv["id"], 10001, "b"),
        client.post(f"/invoices/{inv['id']}/payments", json={"amount_minor": 1}),
    ]
    for resp in errors:
        err = resp.json()["error"]
        assert set(err) == {"code", "message", "retryable", "next_step"}
        assert err["next_step"]


def test_proration_preview_endpoint(client):
    resp = client.post(
        "/proration-preview",
        json={
            "old_price_minor": 3000,
            "new_price_minor": 6000,
            "period_start": "2026-11-01",
            "period_end": "2026-12-01",
            "change_on": "2026-11-16",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "days_total": 30,
        "days_remaining": 15,
        "credit_minor": 1500,
        "charge_minor": 3000,
        "net_minor": 1500,
    }


def test_proration_preview_rejects_date_outside_period(client):
    resp = client.post(
        "/proration-preview",
        json={
            "old_price_minor": 3000,
            "new_price_minor": 6000,
            "period_start": "2026-11-01",
            "period_end": "2026-12-01",
            "change_on": "2027-01-01",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_period"
