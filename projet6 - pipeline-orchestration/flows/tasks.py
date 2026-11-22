"""Reusable Prefect tasks: run a job, check data freshness, send alerts, write the run summary."""
import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from prefect import get_run_logger, task

from . import config


class JobFailed(RuntimeError):
    pass


@task(retries=config.RETRIES, retry_delay_seconds=config.RETRY_DELAYS_S)
def run_job(name: str, job: dict) -> dict:
    """Run one pipeline as an isolated subprocess with a hard timeout. Non-zero exit = task failure (retried)."""
    log = get_run_logger()
    t0 = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, *job["cmd"]], cwd=job["dir"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=job["timeout_s"])
    except subprocess.TimeoutExpired as err:
        raise JobFailed(f"{name}: timeout after {job['timeout_s']} s") from err
    secs = round(time.perf_counter() - t0, 1)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
    if proc.returncode != 0:
        log.error("%s failed (exit %s): %s", name, proc.returncode, " | ".join(tail))
        raise JobFailed(f"{name}: exit code {proc.returncode}: {' | '.join(tail)}")
    log.info("%s OK in %ss", name, secs)
    return {"job": name, "status": "success", "seconds": secs, "last_log": tail[-1] if tail else ""}


def newest_row(job: dict) -> str | None:
    db = Path(job["dir"]) / job["db"]
    if not db.exists():
        return None
    conn = sqlite3.connect(db)
    try:
        return conn.execute(job["freshness_sql"]).fetchone()[0]
    finally:
        conn.close()                      # `with sqlite3.connect()` only commits, it does not close


@task
def check_freshness(name: str, job: dict, now: datetime | None = None) -> dict:
    """Data SLA: the newest row must be younger than max_age_h, otherwise the dataset is 'stale'."""
    if "freshness_sql" not in job:
        return {"job": name, "fresh": None}
    now = now or datetime.now(timezone.utc)
    stale = {"job": name, "fresh": False, "age_h": None, "max_age_h": job["max_age_h"]}
    try:
        latest = newest_row(job)
        if latest is None:
            return {**stale, "error": "no data"}
        age_h = round((now - datetime.fromisoformat(latest)).total_seconds() / 3600, 1)
    except (sqlite3.Error, ValueError, TypeError) as err:      # a check we cannot run counts as 'not fresh'
        return {**stale, "error": f"cannot check: {err}"}
    return {"job": name, "fresh": age_h <= job["max_age_h"], "age_h": age_h, "max_age_h": job["max_age_h"]}


def write_alert_log(line: str) -> None:
    with open(config.REPORT_DIR / "alerts.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


@task
def send_alert(text: str) -> None:
    """Alert to Slack if a webhook is configured; always appended to reports/alerts.log."""
    config.REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_alert_log(f"{stamp} {text}")
    if config.SLACK_WEBHOOK_URL:
        req = urllib.request.Request(config.SLACK_WEBHOOK_URL, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except OSError as err:            # Slack down must not stop the run report
            write_alert_log(f"{stamp} Slack alert not sent: {err}")


@task
def write_summary(results: list[dict], freshness: list[dict]) -> Path:
    """Markdown run report: one line per job + data freshness, readable by a non-technical client."""
    config.REPORT_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    lines = [f"# Platform run {now:%Y-%m-%d %H:%M} UTC", "",
             "| Job | Status | Duration (s) | Detail |", "|---|---|---|---|"]
    for r in results:
        detail = r.get("last_log") or r.get("error", "")
        lines.append(f"| {r['job']} | {r['status']} | {r.get('seconds', '')} | {detail[:90]} |")
    lines += ["", "| Dataset | Fresh | Age (h) | SLA (h) |", "|---|---|---|---|"]
    for f in freshness:
        if f["fresh"] is not None:
            lines.append(f"| {f['job']} | {'yes' if f['fresh'] else 'NO'} | {f['age_h']} | {f['max_age_h']} |")
    text = "\n".join(lines) + "\n"
    path = config.REPORT_DIR / f"run_{now:%Y%m%d_%H%M%S}.md"
    path.write_text(text, encoding="utf-8")
    (config.REPORT_DIR / "latest_run.md").write_text(text, encoding="utf-8")
    return path
