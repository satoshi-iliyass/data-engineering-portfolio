"""Behaviour tests with fake jobs (tiny Python commands) - no network, run in seconds."""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest
from prefect.testing.utilities import prefect_test_harness

from flows import config, tasks
from flows.platform import daily_platform


@pytest.fixture(scope="session", autouse=True)
def prefect_backend():
    with prefect_test_harness():
        yield


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks.run_job, "retry_delay_seconds", [0, 0])
    monkeypatch.setattr(config, "REPORT_DIR", tmp_path / "reports")


def job(tmp_path, code, **extra):
    return {"dir": tmp_path, "cmd": ["-c", code], "timeout_s": 30, **extra}


def test_all_success_writes_summary(tmp_path):
    out = daily_platform(jobs={"a": job(tmp_path, "print('done a')"), "b": job(tmp_path, "print('done b')")})
    assert [r["status"] for r in out["results"]] == ["success", "success"]
    assert out["problems"] == []
    summary = (config.REPORT_DIR / "latest_run.md").read_text(encoding="utf-8")
    assert "done a" in summary and "done b" in summary
    assert not (config.REPORT_DIR / "alerts.log").exists()


def test_transient_failure_is_retried_then_succeeds(tmp_path):
    flaky = ("import pathlib,sys; p=pathlib.Path('attempts.txt'); n=int(p.read_text()) if p.exists() else 0;"
             "p.write_text(str(n+1)); sys.exit(0 if n >= 1 else 1)")
    out = daily_platform(jobs={"flaky": job(tmp_path, flaky)})
    assert out["results"][0]["status"] == "success"
    assert (tmp_path / "attempts.txt").read_text() == "2"          # failed once, retried once


def test_permanent_failure_alerts_and_skips_downstream(tmp_path):
    jobs = {"up": job(tmp_path, "import sys; print('boom'); sys.exit(3)"),
            "down": job(tmp_path, "print('should not run')", depends_on=["up"]),
            "other": job(tmp_path, "print('independent')")}
    out = daily_platform(jobs=jobs)
    status = {r["job"]: r["status"] for r in out["results"]}
    assert status["up"] == "failed"
    assert status["down"].startswith("skipped")
    assert status["other"] == "success"                            # one failure does not stop the others
    alert = (config.REPORT_DIR / "alerts.log").read_text(encoding="utf-8")
    assert "up failed" in alert and "exit code 3" in alert


def test_timeout_is_a_failure(tmp_path):
    out = daily_platform(jobs={"slow": {**job(tmp_path, "import time; time.sleep(5)"), "timeout_s": 1}})
    assert out["results"][0]["status"] == "failed"
    assert "timeout" in out["results"][0]["error"]


def _db(tmp_path, ts):
    c = sqlite3.connect(tmp_path / "d.db")
    c.execute("CREATE TABLE t (fetched_at TEXT)")
    c.execute("INSERT INTO t VALUES (?)", (ts,))
    c.commit()
    c.close()
    return {"dir": tmp_path, "db": "d.db", "freshness_sql": "SELECT MAX(fetched_at) FROM t", "max_age_h": 24}


def test_freshness_sla(tmp_path):
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    fresh = tasks.check_freshness.fn("x", _db(tmp_path, (now - timedelta(hours=3)).isoformat()), now)
    assert fresh["fresh"] is True and fresh["age_h"] == 3.0
    (tmp_path / "d.db").unlink()
    stale = tasks.check_freshness.fn("x", _db(tmp_path, (now - timedelta(hours=30)).isoformat()), now)
    assert stale["fresh"] is False


def test_stale_data_raises_alert_even_if_job_succeeds(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    j = {**job(tmp_path, "print('ran but loaded nothing')"), **_db(tmp_path, old)}
    out = daily_platform(jobs={"silent": j})
    assert out["results"][0]["status"] == "success"
    assert any("STALE" in p for p in out["problems"])
    assert "STALE" in (config.REPORT_DIR / "alerts.log").read_text(encoding="utf-8")
