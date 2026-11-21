"""Download the official NYC TLC open data (yellow taxi trips + zone lookup). Idempotent: skips existing files.

    python -m src.download --months 2024-01 2024-02 2024-03
"""
import argparse
import urllib.request
from pathlib import Path

BASE = "https://d37ci6vzurychx.cloudfront.net"
ROOT = Path(__file__).resolve().parent.parent


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)          # write to .part then rename: no half files
    tmp.replace(dest)
    print(f"downloaded {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")


def main(months: list[str], landing: Path) -> None:
    for m in months:
        fetch(f"{BASE}/trip-data/yellow_tripdata_{m}.parquet", landing / f"yellow_tripdata_{m}.parquet")
    fetch(f"{BASE}/misc/taxi_zone_lookup.csv", landing / "taxi_zone_lookup.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", default=["2024-01", "2024-02", "2024-03"])
    ap.add_argument("--landing", type=Path, default=ROOT / "data" / "landing")
    a = ap.parse_args()
    main(a.months, a.landing)
