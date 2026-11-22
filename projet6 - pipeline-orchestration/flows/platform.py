"""Daily data platform flow.

    python -m flows.platform                     # run once now
    python -m flows.platform --only weather_api  # run a subset
    python -m flows.platform --serve             # long-running scheduler (cron in config.DAILY_CRON)
"""
import argparse

from prefect import flow, get_run_logger
from prefect.futures import wait

from . import config
from .tasks import check_freshness, run_job, send_alert, write_summary


@flow(name="daily-data-platform")
def daily_platform(only: list[str] | None = None, jobs: dict | None = None) -> dict:
    log = get_run_logger()
    jobs = jobs or config.JOBS
    unknown = set(only or []) - set(jobs)
    if unknown:
        raise ValueError(f"unknown job(s): {sorted(unknown)}; known: {sorted(jobs)}")
    selected = {k: v for k, v in jobs.items() if not only or k in only}

    # 1. independent jobs run in parallel; a dependent job waits for its upstream job
    futures = {}
    for name, job in selected.items():
        upstream = [futures[d] for d in job.get("depends_on", []) if d in futures]
        futures[name] = run_job.with_options(task_run_name=f"run-{name}").submit(name, job, wait_for=upstream)
    wait(list(futures.values()))

    results = []
    for name, fut in futures.items():
        state = fut.state
        if state.is_completed():
            results.append(fut.result())
        else:
            status = "skipped (upstream failed)" if state.name == "NotReady" else "failed"
            results.append({"job": name, "status": status, "error": str(state.message or state.name)})

    # 2. data SLA checks run even if a job failed: they say how old the client's data is
    freshness = [check_freshness.with_options(task_run_name=f"freshness-{n}")(n, j) for n, j in selected.items()]

    # 3. one alert listing every problem, then the run report
    problems = [f"{r['job']} {r['status']}: {r.get('error', '')[:200]}" for r in results if r["status"] != "success"]
    problems += [f"{f['job']} STALE: " + (f.get("error") or f"{f['age_h']} h old (SLA {f['max_age_h']} h)")
                 for f in freshness if f["fresh"] is False]
    if problems:
        send_alert("daily-data-platform problems:\n" + "\n".join(problems))
    path = write_summary(results, freshness)
    log.info("summary -> %s | %d problem(s)", path, len(problems))
    return {"results": results, "freshness": freshness, "problems": problems}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="long-running scheduler")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    if a.serve:
        daily_platform.serve(name="daily-6am", cron=config.DAILY_CRON)
    else:
        daily_platform(only=a.only)
