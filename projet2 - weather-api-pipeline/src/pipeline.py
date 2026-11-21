"""Orchestrator: extract -> flatten -> validate/quarantine -> incremental upsert.

    python -m src.pipeline
"""
import logging
import sys
from datetime import datetime, timezone

from . import config, extract, load, quality, transform

log = logging.getLogger("pipeline")


def setup_logging():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(config.LOG_DIR / "pipeline.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])


def run() -> int:
    conn = load.connect(config.DB_PATH)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        payloads = extract.extract_all()
        rows = [r for city, p in payloads.items() for r in transform.flatten(city, p, fetched_at)]
        good, bad = quality.split(rows)
        if bad:
            load.quarantine(conn, bad)
            log.warning("%d rows quarantined", len(bad))
        inserted, updated = load.upsert(conn, good)
        load.log_run(conn, "success", inserted, updated, len(bad))
        log.info("SUCCESS inserted=%d updated=%d rejected=%d", inserted, updated, len(bad))
        return 0
    except Exception as err:
        load.log_run(conn, "failed", message=str(err))
        log.exception("FAILED: %s", err)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    setup_logging()
    sys.exit(run())
