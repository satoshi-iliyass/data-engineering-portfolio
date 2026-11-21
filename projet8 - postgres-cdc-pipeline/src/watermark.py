"""The 'classic' incremental load most scripts use: WHERE updated_at > last watermark.
Kept here on purpose to show what it misses compared with CDC (hard deletes)."""
import psycopg

from . import config

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS naive;
CREATE TABLE IF NOT EXISTS naive.orders (id int PRIMARY KEY, customer_id int, status text, amount numeric(10,2),
                                         updated_at timestamptz);
CREATE TABLE IF NOT EXISTS naive.watermark (tbl text PRIMARY KEY, value timestamptz);
"""


def sync() -> int:
    with psycopg.connect(config.dsn(config.SOURCE_DB)) as src, psycopg.connect(config.dsn(config.TARGET_DB)) as tgt:
        tgt.execute(SCHEMA)
        wm = tgt.execute("SELECT value FROM naive.watermark WHERE tbl = 'orders'").fetchone()
        rows = src.execute("SELECT id, customer_id, status, amount, updated_at FROM orders "
                           "WHERE updated_at > %s ORDER BY updated_at",
                           (wm[0] if wm else "-infinity",)).fetchall()
        with tgt.cursor() as cur:
            cur.executemany("""INSERT INTO naive.orders VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET
                               customer_id = EXCLUDED.customer_id, status = EXCLUDED.status,
                               amount = EXCLUDED.amount, updated_at = EXCLUDED.updated_at""", rows)
        if rows:
            tgt.execute("INSERT INTO naive.watermark VALUES ('orders', %s) ON CONFLICT (tbl) DO UPDATE "
                        "SET value = EXCLUDED.value", (rows[-1][4],))
    return len(rows)
