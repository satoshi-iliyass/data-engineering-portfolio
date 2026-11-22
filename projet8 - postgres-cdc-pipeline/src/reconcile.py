"""Row-by-row reconciliation: does the analytics copy equal the source? (what auditors / finance ask)."""
import psycopg

from . import config


def _rows(con, sql):
    return {r[0]: tuple(r[1:]) for r in con.execute(sql).fetchall()}


def compare(target_sql: str, source_sql: str = "SELECT id, customer_id, status, amount FROM orders") -> dict:
    with psycopg.connect(config.dsn(config.SOURCE_DB)) as src, psycopg.connect(config.dsn(config.TARGET_DB)) as tgt:
        s = _rows(src, source_sql)
        t = _rows(tgt, target_sql)
    missing = s.keys() - t.keys()
    ghosts = t.keys() - s.keys()                                  # deleted in source, still in target
    different = [k for k in s.keys() & t.keys() if s[k] != t[k]]
    return {"source_rows": len(s), "target_rows": len(t), "missing": len(missing), "ghost_rows": len(ghosts),
            "different": len(different), "match": not (missing or ghosts or different)}


def revenue(db: str, table: str) -> float:
    with psycopg.connect(config.dsn(db)) as con:
        return float(con.execute(f"SELECT coalesce(sum(amount), 0) FROM {table}").fetchone()[0])


def orders(target_table: str) -> dict:
    return {**compare(f"SELECT id, customer_id, status, amount FROM {target_table}"),
            "source_revenue": revenue(config.SOURCE_DB, "orders"),
            "target_revenue": revenue(config.TARGET_DB, target_table)}


def cdc() -> dict:
    return orders("core.orders_live")


def customers() -> dict:
    """Every column, long texts included."""
    cols = "id, name, email, country::text, segment, updated_at"
    return compare(f"SELECT {cols} FROM core.customers WHERE NOT _deleted", f"SELECT {cols} FROM customers")


def watermark() -> dict:
    return orders("naive.orders")
