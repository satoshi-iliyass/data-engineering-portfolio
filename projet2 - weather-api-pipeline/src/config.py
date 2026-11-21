"""Configuration: cities, API, paths, quality thresholds."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "weather.db"
LOG_DIR = ROOT / "logs"

API_URL = "https://api.open-meteo.com/v1/forecast"   # free, no API key
HOURLY_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m"]
PAST_DAYS = 3        # overlap window: late corrections from the API are absorbed by the upsert
FORECAST_DAYS = 2
TIMEOUT_S = 20
MAX_RETRIES = 3

CITIES = {
    "Paris": (48.8566, 2.3522),
    "Lyon": (45.7640, 4.8357),
    "Casablanca": (33.5731, -7.5898),
    "Montreal": (45.5019, -73.5674),
    "Tokyo": (35.6762, 139.6503),
}

# Physical plausibility bounds used by the quality checks
BOUNDS = {
    "temperature_c": (-60, 60),
    "humidity_pct": (0, 100),
    "precipitation_mm": (0, 300),
    "wind_kmh": (0, 300),
}
