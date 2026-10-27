"""Real threads, each with its own SQLite connection, hitting one database file."""
import threading
from concurrent.futures import ThreadPoolExecutor

from billing import service
from billing.db import connect, init_db
from billing.errors import ApiError


def _new_invoice(db_path, total):
    conn = connect(db_path, busy_timeout_s=30)
    try:
        data = {
            "customer_id": "cus_1",
            "currency": "INR",
            "line_items": [{"description": "plan", "amount_minor": total}],
        }
        out = service.execute(conn, "POST /invoices", "setup", data, service.create_invoice(data))
        return out.body["id"]
    finally:
        conn.close()


def _pay(db_path, invoice_id, amount, key, barrier):
    conn = connect(db_path, busy_timeout_s=30)
    try:
        barrier.wait()  # release all threads at the same moment
        return service.execute(
            conn,
            f"POST /invoices/{invoice_id}/payments",
            key,
            {"amount_minor": amount},
            service.apply_payment(invoice_id, amount, key),
        )
    finally:
        conn.close()


def _read(db_path, invoice_id):
    conn = connect(db_path)
    try:
        return service.summarize(conn, invoice_id)
    finally:
        conn.close()


def test_same_request_sent_20_times_at_once_changes_balance_once(db_path):
    init_db(db_path)
    invoice_id = _new_invoice(db_path, 10000)
    barrier = threading.Barrier(20)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [
            pool.submit(_pay, db_path, invoice_id, 500, "same-key", barrier)
            for _ in range(20)
        ]
        outcomes = [f.result() for f in futures]

    assert all(o.status == 201 for o in outcomes)
    assert sum(1 for o in outcomes if not o.replayed) == 1
    assert sum(1 for o in outcomes if o.replayed) == 19
    assert len({str(o.body) for o in outcomes}) == 1  # everyone saw the same answer

    summary = _read(db_path, invoice_id)
    assert summary["balance_minor"] == 9500
    assert len(summary["ledger"]) == 1


def test_many_different_payments_never_overpay_the_invoice(db_path):
    init_db(db_path)
    invoice_id = _new_invoice(db_path, 1500)
    barrier = threading.Barrier(20)

    def attempt(i):
        try:
            return _pay(db_path, invoice_id, 100, f"key-{i}", barrier)
        except ApiError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))

    accepted = [r for r in results if not isinstance(r, ApiError)]
    rejected = [r for r in results if isinstance(r, ApiError)]
    assert len(accepted) == 15  # 15 x 100 = 1500
    assert len(rejected) == 5
    assert {r.code for r in rejected} <= {"invoice_settled", "amount_exceeds_balance"}

    summary = _read(db_path, invoice_id)
    assert summary["balance_minor"] == 0
    assert summary["paid_minor"] == 1500
    assert summary["status"] == "paid"
