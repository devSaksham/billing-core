import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .errors import ApiError


@dataclass(frozen=True)
class Outcome:
    status: int
    body: dict
    replayed: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_body(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _busy(exc: sqlite3.OperationalError) -> ApiError:
    return ApiError(
        503,
        "database_busy",
        "The database was locked by another write and did not free up in time.",
        "Retry the same request with the same Idempotency-Key. It is safe to repeat.",
        retryable=True,
    )


def execute(
    conn: sqlite3.Connection,
    scope: str,
    key: str,
    request_body: dict,
    action: Callable[[sqlite3.Connection], tuple[int, dict]],
) -> Outcome:
    """Run `action` at most once per (scope, key).

    The key lookup, the money movement and the key insert all happen inside one
    BEGIN IMMEDIATE transaction. SQLite allows one writer at a time, so two
    requests with the same key cannot both pass the lookup. The second one waits,
    then finds the stored result and replays it.
    """
    request_hash = _hash_body(request_body)
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc) or "busy" in str(exc):
            raise _busy(exc)
        raise

    try:
        row = conn.execute(
            "SELECT request_hash, status_code, response_json "
            "FROM idempotency_keys WHERE scope = ? AND key = ?",
            (scope, key),
        ).fetchone()

        if row is not None:
            if row["request_hash"] != request_hash:
                raise ApiError(
                    422,
                    "idempotency_key_reused",
                    "This Idempotency-Key was already used with a different request body.",
                    "Use a new Idempotency-Key for a different request, "
                    "or resend the original body to get the original result.",
                )
            conn.execute("COMMIT")
            return Outcome(row["status_code"], json.loads(row["response_json"]), True)

        status, body = action(conn)
        conn.execute(
            "INSERT INTO idempotency_keys "
            "(scope, key, request_hash, status_code, response_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scope, key, request_hash, status, json.dumps(body), _now()),
        )
        conn.execute("COMMIT")
        return Outcome(status, body, False)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def summarize(conn: sqlite3.Connection, invoice_id: str) -> dict:
    inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if inv is None:
        raise ApiError(
            404,
            "invoice_not_found",
            f"No invoice with id {invoice_id}.",
            "Check the invoice id. Create the invoice first with POST /invoices.",
        )
    totals = {"payment": 0, "credit": 0}
    for r in conn.execute(
        "SELECT kind, SUM(amount_minor) AS s FROM ledger WHERE invoice_id = ? GROUP BY kind",
        (invoice_id,),
    ):
        totals[r["kind"]] = r["s"]

    balance = inv["total_minor"] - totals["payment"] - totals["credit"]
    if balance > 0:
        status = "open"
    elif totals["payment"] > 0:
        status = "paid"
    else:
        status = "credited"

    ledger = [
        {
            "id": r["id"],
            "kind": r["kind"],
            "amount_minor": r["amount_minor"],
            "note": r["note"],
            "created_at": r["created_at"],
        }
        for r in conn.execute(
            "SELECT id, kind, amount_minor, note, created_at FROM ledger "
            "WHERE invoice_id = ? ORDER BY id",
            (invoice_id,),
        )
    ]
    return {
        "id": inv["id"],
        "customer_id": inv["customer_id"],
        "currency": inv["currency"],
        "total_minor": inv["total_minor"],
        "paid_minor": totals["payment"],
        "credited_minor": totals["credit"],
        "balance_minor": balance,
        "status": status,
        "ledger": ledger,
    }


def create_invoice(data: dict) -> Callable[[sqlite3.Connection], tuple[int, dict]]:
    def action(conn: sqlite3.Connection) -> tuple[int, dict]:
        invoice_id = "inv_" + uuid.uuid4().hex[:16]
        total = sum(item["amount_minor"] for item in data["line_items"])
        conn.execute(
            "INSERT INTO invoices (id, customer_id, currency, total_minor, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (invoice_id, data["customer_id"], data["currency"], total, _now()),
        )
        for item in data["line_items"]:
            conn.execute(
                "INSERT INTO line_items (invoice_id, description, amount_minor) VALUES (?, ?, ?)",
                (invoice_id, item["description"], item["amount_minor"]),
            )
        return 201, summarize(conn, invoice_id)

    return action


def _post_entry(
    invoice_id: str, kind: str, amount_minor: int, note: str | None, key: str
) -> Callable[[sqlite3.Connection], tuple[int, dict]]:
    def action(conn: sqlite3.Connection) -> tuple[int, dict]:
        current = summarize(conn, invoice_id)  # raises 404 if missing
        balance = current["balance_minor"]
        if balance == 0:
            raise ApiError(
                409,
                "invoice_settled",
                f"Invoice {invoice_id} has no balance left.",
                "Nothing to do. Fetch the invoice with GET /invoices/{id} to see its ledger.",
            )
        if amount_minor > balance:
            raise ApiError(
                409,
                "amount_exceeds_balance",
                f"{kind} of {amount_minor} is more than the {balance} still owed.",
                f"Resend with amount_minor of {balance} or less.",
            )
        cur = conn.execute(
            "INSERT INTO ledger (invoice_id, kind, amount_minor, note, idempotency_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (invoice_id, kind, amount_minor, note, key, _now()),
        )
        return 201, {"entry_id": cur.lastrowid, "invoice": summarize(conn, invoice_id)}

    return action


def apply_payment(invoice_id: str, amount_minor: int, key: str):
    return _post_entry(invoice_id, "payment", amount_minor, None, key)


def apply_credit(invoice_id: str, amount_minor: int, reason: str, key: str):
    return _post_entry(invoice_id, "credit", amount_minor, reason, key)
