"""Load: incremental, idempotent upsert into SQLite + quarantine + run log."""
import sqlite3
from pathlib import Path

from .transform import daily_summary_sql

SCHEMA = """
CREATE TABLE IF NOT EXISTS hourly_weather (
    city TEXT NOT NULL, ts_utc TEXT NOT NULL,
    temperature_c REAL, humidity_pct REAL, precipitation_mm REAL, wind_kmh REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (city, ts_utc)
);
CREATE TABLE IF NOT EXISTS rejected_rows (
    city TEXT, ts_utc TEXT, reason TEXT, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT, inserted INTEGER, updated INTEGER, rejected INTEGER, message TEXT
);
"""

UPSERT = """
INSERT INTO hourly_weather (city, ts_utc, temperature_c, humidity_pct, precipitation_mm, wind_kmh, fetched_at)
VALUES (:city, :ts_utc, :temperature_c, :humidity_pct, :precipitation_mm, :wind_kmh, :fetched_at)
ON CONFLICT(city, ts_utc) DO UPDATE SET
    temperature_c=excluded.temperature_c, humidity_pct=excluded.humidity_pct,
    precipitation_mm=excluded.precipitation_mm, wind_kmh=excluded.wind_kmh,
    fetched_at=excluded.fetched_at
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executescript(daily_summary_sql())
    return conn


def upsert(conn: sqlite3.Connection, rows: list[dict]) -> tuple[int, int]:
    """Return (inserted, updated) so the run log shows what actually changed."""
    before = conn.execute("SELECT COUNT(*) FROM hourly_weather").fetchone()[0]
    with conn:
        conn.executemany(UPSERT, rows)
    after = conn.execute("SELECT COUNT(*) FROM hourly_weather").fetchone()[0]
    inserted = after - before
    return inserted, len(rows) - inserted


def quarantine(conn: sqlite3.Connection, bad: list[dict]) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO rejected_rows (city, ts_utc, reason, fetched_at) VALUES (:city, :ts_utc, :reason, :fetched_at)",
            bad)


def log_run(conn, status, inserted=0, updated=0, rejected=0, message=""):
    with conn:
        conn.execute("INSERT INTO runs (status, inserted, updated, rejected, message) VALUES (?,?,?,?,?)",
                     (status, inserted, updated, rejected, message))
