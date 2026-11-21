"""Transform: flatten Open-Meteo's column-oriented JSON into one row per city per hour."""

FIELD_MAP = {
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "humidity_pct",
    "precipitation": "precipitation_mm",
    "wind_speed_10m": "wind_kmh",
}


def flatten(city: str, payload: dict, fetched_at: str) -> list[dict]:
    hourly = payload["hourly"]
    times = hourly["time"]
    rows = []
    for i, ts in enumerate(times):
        row = {"city": city, "ts_utc": ts, "fetched_at": fetched_at}
        for api_name, col in FIELD_MAP.items():
            values = hourly.get(api_name, [])
            row[col] = values[i] if i < len(values) else None
        rows.append(row)
    return rows


def daily_summary_sql() -> str:
    """Daily aggregates are computed in SQL (in the database), not in Python."""
    return """
    CREATE VIEW IF NOT EXISTS daily_weather AS
    SELECT city,
           substr(ts_utc, 1, 10)            AS day,
           ROUND(MIN(temperature_c), 1)     AS t_min,
           ROUND(MAX(temperature_c), 1)     AS t_max,
           ROUND(AVG(temperature_c), 1)     AS t_avg,
           ROUND(SUM(precipitation_mm), 1)  AS rain_mm,
           ROUND(MAX(wind_kmh), 1)          AS wind_max,
           COUNT(*)                         AS hours
    FROM hourly_weather
    GROUP BY city, day;
    """
