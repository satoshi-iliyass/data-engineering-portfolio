"""Load: idempotent upsert into SQLite. Re-running a day never creates duplicates."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    rating     INTEGER
);
CREATE TABLE IF NOT EXISTS prices (
    product_id TEXT NOT NULL REFERENCES products(product_id),
    scraped_on TEXT NOT NULL,
    price_gbp  REAL NOT NULL,
    in_stock   INTEGER NOT NULL,
    PRIMARY KEY (product_id, scraped_on)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date   TEXT NOT NULL,
    status     TEXT NOT NULL,
    rows       INTEGER,
    message    TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, rows: list[dict]) -> int:
    with conn:  # one transaction: all rows or nothing
        conn.executemany(
            """INSERT INTO products (product_id, title, rating) VALUES (:product_id, :title, :rating)
               ON CONFLICT(product_id) DO UPDATE SET title=excluded.title, rating=excluded.rating""",
            rows,
        )
        conn.executemany(
            """INSERT INTO prices (product_id, scraped_on, price_gbp, in_stock)
               VALUES (:product_id, :scraped_on, :price_gbp, :in_stock)
               ON CONFLICT(product_id, scraped_on) DO UPDATE
               SET price_gbp=excluded.price_gbp, in_stock=excluded.in_stock""",
            rows,
        )
    return len(rows)


def log_run(conn: sqlite3.Connection, run_date: str, status: str, rows: int, message: str = "") -> None:
    with conn:
        conn.execute("INSERT INTO runs (run_date, status, rows, message) VALUES (?, ?, ?, ?)",
                     (run_date, status, rows, message))


def count_day(conn: sqlite3.Connection, day: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM prices WHERE scraped_on = ?", (day,)).fetchone()[0]
