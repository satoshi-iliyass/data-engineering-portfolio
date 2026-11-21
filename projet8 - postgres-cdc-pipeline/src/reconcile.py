"""Row-by-row reconciliation: does the analytics copy equal the source? (what auditors / finance ask)."""
import psycopg

from . import config


def _rows(con, sql):
    return {r[0]: tuple(r[1:]) for r in con.execute(sql).fetchall()}


def compare(target_sql: str) -> dict:
    with psycopg.connect(config.dsn(config.SOURCE_DB)) as src, psycopg.connect(config.dsn(config.TARGET_DB)) as tgt:
        s = _rows(src, "SELECT id, customer_id, status, amount FROM orders")
        t = _rows(tgt, target_sql)
    missing = s.keys() - t.keys()
    ghosts = t.keys() - s.keys()                                  # deleted in source, still in target
    different = [k for k in s.keys() & t.keys() if s[k] != t[k]]
    return {"source_rows": len(s), "target_rows": len(t), "missing": len(missing), "ghost_rows": len(ghosts),
            "different": len(different), "source_revenue": float(sum(v[2] for v in s.values())),
            "target_revenue": float(sum(v[2] for v in t.values())),
            "match": not (missing or ghosts or different)}


def cdc() -> dict:
    return compare("SELECT id, customer_id, status, amount FROM core.orders_live")


def watermark() -> dict:
    return compare("SELECT id, customer_id, status, amount FROM naive.orders")
