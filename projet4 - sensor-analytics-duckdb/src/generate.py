"""Generate a large, realistic industrial sensor dataset (default 10 million rows) as CSV.

20 machines x 4 sensors (temperature, vibration, pressure, current), one reading every 15 s.
Includes realistic defects: gradual bearing wear on 2 machines, random spikes, missing readings.
Generated inside DuckDB (SQL) so it takes seconds and very little RAM.

    python -m src.generate --rows 10000000
"""
import argparse
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW_CSV = ROOT / "data" / "raw" / "sensor_readings.csv"

SQL = """
COPY (
  WITH base AS (
    SELECT i,
           1 + (i % 20)                                   AS machine_id,
           ['temperature','vibration','pressure','current'][1 + ((i // 20) % 4)] AS sensor,
           TIMESTAMP '2026-01-01' + INTERVAL 15 SECOND * (i // 80) AS ts,
           (i // 80) / {steps}::DOUBLE                      AS progress      -- 0 -> 1 over the period
    FROM range({rows}) t(i)
  )
  SELECT machine_id, sensor, ts,
    CASE
      WHEN random() < 0.002 THEN NULL                       -- dropped reading
      ELSE round(
        CASE sensor
          WHEN 'temperature' THEN 65 + 5*random()
               + CASE WHEN machine_id IN (7, 13) THEN 25*progress ELSE 0 END   -- overheating trend
          WHEN 'vibration'   THEN 2.0 + 0.6*random()
               + CASE WHEN machine_id IN (7, 13) THEN 6*progress*progress ELSE 0 END  -- bearing wear
          WHEN 'pressure'    THEN 6.0 + 0.4*random()
          ELSE                    12 + 2*random()
        END
        + CASE WHEN random() < 0.0005 THEN 40 ELSE 0 END, 3) -- rare spike
    END AS value
  FROM base
) TO '{out}' (HEADER, DELIMITER ',');
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=10_000_000)
    args = ap.parse_args()
    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    duckdb.sql(SQL.format(rows=args.rows, steps=max(args.rows // 80, 1), out=RAW_CSV.as_posix()))
    size_mb = RAW_CSV.stat().st_size / 1e6
    print(f"{args.rows:,} rows -> {RAW_CSV.name} ({size_mb:.0f} MB) in {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
