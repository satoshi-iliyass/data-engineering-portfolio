"""Integration tests on a real PostgreSQL (wal_level=logical). Skipped if no server is reachable.
Uses dedicated test databases so the demo data is never touched."""
import os
import random

import pytest

os.environ.setdefault("SOURCE_DB", "test_shop_oltp")
os.environ.setdefault("TARGET_DB", "test_analytics")
os.environ.setdefault("CDC_SLOT", "test_cdc")

import psycopg  # noqa: E402

from src import cdc, config, reconcile, source_app, watermark  # noqa: E402


def _server_ok():
    try:
        with psycopg.connect(config.dsn("postgres"), connect_timeout=3) as c:
            return c.execute("SHOW wal_level").fetchone()[0] == "logical"
    except Exception:
        return False


if os.getenv("REQUIRE_PG") and not _server_ok():          # in CI a missing server is a failure, not a skip
    raise RuntimeError("PostgreSQL with wal_level=logical is required (REQUIRE_PG set)")
pytestmark = pytest.mark.skipif(not _server_ok(), reason="needs PostgreSQL with wal_level=logical")


@pytest.fixture
def env():
    admin = config.dsn("postgres")
    source_app.create_database(admin, config.SOURCE_DB)
    source_app.create_database(admin, config.TARGET_DB)
    source_app.seed(random.Random(1), n_customers=20, n_orders=50)
    cdc.setup_target()
    cdc.initial_snapshot()
    yield
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name = %s",
                  (config.SLOT,))


def src(sql, params=()):
    with psycopg.connect(config.dsn(config.SOURCE_DB)) as c:
        return c.execute(sql, params).fetchall() if sql.lstrip().upper().startswith(("SELECT", "WITH")) \
            else c.execute(sql, params)


def tgt(sql, params=()):
    with psycopg.connect(config.dsn(config.TARGET_DB)) as c:
        return c.execute(sql, params).fetchall()


def test_parser_handles_quotes_nulls_and_types():
    c = cdc.parse_change("0/1", 7, 0, "table public.customers: UPDATE: id[integer]:3 name[text]:'O''Brien' "
                                      "email[text]:null segment[text]:'gold' updated_at[timestamp with time zone]:"
                                      "'2026-11-22 00:00:00+01'")
    assert c.table == "customers" and c.op == "UPDATE"
    assert c.cols["name"] == ("text", "O'Brien") and c.cols["email"] == ("text", None)
    assert c.cols["updated_at"][0] == "timestamp with time zone"


def test_snapshot_then_insert_update_delete_are_replicated(env):
    src("INSERT INTO orders (customer_id, amount) VALUES (1, 99.90)")
    src("UPDATE orders SET status = 'paid', amount = 10 WHERE id = 1")
    src("DELETE FROM orders WHERE id = 2")
    r = cdc.sync_all()
    assert r["read"] == 3 and r["applied"] == 3
    assert tgt("SELECT status, amount FROM core.orders WHERE id = 1")[0] == ("paid", 10)
    assert tgt("SELECT _deleted FROM core.orders WHERE id = 2")[0][0] is True
    assert reconcile.cdc()["match"]
    assert cdc.pending_changes() == 0


def test_crash_between_commit_and_ack_does_not_duplicate(env):
    src("UPDATE orders SET amount = amount + 1 WHERE id <= 5")
    with pytest.raises(RuntimeError):
        cdc.sync_once(crash_before_ack=True)
    before = tgt("SELECT sum(amount) FROM core.orders")[0][0]
    again = cdc.sync_all()
    assert again["read"] == 5 and again["applied"] == 0              # re-read, but skipped
    assert tgt("SELECT sum(amount) FROM core.orders")[0][0] == before
    assert tgt("SELECT count(*) FROM raw.cdc_events")[0][0] == 5     # event log not duplicated either
    assert reconcile.cdc()["match"]


def test_rolled_back_transactions_are_never_replicated(env):
    with psycopg.connect(config.dsn(config.SOURCE_DB)) as c:
        c.execute("UPDATE orders SET amount = 0")
        c.rollback()
    assert cdc.sync_all()["read"] == 0
    assert reconcile.cdc()["match"]


def test_scd2_creates_a_version_only_on_real_change(env):
    src("UPDATE customers SET segment = 'gold', email = 'new@x.com' WHERE id = 1")
    src("UPDATE customers SET name = 'Renamed' WHERE id = 1")       # name is not tracked -> no new version
    cdc.sync_all()
    hist = tgt("SELECT email, segment, is_current, valid_to IS NULL FROM core.customers_history "
               "WHERE id = 1 ORDER BY valid_from")
    assert len(hist) == 2
    assert hist[0][2] is False and hist[1] == ("new@x.com", "gold", True, True)
    assert tgt("SELECT name FROM core.customers WHERE id = 1")[0][0] == "Renamed"


def test_watermark_misses_hard_deletes_but_cdc_does_not(env):
    watermark.sync()
    src("DELETE FROM orders WHERE id IN (3, 4)")
    cdc.sync_all()
    watermark.sync()
    assert reconcile.cdc()["match"] is True
    w = reconcile.watermark()
    assert w["match"] is False and w["ghost_rows"] == 2
