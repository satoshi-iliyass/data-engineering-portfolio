"""Extract: download catalogue pages with retries and keep a raw copy of each."""
import logging
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import config

log = logging.getLogger(__name__)


def is_permanent(err: requests.RequestException) -> bool:
    """4xx = our request is wrong (e.g. 404), retrying is useless. 429 = 'slow down', so retry."""
    if err.response is None:
        return False
    code = err.response.status_code
    return 400 <= code < 500 and code != 429


def fetch(url: str, session: requests.Session) -> str:
    """GET; temporary failures are retried (wait 1 s, then 2 s), a missing page (4xx) is not."""
    last_err = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=config.TIMEOUT_S)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp.text
        except requests.RequestException as err:
            last_err = err
            log.warning("GET %s attempt %d/%d failed: %s", url, attempt, config.MAX_RETRIES, err)
            if is_permanent(err):
                break
            if attempt < config.MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"giving up on {url}: {last_err}")


def next_page(html: str) -> str | None:
    link = BeautifulSoup(html, "html.parser").select_one("li.next > a")
    return link["href"] if link else None


def save_raw(html: str, page_name: str, run_date: date) -> Path:
    folder = config.RAW_DIR / run_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / page_name
    path.write_text(html, encoding="utf-8")
    return path


def extract_all(run_date: date) -> list[Path]:
    """Follow the 'next' link page by page; return the saved raw files."""
    session = requests.Session()
    session.headers["User-Agent"] = config.USER_AGENT
    page, saved = config.START_PAGE, []
    while page and len(saved) < config.MAX_PAGES:
        html = fetch(config.BASE_URL + page, session)
        saved.append(save_raw(html, page, run_date))
        page = next_page(html)
        time.sleep(config.REQUEST_DELAY_S)
    log.info("extracted %d pages", len(saved))
    return saved
