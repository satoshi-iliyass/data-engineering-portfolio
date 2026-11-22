"""Cases found by review: most of these failed on the first version. Needs a real PostgreSQL (see test_cdc.py)."""
import pytest

from tests.test_cdc import cdc, config, env, psycopg, pytestmark, reconcile, source_app, src, tgt  # noqa: F401


def one_transaction(*statements):
    with psycopg.connect(config.dsn(config.SOURCE_DB)) as c:
        for q in statements:
            c.execute(q)


def current(customer):
    return tgt("SELECT segment, valid_to FROM core.customers_history WHERE id = %s AND is_current", (customer,))


def everything_matches():
    return reconcile.cdc()["match"] and reconcile.customers()["match"]


def test_two_changes_in_one_transaction_keep_one_current_version(env):
    one_transaction("UPDATE customers SET segment = 'x1' WHERE id = 1", "UPDATE customers SET segment = 'x2' WHERE id = 1")
    cdc.sync_all()
    assert current(1) == [("x2", None)] and everything_matches()


def test_truncate_is_replicated(env):
    src("TRUNCATE orders, customers")
    cdc.sync_all()
    assert tgt("SELECT count(*) FROM core.orders_live")[0][0] == 0
    assert tgt("SELECT count(*) FROM core.customers_history WHERE is_current")[0][0] == 0
    assert everything_matches()


def test_rows_inserted_after_a_truncate_are_alive(env):
    one_transaction("TRUNCATE orders", "INSERT INTO orders (id, customer_id, amount) VALUES (1, 1, 5)")
    cdc.sync_all()
    assert tgt("SELECT id, amount FROM core.orders_live") == [(1, 5)] and everything_matches()


def test_changed_primary_key_leaves_no_ghost(env):
    src("UPDATE orders SET id = 9001 WHERE id = 1")
    cdc.sync_all()
    assert tgt("SELECT id FROM core.orders_live WHERE id IN (1, 9001)") == [(9001,)] and everything_matches()


def test_changed_customer_key_moves_the_history(env):
    one_transaction("DELETE FROM orders WHERE customer_id = 2", "UPDATE customers SET id = 900 WHERE id = 2")
    cdc.sync_all()
    assert current(2) == [] and len(current(900)) == 1 and everything_matches()


LONG = "repeat('abcd', 5000)"
BIG_TEXT = ("ALTER TABLE customers ALTER name SET STORAGE EXTERNAL", f"UPDATE customers SET name = {LONG} WHERE id = 1")


def test_unchanged_long_text_is_kept(env):
    one_transaction(*BIG_TEXT)
    cdc.sync_all()
    src("UPDATE customers SET segment = 'gold2' WHERE id = 1")
    cdc.sync_all()
    assert tgt("SELECT length(name), segment FROM core.customers WHERE id = 1") == [(20000, "gold2")]
    assert current(1)[0][0] == "gold2" and everything_matches()


def test_unchanged_long_tracked_text_is_kept_in_history(env):
    one_transaction("ALTER TABLE customers ALTER email SET STORAGE EXTERNAL", f"UPDATE customers SET email = {LONG} WHERE id = 1")
    cdc.sync_all()
    src("UPDATE customers SET segment = 'gold2' WHERE id = 1")
    cdc.sync_all()
    assert tgt("SELECT length(email), segment FROM core.customers_history WHERE id = 1 AND is_current") == [(20000, "gold2")]


def test_key_change_with_unreadable_long_text_stops_instead_of_losing_it(env):
    one_transaction(*BIG_TEXT, "DELETE FROM orders WHERE customer_id = 1")
    cdc.sync_all()
    src("UPDATE customers SET id = 901 WHERE id = 1")
    with pytest.raises(ValueError, match="long value"):
        cdc.sync_all()


@pytest.mark.parametrize("text", ["O'Brien", "new-tuple: x", "old-key: id[integer]:5 new-tuple: id[integer]:6", "a[text]:'b'",
                                  "line1\nline2", "back\\slash", "unchanged-toast-datum", "null", "", "é£漢", "COMMIT 12"])
def test_strange_text_arrives_unchanged(env, text):
    src("UPDATE customers SET name = %s WHERE id = 1", (text,))
    cdc.sync_all()
    assert tgt("SELECT name FROM core.customers WHERE id = 1") == [(text,)] and everything_matches()


def test_same_table_name_in_another_schema_is_ignored(env):
    one_transaction("CREATE SCHEMA audit", "CREATE TABLE audit.orders (id int PRIMARY KEY, note text)",
                    "INSERT INTO audit.orders VALUES (1, 'not a shop order')")
    cdc.sync_all()
    assert everything_matches()


def test_unknown_line_is_never_ignored():
    with pytest.raises(ValueError, match="cannot read"):
        cdc.parse_change("0/1", 1, 0, "table public.orders: MYSTERY: id[integer]:1")
    with pytest.raises(ValueError, match="cannot read"):
        cdc.parse_change("0/1", 1, 0, "table public.orders: INSERT: id[integer]:1 tags[text[]]:'{a}'")


def test_untracked_table_is_ignored():
    assert cdc.parse_change("0/1", 1, 0, 'table public."Odd Name": INSERT: x[text[]]:\'{a}\'') == []


def test_older_event_never_rewrites_newer_data(env):
    src("UPDATE orders SET amount = 777 WHERE id = 1")
    cdc.sync_all()
    old = cdc.parse_change("0/1", 1, 0, "table public.orders: UPDATE: id[integer]:1 customer_id[integer]:1 "
                                        "status[text]:'old' amount[numeric]:1.00")[0]
    with psycopg.connect(config.dsn(config.TARGET_DB)) as t:
        cdc._upsert(t, old)
    assert tgt("SELECT amount FROM core.orders WHERE id = 1") == [(777,)]


def test_deleted_customer_closes_its_history(env):
    one_transaction("DELETE FROM orders WHERE customer_id = 3", "DELETE FROM customers WHERE id = 3")
    cdc.sync_all()
    assert current(3) == [] and everything_matches()


def test_reconciliation_sees_a_wrong_customer(env):
    with psycopg.connect(config.dsn(config.TARGET_DB)) as t:
        t.execute("UPDATE core.customers SET name = 'wrong' WHERE id = 1")
    assert reconcile.customers()["match"] is False


def test_reconciliation_sees_missing_ghost_and_different_orders(env):
    with psycopg.connect(config.dsn(config.TARGET_DB)) as t:
        t.execute("DELETE FROM core.orders WHERE id = 1")
        t.execute("UPDATE core.orders SET amount = amount + 1 WHERE id = 2")
        t.execute("INSERT INTO core.orders (id, amount, _lsn) VALUES (9999, 1, '0/1')")
    r = reconcile.cdc()
    assert (r["missing"], r["different"], r["ghost_rows"], r["match"]) == (1, 1, 1, False)


def test_simulator_really_changes_data(env):
    import random
    stats = source_app.simulate_round(random.Random(3), new_orders=5)
    assert stats["inserted"] == 5 and stats["updated"] > 0 and stats["customers_changed"] == 10
    assert cdc.pending_changes() > 0
    cdc.sync_all()
    assert everything_matches()


def test_demo_runs(tmp_path, monkeypatch, env):
    from src import run_demo
    monkeypatch.setattr(run_demo, "ROOT", tmp_path)
    res = run_demo.main(rounds=3)
    assert res["cdc"]["match"] and res["customers"]["match"] and not res["watermark"]["match"]
    assert res["crash_replay"]["applied"] == 0 and res["crash_replay"]["read"] > 0
    assert (tmp_path / "reports" / "cdc_report.md").exists()


def test_password_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("PG_PASSWORD", "from-env")
    assert config.password() == "from-env" and "password=from-env" in config.dsn("x")
