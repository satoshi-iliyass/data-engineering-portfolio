from src import load, quality, transform

PAYLOAD = {"hourly": {
    "time": ["2026-01-01T00:00", "2026-01-01T01:00", "2026-01-01T02:00"],
    "temperature_2m": [5.0, 6.5, 999.0],          # 999 = sensor glitch
    "relative_humidity_2m": [80, 82, 85],
    "precipitation": [0.0, 1.2, 0.0],
    "wind_speed_10m": [10.0, 12.0, 11.0],
}}


def rows(fetched="2026-01-01T03:00:00"):
    return transform.flatten("Paris", PAYLOAD, fetched)


def test_flatten_one_row_per_hour():
    r = rows()
    assert len(r) == 3
    assert r[0] == {"city": "Paris", "ts_utc": "2026-01-01T00:00", "fetched_at": "2026-01-01T03:00:00",
                    "temperature_c": 5.0, "humidity_pct": 80, "precipitation_mm": 0.0, "wind_kmh": 10.0}


def test_flatten_handles_missing_variable():
    p = {"hourly": {"time": ["2026-01-01T00:00"], "temperature_2m": [1.0]}}
    assert transform.flatten("X", p, "t")[0]["wind_kmh"] is None


def test_quality_quarantines_glitch():
    good, bad = quality.split(rows())
    assert len(good) == 2 and len(bad) == 1
    assert "temperature_c=999.0" in bad[0]["reason"]


def test_incremental_upsert(tmp_path):
    conn = load.connect(tmp_path / "w.db")
    good, _ = quality.split(rows())
    assert load.upsert(conn, good) == (2, 0)          # first run: 2 inserted
    assert load.upsert(conn, good) == (0, 2)          # re-run: 0 inserted, 2 updated, no duplicates
    corrected = [{**good[0], "temperature_c": 5.5}]
    load.upsert(conn, corrected)                      # late API correction overwrites
    assert conn.execute("SELECT temperature_c FROM hourly_weather WHERE ts_utc='2026-01-01T00:00'").fetchone()[0] == 5.5


def test_daily_view(tmp_path):
    conn = load.connect(tmp_path / "w.db")
    good, _ = quality.split(rows())
    load.upsert(conn, good)
    day = conn.execute("SELECT t_min, t_max, rain_mm, hours FROM daily_weather").fetchone()
    assert day == (5.0, 6.5, 1.2, 2)
