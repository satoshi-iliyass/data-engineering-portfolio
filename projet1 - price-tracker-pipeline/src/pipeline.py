"""Orchestrator: extract -> transform -> quality gate -> load -> report.

Usage:
    python -m src.pipeline                 # full run (scrapes the site)
    python -m src.pipeline --from-raw DATE # rebuild a day from saved raw HTML, no network
"""
import argparse
import logging
import sys
from datetime import date

from . import config, extract, load, quality, report, transform

log = logging.getLogger("pipeline")


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(config.LOG_DIR / "pipeline.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )


def run(run_date: date, from_raw: bool = False) -> int:
    conn = load.connect(config.DB_PATH)
    try:
        if from_raw:
            files = sorted((config.RAW_DIR / run_date.isoformat()).glob("*.html"))
            if not files:
                raise FileNotFoundError(f"no raw files for {run_date}")
        else:
            files = extract.extract_all(run_date)
        rows = []
        for f in files:
            rows.extend(transform.parse_page(f.read_text(encoding="utf-8"), run_date))
        rows = transform.dedupe(rows)
        log.info("parsed %d rows", len(rows))

        quality.assert_ok(rows)                      # gate: stop before loading bad data
        n = load.upsert(conn, rows)
        path = report.build(conn, run_date.isoformat(), config.REPORT_DIR)
        load.log_run(conn, run_date.isoformat(), "success", n)
        log.info("SUCCESS: %d rows loaded, report %s", n, path)
        return 0
    except Exception as err:
        load.log_run(conn, run_date.isoformat(), "failed", 0, str(err))
        log.exception("FAILED: %s", err)            # alert hook: plug email/Slack here
        return 1
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-raw", metavar="YYYY-MM-DD", help="replay a day from raw HTML")
    args = parser.parse_args()
    setup_logging()
    day = date.fromisoformat(args.from_raw) if args.from_raw else date.today()
    sys.exit(run(day, from_raw=bool(args.from_raw)))


if __name__ == "__main__":
    main()
