import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id           TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL,
    currency     TEXT NOT NULL,
    total_minor  INTEGER NOT NULL CHECK (total_minor > 0),
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS line_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id    TEXT NOT NULL REFERENCES invoices(id),
    description   TEXT NOT NULL,
    amount_minor  INTEGER NOT NULL CHECK (amount_minor > 0)
);

-- Every movement of money is an append-only row. Balances are derived, never stored.
CREATE TABLE IF NOT EXISTS ledger (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id       TEXT NOT NULL REFERENCES invoices(id),
    kind             TEXT NOT NULL CHECK (kind IN ('payment', 'credit')),
    amount_minor     INTEGER NOT NULL CHECK (amount_minor > 0),
    note             TEXT,
    idempotency_key  TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    -- Backstop only. The idempotency table is the primary guard.
    UNIQUE (invoice_id, kind, idempotency_key)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    scope          TEXT NOT NULL,
    key            TEXT NOT NULL,
    request_hash   TEXT NOT NULL,
    status_code    INTEGER NOT NULL,
    response_json  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
"""


def connect(path: str, busy_timeout_s: float = 5.0) -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN/COMMIT ourselves so the idempotency
    # check and the money movement share exactly one transaction.
    conn = sqlite3.connect(path, timeout=busy_timeout_s, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()
