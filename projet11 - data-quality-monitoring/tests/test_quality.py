"""Tests on tiny synthetic databases: each defect type must be caught with the right severity."""
import sqlite3
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from src import engine, history, run

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def make_db(path, orders, customers=((1, "a@x.com"), (2, "b@x.com"))):
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE customers (customer_id INT, email VARCHAR)")
        con.execute("CREATE TABLE orders (id INT, customer_id INT, qty INT, status VARCHAR, price DOUBLE, list DOUBLE)")
        con.executemany("INSERT INTO customers VALUES (?, ?)", list(customers))
        con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", orders)


CONTRACT = """
dataset: orders
source: {{type: duckdb, path: {db}, table: orders}}
volume_anomaly: true
checks:
  - {{id: pk, type: unique, column: id, severity: critical}}
  - {{id: pk_nn, type: not_null, column: id, severity: critical}}
  - {{id: qty, type: between, column: qty, min: 1, max: 10, severity: critical}}
  - {{id: status, type: in_set, column: status, values: [ok, cancelled], severity: critical}}
  - {{id: fk, type: foreign_key, column: customer_id, ref: customers.customer_id, severity: critical}}
  - {{id: price_le_list, type: pair_greater_or_equal, column_a: list, column_b: price, severity: warning}}
  - {{id: rows, type: row_count_between, min: 2, max: 100, severity: critical}}
"""
GOOD = [(1, 1, 2, "ok", 9.0, 10.0), (2, 2, 1, "cancelled", 10.0, 10.0), (3, 1, 5, "ok", 5.0, 6.0)]


@pytest.fixture
def setup(tmp_path):
    def _make(orders):
        db = tmp_path / "w.duckdb"
        db.unlink(missing_ok=True)
        make_db(db, orders)
        cdir = tmp_path / "contracts"
        cdir.mkdir(exist_ok=True)
        (cdir / "orders.yml").write_text(CONTRACT.format(db=db.as_posix()), encoding="utf-8")
        return cdir, tmp_path / "reports"
    return _make


def failed(out):
    return {r.check_id: r for r in out["results"] if not r.passed}


def test_clean_data_passes_everything(setup):
    cdir, rep = setup(GOOD)
    out = run.run(cdir, rep, NOW)
    assert failed(out) == {} and out["summary"]["score_pct"] == 100.0
    assert (rep / "quality_report.html").exists() and (rep / "quality_results.json").exists()


@pytest.mark.parametrize("bad_row,check,severity", [
    ((1, 1, 2, "ok", 1.0, 2.0), "pk", "critical"),              # duplicate primary key
    ((4, 1, 0, "ok", 1.0, 2.0), "qty", "critical"),             # out of range
    ((4, 1, 2, "on_hold", 1.0, 2.0), "status", "critical"),     # unknown category
    ((4, 99, 2, "ok", 1.0, 2.0), "fk", "critical"),             # orphan foreign key
    ((4, 1, 2, "ok", 12.0, 10.0), "price_le_list", "warning"),  # business rule
    ((None, 1, 2, "ok", 1.0, 2.0), "pk_nn", "critical"),        # null key
])
def test_each_defect_is_caught(setup, bad_row, check, severity):
    cdir, rep = setup(GOOD + [bad_row])
    f = failed(run.run(cdir, rep, NOW))
    assert check in f and f[check].severity == severity
    assert set(f) <= {check, "pk_nn", "pk"}                     # nothing else wrongly flagged


def test_failing_examples_are_reported(setup):
    cdir, rep = setup(GOOD + [(4, 1, 2, "on_hold", 1.0, 2.0)])
    r = failed(run.run(cdir, rep, NOW))["status"]
    assert r.failed_examples == ["on_hold"] and r.detail.startswith("1 bad of 4")


def test_volume_anomaly_after_learning_period(setup):
    cdir, rep = setup(GOOD * 1 + [(i, 1, 1, "ok", 1.0, 1.0) for i in range(10, 40)])  # 33 rows
    for _ in range(3):
        assert "volume_anomaly" not in failed(run.run(cdir, rep, NOW))
    cdir, rep = setup(GOOD)                                     # sudden drop 33 -> 3 rows
    f = failed(run.run(cdir, rep, NOW))
    assert f["volume_anomaly"].severity == "warning" and "-90.9 %" in f["volume_anomaly"].detail


def test_volume_rules():
    assert history.volume_anomaly([100, 101], 10)[0] is True          # still learning
    assert history.volume_anomaly([100, 102, 98, 100], 104)[0] is True
    assert history.volume_anomaly([100, 102, 98, 100], 40)[0] is False


def test_freshness_check(tmp_path):
    db = tmp_path / "w.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (fetched_at TEXT)")
    con.execute("INSERT INTO t VALUES (?)", ((NOW - timedelta(hours=30)).isoformat(),))
    con.commit()
    con.close()
    c = {"dataset": "t", "source": {"type": "sqlite", "path": str(db), "table": "t"}, "_file": tmp_path / "x.yml",
         "checks": [{"id": "fresh", "type": "freshness", "column": "fetched_at", "max_age_hours": 26,
                     "severity": "warning"}]}
    res, n = engine.run_contract(c, engine.new_context(), NOW)
    assert n == 1 and res[0].passed is False and "30.0 h old" in res[0].detail


def test_contract_validation(tmp_path):
    (tmp_path / "bad.yml").write_text("dataset: x\nsource: {type: duckdb, path: a, table: b}\n"
                                      "checks:\n  - {id: c, type: unique, column: a, severity: urgent}\n")
    with pytest.raises(ValueError, match="severity"):
        engine.load_contracts(tmp_path)


def test_missing_source_is_critical_unless_optional(tmp_path):
    cdir = tmp_path / "c"
    cdir.mkdir()
    base = "dataset: {n}\n{opt}source: {{type: sqlite, path: nope.db, table: t}}\nchecks: []\n"
    (cdir / "a.yml").write_text(base.format(n="required", opt=""))
    (cdir / "b.yml").write_text(base.format(n="local_only", opt="optional: true\n"))
    out = run.run(cdir, tmp_path / "r", NOW)
    assert out["summary"]["critical_failed"] == 1 and out["summary"]["skipped_optional"] == ["local_only"]


def test_cli_exit_code_is_the_gate(setup, monkeypatch):
    cdir, rep = setup(GOOD + [(4, 1, 2, "ok", 12.0, 10.0)])    # warning only
    for fail_on, code in (("critical", 0), ("warning", 1)):
        monkeypatch.setattr("sys.argv", ["run", "--contracts", str(cdir), "--reports", str(rep), "--fail-on", fail_on])
        with pytest.raises(SystemExit) as e:
            run.main()
        assert e.value.code == code
