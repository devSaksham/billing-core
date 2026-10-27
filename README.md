# billing-core

A small invoice API that stays correct when clients retry.

Billing code fails in boring ways. A client times out and resends. A worker dies halfway. Someone gets charged twice. This project is a small, runnable answer to that: invoices, payments, credits and mid-cycle proration, with idempotency built into every call that moves money.

It is a learning project, not a production system. See "Limits" below.

Live page with a retry simulator: https://devsaksham.github.io/billing-core/ (source in `docs/`).

## What it does

- `POST /invoices` creates an invoice from line items (money is integer minor units, never floats).
- `POST /invoices/{id}/payments` applies a payment.
- `POST /invoices/{id}/credits` applies a credit (a reduction, with a reason).
- `GET /invoices/{id}` returns totals, balance, status and the full ledger.
- `POST /proration-preview` prices a mid-cycle plan change by whole days, rounded half up.

Every POST that moves money needs an `Idempotency-Key` header.

## The behaviour worth looking at

- Same key, same body: applied once. Every retry gets the original response back, marked `Idempotent-Replayed: true`.
- Same key, different body: rejected with `422 idempotency_key_reused`.
- 20 identical requests fired at the same instant from 20 threads: the balance changes once, 19 get replays (`tests/test_concurrency.py`).
- 20 different payments racing for a balance that fits 15: exactly 15 succeed, 5 are rejected, the invoice is never overpaid.
- A retry that arrives after the first call already settled the invoice gets the original success, not a confusing 409.
- Every error body has `code`, `message`, `retryable` and `next_step`, so a caller knows what to do.

Why it works is written up in [DECISIONS.md](DECISIONS.md).

## Run it

```bash
pip install -r requirements.txt
python -m pytest -q          # 40 tests
uvicorn billing.api:create_app --factory --reload
```

Try it:

```bash
curl -s -X POST localhost:8000/invoices \
  -H 'Idempotency-Key: demo-1' -H 'Content-Type: application/json' \
  -d '{"customer_id":"cus_1","currency":"INR","line_items":[{"description":"Pro plan","amount_minor":10000}]}'

# send this twice, the balance drops once
curl -s -X POST localhost:8000/invoices/<id>/payments \
  -H 'Idempotency-Key: pay-1' -H 'Content-Type: application/json' \
  -d '{"amount_minor":2500}'
```

## Layout

```
billing/
  api.py        FastAPI routes and request validation
  service.py    idempotent execution, invoice logic, ledger
  proration.py  pure proration function
  db.py         SQLite schema and connections
  errors.py     ApiError with next_step
tests/          proration, invoice rules, idempotency, real-thread concurrency
```

## Limits

- SQLite allows one writer at a time. That is what makes the idempotency guarantee simple here. A real system on Postgres would need a unique constraint plus row locks or advisory locks to get the same guarantee.
- Idempotency keys never expire. Real systems expire them (24 hours is common).
- Only successful results are stored under a key. A rejected request can be corrected and resent with the same key.
- No auth, no real payment gateway, no refunds, no currency conversion, no taxes.
- Proration is by whole days. Real billing also handles time zones, anniversary billing and tax on credits.
