"""Spark batch job: landing -> bronze -> silver -> gold (Parquet lakehouse), with metrics.

    python -m src.pipeline --months 2024-01 2024-02 2024-03

Runs on Linux / a cluster (GitHub Actions in this repo). Writes reports/spark_report.md and metrics.json.
"""
import argparse
import json
import os

import time
from datetime import date
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from . import transforms as T

ROOT = Path(__file__).resolve().parent.parent


def spark_session(app: str = "nyc-taxi-lakehouse") -> SparkSession:
    return (SparkSession.builder.appName(app)
            .master(os.getenv("SPARK_MASTER", "local[*]"))
            .config("spark.driver.memory", os.getenv("SPARK_DRIVER_MEMORY", "6g"))
            .config("spark.sql.adaptive.enabled", "true")            # AQE: coalesce small shuffle partitions
            .config("spark.sql.shuffle.partitions", "64")
            .config("spark.sql.session.timeZone", "UTC")
            .config("spark.sql.parquet.compression.codec", "zstd")
            .config("spark.ui.enabled", "false")
            .getOrCreate())


def folder_mb(p: Path) -> float:
    return round(sum(f.stat().st_size for f in p.rglob("*.parquet")) / 1e6, 3)


def month_end(m: str) -> str:
    y, mo = map(int, m.split("-"))
    return date(y + mo // 12, mo % 12 + 1, 1).isoformat()


def run(months: list[str], landing: Path, lake: Path) -> dict:
    spark = spark_session()
    t, metrics = {}, {"months": months}

    t0 = time.perf_counter()
    frames = [T.normalise(spark.read.parquet(str(landing / f"yellow_tripdata_{m}.parquet")),
                          f"yellow_tripdata_{m}.parquet") for m in months]
    bronze = T.union_months(frames).withColumn("ingested_at", F.current_timestamp())
    bronze.write.mode("overwrite").partitionBy("source_file").parquet(str(lake / "bronze" / "trips"))
    bronze = spark.read.parquet(str(lake / "bronze" / "trips"))
    metrics["bronze_rows"] = bronze.count()
    t["bronze"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    zones = spark.read.option("header", True).csv(str(landing / "taxi_zone_lookup.csv"))
    silver, rejected = T.to_silver(bronze, zones, f"{months[0]}-01", month_end(months[-1]))
    metrics["broadcast_join"] = "BroadcastHashJoin" in silver._jdf.queryExecution().executedPlan().toString()
    (silver.repartition("pickup_date")                                  # one set of files per day, no tiny files
           .write.mode("overwrite").partitionBy("pickup_date").parquet(str(lake / "silver" / "trips")))
    rejected.write.mode("overwrite").parquet(str(lake / "quarantine" / "trips"))
    silver = spark.read.parquet(str(lake / "silver" / "trips")).cache()
    metrics["silver_rows"] = silver.count()
    reasons = rejected.groupBy("reject_reason").count().orderBy(F.desc("count")).collect()
    metrics["rejected"] = {r["reject_reason"]: r["count"] for r in reasons}
    metrics["rejected_rows"] = sum(metrics["rejected"].values())
    metrics["duplicates_removed"] = metrics["bronze_rows"] - metrics["rejected_rows"] - metrics["silver_rows"]
    t["silver"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    gold = {"daily_borough_kpis": T.daily_borough_kpis(silver), "hourly_demand": T.hourly_demand(silver),
            "top_routes": T.top_routes(silver), "tipping": T.tipping(silver)}
    for name, df in gold.items():
        df.coalesce(1).write.mode("overwrite").parquet(str(lake / "gold" / name))
    t["gold"] = time.perf_counter() - t0

    # partition pruning, measured: the same aggregation on one day vs. on all days (fresh reads, no cache)
    silver_dir = lake / "silver" / "trips"
    day = F.to_date(F.lit(f"{months[0]}-15"))
    agg = [F.count("*").alias("trips"), F.sum("total_amount").alias("revenue")]
    t0 = time.perf_counter()
    metrics["all_days_trips"] = spark.read.parquet(str(silver_dir)).agg(*agg).first()["trips"]
    t["all_days_query"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    metrics["one_day_trips"] = spark.read.parquet(str(silver_dir)).filter(F.col("pickup_date") == day) \
        .agg(*agg).first()["trips"]
    t["one_day_query"] = time.perf_counter() - t0
    metrics["one_day_files"] = len(list((silver_dir / f"pickup_date={months[0]}-15").glob("*.parquet")))
    metrics["total_silver_files"] = len(list(silver_dir.rglob("*.parquet")))

    metrics["seconds"] = {k: round(v, 1) for k, v in t.items()}
    metrics["sizes_mb"] = {"landing": round(sum((landing / f"yellow_tripdata_{m}.parquet").stat().st_size
                                                for m in months) / 1e6, 1),
                           "bronze": folder_mb(lake / "bronze"), "silver": folder_mb(lake / "silver"),
                           "gold": folder_mb(lake / "gold")}
    metrics["silver_days"] = len(list((lake / "silver" / "trips").glob("pickup_date=*")))
    metrics["gold_preview"] = {
        "tipping": [r.asDict() for r in gold["tipping"].orderBy(F.desc("trips")).collect()],
        "top_routes": [r.asDict() for r in spark.read.parquet(str(lake / "gold" / "top_routes"))
                       .orderBy("rank").limit(5).collect()],
        "borough_totals": [r.asDict() for r in spark.read.parquet(str(lake / "gold" / "daily_borough_kpis"))
                           .groupBy("pu_borough").agg(F.sum("trips").alias("trips"),
                                                      F.round(F.sum("revenue_usd"), 0).alias("revenue_usd"))
                           .orderBy(F.desc("trips")).limit(6).collect()],
        "peak_hour": spark.read.parquet(str(lake / "gold" / "hourly_demand"))
        .orderBy(F.desc("avg_trips_per_hour")).first().asDict(),
    }
    spark.stop()
    write_report(metrics)
    return metrics


def write_report(m: dict) -> None:
    s, z = m["seconds"], m["sizes_mb"]
    lines = [f"# Spark run report — NYC yellow taxi {m['months'][0]} to {m['months'][-1]}", "",
             f"- Bronze rows: **{m['bronze_rows']:,}** ({z['landing']} MB official Parquet)",
             f"- Silver rows: **{m['silver_rows']:,}** across {m['silver_days']} daily partitions",
             f"- Rejected to quarantine: **{m['rejected_rows']:,}** "
             f"({100 * m['rejected_rows'] / m['bronze_rows']:.2f} %), duplicates removed: {m['duplicates_removed']:,}",
             f"- Stage times: bronze {s['bronze']} s, silver {s['silver']} s, gold {s['gold']} s",
             f"- Partition pruning: revenue query on one day ({m['one_day_trips']:,} trips, folder of "
             f"{m['one_day_files']} of {m['total_silver_files']} files) {s['one_day_query']} s vs all 91 days "
             f"({m['all_days_trips']:,} trips) {s['all_days_query']} s",
             f"- Zone join executed as broadcast hash join: {m['broadcast_join']}",
             f"- Lake size (zstd Parquet): bronze {z['bronze']:.1f} MB, silver {z['silver']:.1f} MB, "
             f"gold {z['gold'] * 1000:.0f} KB", "",
             "## Rejection reasons", "", "| Reason | Rows |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in m["rejected"].items()]
    g = m["gold_preview"]
    lines += ["", "## Trips and revenue by pickup borough", "", "| Borough | Trips | Revenue (USD) |", "|---|---|---|"]
    lines += [f"| {r['pu_borough']} | {r['trips']:,} | {r['revenue_usd']:,.0f} |" for r in g["borough_totals"]]
    lines += ["", "## Top 5 routes", "", "| # | From | To | Trips | Avg total (USD) |", "|---|---|---|---|---|"]
    lines += [f"| {r['rank']} | {r['pu_zone']} | {r['do_zone']} | {r['trips']:,} | {r['avg_total_usd']} |"
              for r in g["top_routes"]]
    lines += ["", "## Tipping by payment type", "", "| Payment | Trips | Avg tip % of fare |", "|---|---|---|"]
    lines += [f"| {r['payment']} | {r['trips']:,} | {r['avg_tip_pct']} |" for r in g["tipping"]]
    p = g["peak_hour"]
    lines += ["", f"Peak demand: **{p['weekday']} {p['pickup_hour']}:00** with {p['avg_trips_per_hour']:,} trips/hour on average."]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "spark_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "reports" / "metrics.json").write_text(json.dumps(m, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", default=["2024-01", "2024-02", "2024-03"])
    ap.add_argument("--landing", type=Path, default=ROOT / "data" / "landing")
    ap.add_argument("--lake", type=Path, default=ROOT / "data" / "lake")
    a = ap.parse_args()
    out = run(a.months, a.landing, a.lake)
    print(json.dumps({k: v for k, v in out.items() if k != "gold_preview"}, indent=1, default=str))
