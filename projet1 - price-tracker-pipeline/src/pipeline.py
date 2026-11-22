"""Assembly: extract -> transform -> quality gate -> load -> report.

Each step imports its own part only when it runs, so a broken report cannot stop the data from being saved.

    python -m src.pipeline                 # full run (scrapes the site)
    python -m src.pipeline --from-raw DATE # rebuild a day from saved raw HTML, no network
    python -m src.pipeline --report DATE   # rebuild only the Excel report
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from . import config, load

log = logging.getLogger("pipeline")


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(config.LOG_DIR / "pipeline.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )


def get_files(run_date: date, from_raw: bool) -> list[Path]:
    """Step 1: raw pages, from disk or from the site."""
    if not from_raw:
        from . import extract
        return extract.extract_all(run_date)
    files = sorted((config.RAW_DIR / run_date.isoformat()).glob("*.html"))
    if not files:
        raise FileNotFoundError(f"no raw files for {run_date}")
    return files


def read_rows(files: list[Path], run_date: date) -> list[dict]:
    """Step 2: raw pages -> clean rows."""
    from . import transform
    rows = []
    for f in files:
        rows.extend(transform.parse_page(f.read_text(encoding="utf-8"), run_date))
    return transform.dedupe(rows)


def save_rows(conn, rows: list[dict]) -> int:
    """Step 3: quality gate, then load. Bad data is never stored."""
    from . import quality
    quality.assert_ok(rows)
    return load.upsert(conn, rows)


def make_report(conn, run_date: date) -> Path:
    """Step 4: Excel file."""
    from . import report
    return report.build(conn, run_date.isoformat(), config.REPORT_DIR)


def finish(conn, run_date: date, n: int) -> int:
    """The data is already safe here: a report problem is announced, but nothing is lost."""
    day = run_date.isoformat()
    try:
        path = make_report(conn, run_date)
    except Exception as err:
        load.log_run(conn, day, "loaded_without_report", n, str(err))
        log.exception("%d rows stored but REPORT FAILED: %s", n, err)
        return 2
    load.log_run(conn, day, "success", n)
    log.info("SUCCESS: %d rows loaded, report %s", n, path)
    return 0


def run_steps(conn, run_date: date, from_raw: bool) -> int:
    """0 = all good, 1 = nothing stored, 2 = stored but no report."""
    try:
        rows = read_rows(get_files(run_date, from_raw), run_date)
        n = save_rows(conn, rows)
    except Exception as err:
        load.log_run(conn, run_date.isoformat(), "failed", 0, str(err))
        log.exception("FAILED, nothing stored: %s", err)
        return 1
    return finish(conn, run_date, n)


def run(run_date: date, from_raw: bool = False) -> int:
    conn = load.connect(config.DB_PATH)
    try:
        return run_steps(conn, run_date, from_raw)
    finally:
        conn.close()


def report_only(run_date: date) -> int:
    conn = load.connect(config.DB_PATH)
    try:
        n = load.count_day(conn, run_date.isoformat())
        if n == 0:
            log.error("no data for %s: no report", run_date)
            return 1
        return finish(conn, run_date, n)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-raw", metavar="YYYY-MM-DD", help="replay a day from raw HTML")
    parser.add_argument("--report", metavar="YYYY-MM-DD", help="rebuild only the Excel report of a day")
    args = parser.parse_args()
    setup_logging()
    if args.report:
        sys.exit(report_only(date.fromisoformat(args.report)))
    day = date.fromisoformat(args.from_raw) if args.from_raw else date.today()
    sys.exit(run(day, from_raw=bool(args.from_raw)))


if __name__ == "__main__":
    main()
