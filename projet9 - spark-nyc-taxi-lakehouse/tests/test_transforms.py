"""Unit tests on small in-memory DataFrames (no file writes: also runs on Windows without Hadoop tools)."""
import os
from datetime import datetime
from pathlib import Path

import pytest

jdk = next((Path(__file__).resolve().parents[2] / "_outils").glob("jdk-17*"), None) \
    if (Path(__file__).resolve().parents[2] / "_outils").exists() else None
if jdk and "JAVA_HOME" not in os.environ:          # local dev: portable JDK from _outils
    os.environ["JAVA_HOME"] = str(jdk)
os.environ.setdefault("PYSPARK_PYTHON", "python")

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402

from src import transforms as T  # noqa: E402


@pytest.fixture(scope="session")
def spark():
    s = (SparkSession.builder.master("local[2]").appName("tests").config("spark.ui.enabled", "false")
         .config("spark.sql.shuffle.partitions", "4").config("spark.sql.session.timeZone", "UTC").getOrCreate())
    yield s
    s.stop()


def ts(s):
    return datetime.fromisoformat(s + "+00:00")        # explicit UTC: tests independent of the machine's timezone


RAW_COLS = ["VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime", "passenger_count", "trip_distance",
            "PULocationID", "DOLocationID", "payment_type", "fare_amount", "tip_amount", "tolls_amount",
            "total_amount", "congestion_surcharge", "Airport_fee"]


def raw(spark, rows):
    return spark.createDataFrame(rows, RAW_COLS)


def trip(pu="2024-01-10 08:00:00", do="2024-01-10 08:20:00", dist=3.0, fare=15.0, tip=3.0, total=20.0,
         pu_zone=1, do_zone=2, pay=1, vendor=1):
    return (vendor, ts(pu), ts(do), 1.0, dist, pu_zone, do_zone, pay, fare, tip, 0.0, total, 2.5, 0.0)


@pytest.fixture
def zones(spark):
    return spark.createDataFrame([("1", "EWR", "Newark Airport"), ("2", "Manhattan", "Midtown")],
                                 ["LocationID", "Borough", "Zone"])


def test_normalise_handles_schema_drift(spark):
    df = raw(spark, [trip()]).drop("Airport_fee").withColumnRenamed("VendorID", "vendorID")
    out = T.normalise(df, "f.parquet")
    assert out.columns[:3] == ["vendorid", "pickup_ts", "dropoff_ts"]
    row = out.first()
    assert row.airport_fee is None and row.source_file == "f.parquet" and row.vendorid == 1


@pytest.mark.parametrize("kwargs,reason", [
    (dict(do="2024-01-10 07:59:00"), "dropoff before pickup"),
    (dict(pu="2023-12-31 23:00:00", do="2023-12-31 23:10:00"), "pickup outside loaded period"),
    (dict(do="2024-01-10 15:00:00"), "trip longer than 6 h"),
    (dict(dist=0.0), "distance out of range"),
    (dict(fare=-5.0), "negative fare"),
    (dict(total=0.0), "non-positive total"),
    (dict(dist=60.0, do="2024-01-10 08:10:00"), "speed above 100 mph"),
])
def test_reject_rules(spark, kwargs, reason):
    df = T.with_reject_reason(T.normalise(raw(spark, [trip(**kwargs)]), "f"), "2024-01-01", "2024-02-01")
    assert df.first().reject_reason == reason


def test_valid_trip_is_not_rejected(spark):
    df = T.with_reject_reason(T.normalise(raw(spark, [trip()]), "f"), "2024-01-01", "2024-02-01")
    r = df.first()
    assert r.reject_reason is None and r.trip_minutes == 20.0


def test_duplicates_removed_but_distinct_trips_kept(spark, zones):
    rows = [trip(), trip(), trip(pu="2024-01-10 09:00:00", do="2024-01-10 09:30:00")]
    silver, rejected = T.to_silver(T.normalise(raw(spark, rows), "f"), zones, "2024-01-01", "2024-02-01")
    assert silver.count() == 2 and rejected.count() == 0


def test_enrichment_and_broadcast_join(spark, zones):
    silver, _ = T.to_silver(T.normalise(raw(spark, [trip(pu_zone=1, do_zone=2, pay=2, tip=0.0)]), "f"),
                            zones, "2024-01-01", "2024-02-01")
    r = silver.first()
    assert (r.pu_borough, r.do_zone, r.payment, r.pickup_hour, r.speed_mph) == ("EWR", "Midtown", "cash", 8, 9.0)
    assert "BroadcastHashJoin" in silver._jdf.queryExecution().executedPlan().toString()


def test_unknown_zone_is_kept_with_null_borough(spark, zones):
    silver, _ = T.to_silver(T.normalise(raw(spark, [trip(pu_zone=999)]), "f"), zones, "2024-01-01", "2024-02-01")
    assert silver.count() == 1 and silver.first().pu_borough is None


def test_gold_tables(spark, zones):
    rows = [trip(), trip(pu="2024-01-10 08:05:00", do="2024-01-10 08:30:00", total=30.0),
            trip(pu="2024-01-11 18:00:00", do="2024-01-11 18:10:00", pu_zone=2, do_zone=1, pay=2, tip=0.0)]
    silver, _ = T.to_silver(T.normalise(raw(spark, rows), "f"), zones, "2024-01-01", "2024-02-01")
    k = {(r.pickup_date.isoformat(), r.pu_borough): r for r in T.daily_borough_kpis(silver).collect()}
    assert k[("2024-01-10", "EWR")].trips == 2 and k[("2024-01-10", "EWR")].revenue_usd == 50.0
    routes = T.top_routes(silver).orderBy("rank").collect()
    assert routes[0].pu_zone == "Newark Airport" and routes[0].trips == 2
    tips = {r.payment: r for r in T.tipping(silver).collect()}
    assert tips["credit card"].avg_tip_pct == 20.0 and tips["cash"].avg_tip_pct == 0.0
    demand = T.hourly_demand(silver).filter(F.col("pickup_hour") == 8).first()
    assert demand.weekday == "Wed" and demand.avg_trips_per_hour == 2.0
