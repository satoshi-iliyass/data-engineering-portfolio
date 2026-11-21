"""Convert the raw CSV into a partitioned, compressed Parquet dataset (one folder per month).

    python -m src.convert
"""
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW_CSV = ROOT / "data" / "raw" / "sensor_readings.csv"
PARQUET_DIR = ROOT / "data" / "parquet"


def folder_size_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6


def main() -> None:
    t0 = time.perf_counter()
    duckdb.sql(f"""
        COPY (
            SELECT *, strftime(ts, '%Y-%m') AS month
            FROM read_csv('{RAW_CSV.as_posix()}', header=true,
                          columns={{'machine_id':'INTEGER','sensor':'VARCHAR','ts':'TIMESTAMP','value':'DOUBLE'}})
        ) TO '{PARQUET_DIR.as_posix()}'
        (FORMAT parquet, PARTITION_BY (month), COMPRESSION zstd, OVERWRITE_OR_IGNORE)
    """)
    csv_mb, pq_mb = RAW_CSV.stat().st_size / 1e6, folder_size_mb(PARQUET_DIR)
    print(f"CSV {csv_mb:.0f} MB -> Parquet {pq_mb:.0f} MB "
          f"({csv_mb / pq_mb:.1f}x smaller) in {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
