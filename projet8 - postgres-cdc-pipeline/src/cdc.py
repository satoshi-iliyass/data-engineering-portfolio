"""Log-based Change Data Capture from PostgreSQL's write-ahead log (WAL), the technique behind Debezium.

Every INSERT / UPDATE / DELETE committed in the source is read from a logical replication slot
(built-in `test_decoding` plugin), stored as an append-only event log, and applied to the analytics copy:
  raw.cdc_events          every change, never modified (audit / replay)
  core.<table>            current state, deletes kept as _deleted = true (soft delete)
  core.customers_history  SCD type 2: one row per version of a customer (segment / e-mail / country)
  ops.cdc_offsets         last applied WAL position

Exactly-once effect: the slot is advanced only AFTER the target transaction commits; if the process dies in
between, the same changes are read again and skipped thanks to the stored offset and the per-row _lsn guard.
"""
import json
import re
from dataclasses import dataclass, field

import psycopg

from . import config

TARGET_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS raw.cdc_events (
    lsn pg_lsn, seq int, xid bigint, commit_ts timestamptz, tbl text, op text, row jsonb,
    received_at timestamptz DEFAULT now(), PRIMARY KEY (lsn, seq));
CREATE TABLE IF NOT EXISTS core.customers (
    id int PRIMARY KEY, name text, email text, country text, segment text, updated_at timestamptz,
    _deleted boolean NOT NULL DEFAULT false, _lsn pg_lsn NOT NULL, _synced_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS core.orders (
    id int PRIMARY KEY, customer_id int, status text, amount numeric(10,2), updated_at timestamptz,
    _deleted boolean NOT NULL DEFAULT false, _lsn pg_lsn NOT NULL, _synced_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS core.customers_history (
    id int, email text, country text, segment text, valid_from timestamptz, valid_to timestamptz,
    is_current boolean, _lsn pg_lsn, PRIMARY KEY (id, valid_from));
CREATE TABLE IF NOT EXISTS ops.cdc_offsets (slot text PRIMARY KEY, applied_lsn pg_lsn, updated_at timestamptz);
CREATE OR REPLACE VIEW core.orders_live AS SELECT * FROM core.orders WHERE NOT _deleted;
"""
SCD2_COLS = ("email", "country", "segment")

HEADER = re.compile(r"^table (\w+)\.(\w+): (INSERT|UPDATE|DELETE): (.*)$", re.S)
COLUMN = re.compile(r"(\w+)\[([^\]]+)\]:('(?:[^']|'')*'|\S+)")
COMMIT = re.compile(r"^COMMIT (\d+)(?: \(at (.+)\))?$")


@dataclass
class Change:
    lsn: str
    seq: int
    xid: int
    table: str
    op: str
    cols: dict = field(default_factory=dict)    # name -> (pg_type, text value or None)
    commit_ts: str | None = None


def parse_value(raw: str):
    if raw == "null":
        return None
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    return raw


def parse_change(lsn: str, xid: int, seq: int, data: str) -> Change | None:
    m = HEADER.match(data)
    if not m:
        return None
    _, table, op, body = m.groups()
    if "new-tuple:" in body:                    # UPDATE that changed the key: keep the new row
        body = body.split("new-tuple:", 1)[1]
    cols = {name: (typ, parse_value(val)) for name, typ, val in COLUMN.findall(body)}
    return Change(lsn, seq, xid, table, op, cols)


def read_changes(src, limit: int) -> tuple[list[Change], str | None]:
    """Peek (not consume) whole transactions from the slot. Returns changes + LSN of the last COMMIT."""
    rows = src.execute("SELECT lsn::text, xid::text, data FROM pg_logical_slot_peek_changes(%s, NULL, %s, "
                       "'include-timestamp', 'on')", (config.SLOT, limit)).fetchall()
    changes, pending, last_commit, seq = [], [], None, 0
    for lsn, xid, data in rows:
        if data.startswith("BEGIN"):
            pending, seq = [], 0
        elif (m := COMMIT.match(data)):
            for c in pending:
                c.commit_ts = m.group(2)
            changes += pending
            last_commit = lsn
        elif (c := parse_change(lsn, int(xid), seq, data)) and c.table in config.TABLES:
            pending.append(c)
            seq += 1
    return changes, last_commit


# ---------- apply to the target ----------

def _upsert(tgt, c: Change) -> None:
    names = list(c.cols)
    vals = [c.cols[n][1] for n in names]
    casts = ", ".join(f"%s::{c.cols[n][0]}" for n in names)
    sets = ", ".join(f"{n} = EXCLUDED.{n}" for n in names)
    pk = config.TABLES[c.table]
    tgt.execute(f"""INSERT INTO core.{c.table} ({', '.join(names)}, _deleted, _lsn, _synced_at)
                    VALUES ({casts}, false, %s::pg_lsn, now())
                    ON CONFLICT ({pk}) DO UPDATE SET {sets}, _deleted = false, _lsn = EXCLUDED._lsn, _synced_at = now()
                    WHERE core.{c.table}._lsn < EXCLUDED._lsn""", [*vals, c.lsn])


def _delete(tgt, c: Change) -> None:
    pk = config.TABLES[c.table]
    typ, val = c.cols[pk]
    tgt.execute(f"""UPDATE core.{c.table} SET _deleted = true, _lsn = %s::pg_lsn, _synced_at = now()
                    WHERE {pk} = %s::{typ} AND _lsn < %s::pg_lsn""", (c.lsn, val, c.lsn))


def _scd2(tgt, c: Change) -> None:
    """Customers: new version only when a tracked attribute really changed."""
    cid = int(c.cols["id"][1])
    cur = tgt.execute("SELECT email, country, segment, _lsn::text FROM core.customers_history "
                      "WHERE id = %s AND is_current", (cid,)).fetchone()
    if c.op == "DELETE":
        if cur:
            tgt.execute("UPDATE core.customers_history SET valid_to = %s, is_current = false "
                        "WHERE id = %s AND is_current", (c.commit_ts, cid))
        return
    new = tuple(c.cols[k][1] for k in SCD2_COLS)
    if cur and tuple(x.strip() if isinstance(x, str) else x for x in cur[:3]) == tuple(
            x.strip() if isinstance(x, str) else x for x in new):
        return
    if cur:
        tgt.execute("UPDATE core.customers_history SET valid_to = %s, is_current = false "
                    "WHERE id = %s AND is_current", (c.commit_ts, cid))
    tgt.execute("INSERT INTO core.customers_history VALUES (%s, %s, %s, %s, %s, NULL, true, %s::pg_lsn) "
                "ON CONFLICT DO NOTHING", (cid, *new, c.commit_ts, c.lsn))


def apply(tgt, changes: list[Change], last_commit: str) -> int:
    applied_lsn = tgt.execute("SELECT applied_lsn::text FROM ops.cdc_offsets WHERE slot = %s FOR UPDATE",
                              (config.SLOT,)).fetchone()[0]
    with tgt.cursor() as cur:
        cur.executemany("INSERT INTO raw.cdc_events (lsn, seq, xid, commit_ts, tbl, op, row) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        [(c.lsn, c.seq, c.xid, c.commit_ts, c.table, c.op,
                          json.dumps({k: v[1] for k, v in c.cols.items()})) for c in changes])
    n = 0
    for c in changes:
        if tgt.execute("SELECT %s::pg_lsn <= %s::pg_lsn", (c.lsn, applied_lsn)).fetchone()[0]:
            continue                                    # already applied before a crash: skip
        _delete(tgt, c) if c.op == "DELETE" else _upsert(tgt, c)
        if c.table == "customers":
            _scd2(tgt, c)
        n += 1
    tgt.execute("UPDATE ops.cdc_offsets SET applied_lsn = %s, updated_at = now() WHERE slot = %s",
                (last_commit, config.SLOT))
    return n


# ---------- lifecycle ----------

def setup_target() -> None:
    with psycopg.connect(config.dsn(config.TARGET_DB)) as tgt:
        tgt.execute(TARGET_SCHEMA)


def initial_snapshot() -> dict:
    """Create the slot FIRST, then copy the tables: nothing committed after this point can be missed."""
    with psycopg.connect(config.dsn(config.SOURCE_DB), autocommit=True) as src, \
            psycopg.connect(config.dsn(config.TARGET_DB)) as tgt:
        slot_lsn = src.execute("SELECT lsn::text FROM pg_create_logical_replication_slot(%s, 'test_decoding')",
                               (config.SLOT,)).fetchone()[0]
        counts = {}
        for table in config.TABLES:
            rows = src.execute(f"SELECT * FROM {table}")
            cols = [d.name for d in rows.description]
            data = rows.fetchall()
            with tgt.cursor() as cur:
                cur.executemany(f"INSERT INTO core.{table} ({', '.join(cols)}, _lsn) VALUES "
                                f"({', '.join(['%s'] * len(cols))}, %s::pg_lsn)", [(*r, slot_lsn) for r in data])
            counts[table] = len(data)
        tgt.execute("INSERT INTO core.customers_history SELECT id, email, country, segment, updated_at, NULL, true, "
                    "_lsn FROM core.customers")
        tgt.execute("INSERT INTO ops.cdc_offsets VALUES (%s, %s, now())", (config.SLOT, slot_lsn))
    return {"slot_lsn": slot_lsn, **counts}


def sync_once(limit: int = config.BATCH_CHANGES, crash_before_ack: bool = False) -> dict | None:
    """Read a batch, apply it in ONE target transaction, then acknowledge it to the source slot.
    Returns None when there is nothing left to read."""
    with psycopg.connect(config.dsn(config.SOURCE_DB), autocommit=True) as src:
        changes, last_commit = read_changes(src, limit)
        if last_commit is None:
            return None
        with psycopg.connect(config.dsn(config.TARGET_DB)) as tgt:
            n = apply(tgt, changes, last_commit)
        if crash_before_ack:
            raise RuntimeError("simulated crash after target commit, before slot acknowledgement")
        src.execute("SELECT pg_replication_slot_advance(%s, %s::pg_lsn)", (config.SLOT, last_commit))
    return {"read": len(changes), "applied": n, "acked_lsn": last_commit}


def sync_all() -> dict:
    total = {"batches": 0, "read": 0, "applied": 0}
    while (r := sync_once()) is not None:
        total["batches"] += 1
        total["read"] += r["read"]
        total["applied"] += r["applied"]
    return total


def pending_changes() -> int:
    """Replication lag expressed as the number of changes not yet applied (monitoring metric)."""
    with psycopg.connect(config.dsn(config.SOURCE_DB)) as src:
        return src.execute("SELECT count(*) FROM pg_logical_slot_peek_changes(%s, NULL, NULL) "
                           "WHERE data LIKE 'table %%'", (config.SLOT,)).fetchone()[0]
