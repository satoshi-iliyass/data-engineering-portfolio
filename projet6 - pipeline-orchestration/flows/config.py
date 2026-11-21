"""Jobs the platform orchestrates, their SLAs and alerting settings (secrets come from env vars)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO = ROOT.parent
REPORT_DIR = ROOT / "reports"

# Each job is an existing, independently tested pipeline. The orchestrator only runs and watches them.
JOBS = {
    "weather_api": {
        "dir": PORTFOLIO / "projet2 - weather-api-pipeline",
        "cmd": ["-m", "src.pipeline"],
        "timeout_s": 300,
        "db": "data/weather.db",
        "freshness_sql": "SELECT MAX(fetched_at) FROM hourly_weather",
        "max_age_h": 26,
    },
    "price_tracker": {
        "dir": PORTFOLIO / "projet1 - price-tracker-pipeline",
        "cmd": ["-m", "src.pipeline"],
        "timeout_s": 900,
        "db": "data/prices.db",
        "freshness_sql": "SELECT MAX(scraped_on) || 'T00:00:00+00:00' FROM prices",
        "max_age_h": 48,
    },
    "excel_consolidation": {
        "dir": PORTFOLIO / "projet3 - excel-automation",
        "cmd": ["-m", "src.main"],
        "timeout_s": 300,
        "depends_on": ["price_tracker"],   # example dependency: runs only after upstream data landed
    },
}

RETRIES = 2
RETRY_DELAYS_S = [30, 120]          # back-off between attempts
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")   # optional; alerts always go to reports/alerts.log too
DAILY_CRON = "0 6 * * *"            # 06:00 every day
