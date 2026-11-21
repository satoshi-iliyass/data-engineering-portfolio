"""Analytics on the Parquet dataset with DuckDB: KPIs, anomaly detection, CSV vs Parquet benchmark.

    python -m src.analytics
"""
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW_CSV = ROOT / "data" / "raw" / "sensor_readings.csv"
PQ = (ROOT / "data" / "parquet").as_posix() + "/**/*.parquet"
OUT = ROOT / "results"

QUERIES = {
    # Data quality: how many readings are missing, per sensor
    "data_quality": f"""
        SELECT sensor, COUNT(*) AS readings, COUNT(*) - COUNT(value) AS missing,
               ROUND(100.0 * (COUNT(*) - COUNT(value)) / COUNT(*), 3) AS missing_pct
        FROM read_parquet('{PQ}', hive_partitioning=true) GROUP BY sensor ORDER BY sensor""",

    # Daily health per machine (what a maintenance dashboard shows)
    "daily_machine_kpis": f"""
        SELECT machine_id, CAST(ts AS DATE) AS day,
               ROUND(AVG(value) FILTER (WHERE sensor='temperature'), 2) AS temp_avg,
               ROUND(MAX(value) FILTER (WHERE sensor='temperature'), 2) AS temp_max,
               ROUND(AVG(value) FILTER (WHERE sensor='vibration'), 3)   AS vib_avg
        FROM read_parquet('{PQ}', hive_partitioning=true)
        GROUP BY ALL ORDER BY machine_id, day""",

    # Anomalies: value more than 4 standard deviations from that machine/sensor's mean
    "spikes": f"""
        WITH stats AS (
            SELECT machine_id, sensor, AVG(value) mu, STDDEV(value) sd
            FROM read_parquet('{PQ}', hive_partitioning=true) GROUP BY ALL)
        SELECT r.machine_id, r.sensor, COUNT(*) AS spikes
        FROM read_parquet('{PQ}', hive_partitioning=true) r JOIN stats s USING (machine_id, sensor)
        WHERE ABS(r.value - s.mu) > 4 * s.sd
        GROUP BY ALL ORDER BY spikes DESC""",

    # Predictive maintenance: machines whose vibration trend is rising (slope of a linear fit)
    "degradation_ranking": f"""
        WITH daily AS (
            SELECT machine_id, CAST(ts AS DATE) AS day, AVG(value) AS vib
            FROM read_parquet('{PQ}', hive_partitioning=true)
            WHERE sensor = 'vibration' GROUP BY ALL)
        SELECT machine_id,
               ROUND(REGR_SLOPE(vib, EPOCH(day) / 86400.0), 4) AS vib_slope_per_day,
               ROUND(MIN(vib), 2) AS vib_start_min, ROUND(MAX(vib), 2) AS vib_max,
               CASE WHEN REGR_SLOPE(vib, EPOCH(day) / 86400.0) > 0.01 THEN 'INSPECT' ELSE 'ok' END AS action
        FROM daily GROUP BY machine_id ORDER BY vib_slope_per_day DESC""",
}

BENCH = "SELECT machine_id, AVG(value) FROM {src} WHERE sensor = 'vibration' GROUP BY machine_id"


def timed(sql: str) -> float:
    t0 = time.perf_counter()
    duckdb.sql(sql).fetchall()
    return time.perf_counter() - t0


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, sql in QUERIES.items():
        t = time.perf_counter()
        rel = duckdb.sql(sql)
        rel.write_csv(str(OUT / f"{name}.csv"))
        print(f"\n== {name} ({time.perf_counter() - t:.2f}s)")
        print(duckdb.sql(sql).limit(6))

    csv_src = f"read_csv('{RAW_CSV.as_posix()}', header=true)"
    pq_src = f"read_parquet('{PQ}', hive_partitioning=true)"
    t_csv, t_pq = timed(BENCH.format(src=csv_src)), timed(BENCH.format(src=pq_src))
    line = f"Benchmark same query: CSV {t_csv:.2f}s vs Parquet {t_pq:.2f}s -> {t_csv / t_pq:.1f}x faster"
    print("\n" + line)
    (OUT / "benchmark.txt").write_text(line + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
