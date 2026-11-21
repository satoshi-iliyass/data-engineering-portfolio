"""Central configuration (no secrets in code)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # untouched HTML, one folder per run date
DB_PATH = DATA_DIR / "prices.db"    # clean, historised data
REPORT_DIR = ROOT / "reports"       # Excel output for the business user
LOG_DIR = ROOT / "logs"

# books.toscrape.com is a public sandbox site built for scraping practice.
BASE_URL = "https://books.toscrape.com/catalogue/"
START_PAGE = "page-1.html"
MAX_PAGES = 50                      # safety cap
REQUEST_DELAY_S = 0.5               # be polite to the server
TIMEOUT_S = 20
MAX_RETRIES = 3
USER_AGENT = "price-tracker-pipeline/1.0 (portfolio project)"

# Data quality thresholds
MIN_ROWS = 100                      # fewer rows than this = site probably changed
PRICE_RANGE = (0.01, 1000.0)
