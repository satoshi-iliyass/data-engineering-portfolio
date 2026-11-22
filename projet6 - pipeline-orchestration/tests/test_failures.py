"""The watchman itself must not fall: a broken check or a broken Slack is reported, the run report is still written."""
import json
import sqlite3
import urllib.error

import pytest
from prefect.testing.utilities import prefect_test_harness

from flows import config, tasks
from flows.platform import daily_platform


@pytest.fixture(scope="module", autouse=True)
def prefect_backend():
    with prefect_test_harness():
        yield


@pytest.fixture(autouse=True)
def reports(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks.run_job, "retry_delay_seconds", [0, 0])
    monkeypatch.setattr(config, "REPORT_DIR", tmp_path / "reports")
    return tmp_path / "reports"


def job(tmp_path, **extra):
    return {"dir": tmp_path, "cmd": ["-c", "print('ok')"], "timeout_s": 30, **extra}


def database(tmp_path, value):
    con = sqlite3.connect(tmp_path / "d.db")
    con.execute("CREATE TABLE t (at TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (value,))
    con.commit()
    con.close()
    return {"db": "d.db", "freshness_sql": "SELECT MAX(at) FROM t", "max_age_h": 24}


@pytest.mark.parametrize("sql,value", [("SELECT MAX(x) FROM missing_table", "2026-01-01T00:00:00+00:00"),
                                       ("SELECT MAX(at) FROM t", "not a date"),
                                       ("SELECT MAX(at) FROM t", "2026-01-01T00:00:00")])       # no timezone
def test_unreadable_freshness_is_an_alert_not_a_crash(tmp_path, reports, sql, value):
    out = daily_platform(jobs={"a": job(tmp_path, **{**database(tmp_path, value), "freshness_sql": sql})})
    assert out["results"][0]["status"] == "success"
    assert len(out["problems"]) == 1 and "a STALE: cannot check" in out["problems"][0]
    assert "cannot check" in (reports / "alerts.log").read_text(encoding="utf-8")
    assert (reports / "latest_run.md").exists()


def test_slack_down_does_not_stop_the_report(tmp_path, reports, monkeypatch):
    def down(*args, **kwargs):
        raise urllib.error.URLError("slack down")
    monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.example.invalid/x")
    monkeypatch.setattr(tasks.urllib.request, "urlopen", down)
    out = daily_platform(jobs={"a": {**job(tmp_path), "cmd": ["-c", "raise SystemExit(3)"]}})
    assert out["results"][0]["status"] == "failed"
    log = (reports / "alerts.log").read_text(encoding="utf-8")
    assert "a failed" in log and "Slack alert not sent" in log
    assert (reports / "latest_run.md").exists()


def test_slack_receives_the_message(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.example.invalid/x")
    monkeypatch.setattr(tasks.urllib.request, "urlopen", lambda req, timeout: sent.append(json.loads(req.data)))
    tasks.send_alert.fn("hello")
    assert sent == [{"text": "hello"}]


def test_only_runs_the_chosen_jobs(tmp_path):
    out = daily_platform(only=["b"], jobs={"a": job(tmp_path), "b": job(tmp_path)})
    assert [r["job"] for r in out["results"]] == ["b"]


def test_unknown_job_name_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="unknown job"):
        daily_platform(only=["typo"], jobs={"a": job(tmp_path)})
