# Decisions

Short notes on why the code looks the way it does, including what I would change.

## 1. Money is integer minor units

Floats cannot represent 0.1 exactly, and rounding errors in billing become real disputes. Every amount is an integer (paise or cents). Proration uses integer arithmetic only: `(2n + d) // (2d)` is round half up with no float anywhere.

## 2. Balances are derived, the ledger is append-only

The invoice row stores only its total. Payments and credits are rows in `ledger`. Balance is `total - payments - credits`, computed on read. There is no stored balance column that can drift out of sync with the ledger, and the history of what happened is never overwritten.

## 3. The idempotency check and the money movement share one transaction

The core of `service.execute`:

1. `BEGIN IMMEDIATE` (takes the write lock up front).
2. Look up `(scope, key)`.
3. If found: compare the request hash, then replay the stored response.
4. If not found: run the action, insert the key and its response, `COMMIT`.

If the lookup and the write were separate transactions, two simultaneous requests could both miss the lookup and both write. Because `BEGIN IMMEDIATE` makes writers queue, the second request waits, then sees the stored key and replays. `tests/test_concurrency.py` fires 20 identical requests at the same instant. I also checked the test is not vacuous: with the key lookup disabled it fails.

The `UNIQUE (invoice_id, kind, idempotency_key)` constraint on the ledger is a second line of defence, not the main one.

## 4. The key is scoped to the endpoint and path

The stored scope is the method plus path, for example `POST /invoices/inv_1/payments`. The same key string used on a different invoice, or on credits instead of payments, is independent. A client that reuses a key across unrelated calls does not get a wrong replay.

## 5. Same key, different body is an error

A request hash (SHA-256 of canonical JSON) is stored with the key. If the key comes back with a different body it is almost certainly a client bug, and silently replaying the old result would hide it. It returns `422 idempotency_key_reused`.

## 6. Replay is checked before business rules

A retry that arrives after the first call settled the invoice must get the original `201`, not `409 invoice_settled`. The key lookup runs before the balance checks for that reason. There is a test for exactly this case.

## 7. Only successful results are stored

If a request fails a business rule (for example an overpayment), nothing is stored under its key, so the caller can fix the amount and resend with the same key. The tradeoff: a failure is re-evaluated against current state on every retry. Stripe-style APIs also store errors. I kept it simple and would revisit this with a real client in mind.

## 8. Lock timeouts are retryable errors, not crashes

If another writer holds the lock past the timeout, the API returns `503 database_busy` with `retryable: true`, and the next step says to resend with the same key. Nothing was written, so retrying is safe. There is a test that holds the lock from another connection.

## 9. Errors say what to do next

Every error has `code`, `message`, `retryable` and `next_step`. A caller (or a person reading logs at 2am) should not have to guess whether to retry, change the request or give up.

## 10. What I would do differently with more time

- Move to Postgres and replace the single-writer guarantee with a unique constraint on `(scope, key)` plus `SELECT ... FOR UPDATE` on the invoice row.
- Expire idempotency keys.
- Store an "in progress" state for long-running actions that call an external payment gateway, since a gateway call cannot sit inside a database transaction.
- Add refunds and a proper invoice state machine (draft, open, paid, void).
