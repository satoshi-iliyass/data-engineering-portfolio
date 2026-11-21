"""Extract: call Open-Meteo for each city, keep the raw JSON untouched."""
import json
import logging
import time
from datetime import datetime, timezone

import requests

from . import config

log = logging.getLogger(__name__)


def fetch(city: str, lat: float, lon: float) -> dict:
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(config.HOURLY_VARS),
        "past_days": config.PAST_DAYS, "forecast_days": config.FORECAST_DAYS,
        "timezone": "UTC",
    }
    last_err = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.get(config.API_URL, params=params, timeout=config.TIMEOUT_S)
            if resp.status_code == 429 or resp.status_code >= 500:   # rate limit / server error -> retry
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as err:
            last_err = err
            log.warning("%s attempt %d/%d failed: %s", city, attempt, config.MAX_RETRIES, err)
            if attempt < config.MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"{city}: API failed after {config.MAX_RETRIES} attempts: {last_err}")


def save_raw(city: str, payload: dict, run_ts: datetime) -> None:
    folder = config.RAW_DIR / run_ts.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{city}_{run_ts.strftime('%H%M%S')}.json").write_text(json.dumps(payload), encoding="utf-8")


def extract_all() -> dict[str, dict]:
    run_ts = datetime.now(timezone.utc)
    out, failed = {}, []
    for city, (lat, lon) in config.CITIES.items():
        try:
            payload = fetch(city, lat, lon)
            save_raw(city, payload, run_ts)
            out[city] = payload
        except RuntimeError as err:          # one bad city must not kill the whole run
            log.error(err)
            failed.append(city)
    log.info("extracted %d cities, failed: %s", len(out), failed or "none")
    if not out:
        raise RuntimeError("all cities failed")
    return out
