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


def flatten_all(payloads: dict, fetched_at: str) -> tuple[list[dict], list[str]]:
    """One city with an unreadable answer must not lose the other cities."""
    rows, failed = [], []
    for city, payload in payloads.items():
        try:
            rows.extend(transform.flatten(city, payload, fetched_at))
        except (KeyError, TypeError) as err:
            log.error("%s: unreadable answer: %r", city, err)
            failed.append(city)
    if not rows:
        raise RuntimeError("no usable rows")
    return rows, failed


def run() -> int:
    conn = load.connect(config.DB_PATH)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        payloads = extract.extract_all()
        rows, unreadable = flatten_all(payloads, fetched_at)
        missing = sorted(set(config.CITIES) - set(payloads)) + unreadable
        good, bad = quality.split(rows)
        if bad:
            load.quarantine(conn, bad)
            log.warning("%d rows quarantined", len(bad))
        inserted, updated = load.upsert(conn, good)
        status = "partial" if missing else "success"
        message = "missing cities: " + ", ".join(missing) if missing else ""
        load.log_run(conn, status, inserted, updated, len(bad), message)
        log.info("%s inserted=%d updated=%d rejected=%d %s", status.upper(), inserted, updated, len(bad), message)
        return 2 if missing else 0              # 2 = data stored, but not for every city
    except Exception as err:
        load.log_run(conn, "failed", message=str(err))
        log.exception("FAILED: %s", err)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    setup_logging()
    sys.exit(run())
