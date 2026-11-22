# Pipeline Orchestration — run, retry, monitor and alert with Prefect

Turns three separate scripts (web scraper, REST-API loader, Excel consolidation — projects 1-3) into **one scheduled data platform** that runs every morning without anyone touching it, retries what fails, checks that the data is fresh, and alerts when something is wrong.

Typical client request: *"I have Python scripts that I run by hand / with Windows Task Scheduler. Sometimes they fail silently and nobody notices for a week."*

```
                     ┌─► run-weather_api ───────────────────┐
 06:00 cron ──► flow ┤                                       ├─► freshness checks ─► alert (Slack + log) ─► run report (.md)
 (Prefect)           └─► run-price_tracker ─► run-excel_consolidation ┘   (data SLA)        only if problems       every run
                          parallel             waits for upstream
                     each job: subprocess + timeout + 2 retries (30 s, 120 s back-off)
```

## What it handles

| Situation | Behaviour |
|---|---|
| Network blip / API 5xx | Task retried 2× with back-off; succeeds silently if the retry works |
| Job keeps failing | Marked *failed*, alert sent with the job's last log lines and exit code |
| Upstream job failed | Dependent job is **skipped**, not run on incomplete data |
| One job fails | Independent jobs still run (no "all or nothing") |
| Job hangs | Hard timeout per job → failure + alert |
| Job "succeeds" but loads nothing | **Freshness SLA** on the database (newest row age vs max age) → `STALE` alert |
| Need history | Every run writes `reports/run_<timestamp>.md`; Prefect UI keeps all runs, durations and logs |

Jobs, timeouts, dependencies and SLAs live in one config file (`flows/config.py`) — adding a client script is ~6 lines. Secrets (Slack webhook) come from environment variables, never from code.

## Run

```bash
pip install -r requirements.txt
python -m flows.platform                     # run once now
python -m flows.platform --only weather_api  # run a subset
python -m flows.platform --serve             # scheduler: every day at 06:00
prefect server start                         # optional UI at http://127.0.0.1:4200
python -m pytest -q
```

Optional Slack alerts: `set SLACK_WEBHOOK_URL=https://hooks.slack.com/...`

## Verified result

Real run on the three pipelines (laptop, 2026-11-21):

| Job | Status | Duration | Detail |
|---|---|---|---|
| weather_api | success | 3.5 s | inserted=0 updated=600 rejected=0 (idempotent re-run) |
| price_tracker | success | 42.8 s | 1,000 rows loaded + Excel report |
| excel_consolidation | success | 3.6 s | 9 files → 549 clean rows, 45 set aside with reason |

**13 tests** (fake jobs, no network, ~40 s), including a freshness check that cannot run and Slack being down: all-success report, transient failure retried then OK, permanent failure → alert + downstream skipped + other jobs still run, timeout, freshness SLA, "silent success" caught as stale.

**CI**: GitHub Actions (`.github/workflows/ci.yml` at the repo root) runs the tests of every project and `dbt build` on each push.

## Stack

Python · Prefect 3 (flows, tasks, retries, concurrency, cron schedule) · SQLite · GitHub Actions · pytest
