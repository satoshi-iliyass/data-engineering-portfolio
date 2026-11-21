"""Pure Spark transformations (DataFrame in -> DataFrame out): unit-testable without touching disk.

bronze  raw monthly files, schema drift normalised, lineage columns
silver  typed, validated (rejects kept with a reason), de-duplicated, enriched with zones (broadcast join)
gold    business tables: daily KPIs by borough, hourly demand, top routes, tipping behaviour
"""
from functools import reduce

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

PAYMENT = {1: "credit card", 2: "cash", 3: "no charge", 4: "dispute", 0: "flex fare", 5: "unknown", 6: "voided"}
COLUMNS = ["vendorid", "tpep_pickup_datetime", "tpep_dropoff_datetime", "passenger_count", "trip_distance",
           "pulocationid", "dolocationid", "payment_type", "fare_amount", "tip_amount", "tolls_amount",
           "total_amount", "congestion_surcharge", "airport_fee"]


# ---------- bronze ----------

def normalise(df: DataFrame, source_file: str) -> DataFrame:
    """Monthly files drift (e.g. 'Airport_fee' vs 'airport_fee', int vs double): lower-case names,
    add missing columns as NULL, cast to one contract, keep lineage."""
    df = df.toDF(*[c.lower() for c in df.columns])
    for c in COLUMNS:
        if c not in df.columns:
            df = df.withColumn(c, F.lit(None))
    return df.select(
        F.col("vendorid").cast("int"),
        F.col("tpep_pickup_datetime").cast("timestamp").alias("pickup_ts"),
        F.col("tpep_dropoff_datetime").cast("timestamp").alias("dropoff_ts"),
        F.col("passenger_count").cast("int"),
        F.col("trip_distance").cast("double").alias("distance_mi"),
        F.col("pulocationid").cast("int").alias("pu_zone_id"),
        F.col("dolocationid").cast("int").alias("do_zone_id"),
        F.col("payment_type").cast("int"),
        *[F.col(c).cast("double") for c in ("fare_amount", "tip_amount", "tolls_amount", "total_amount",
                                             "congestion_surcharge", "airport_fee")],
        F.lit(source_file).alias("source_file"),
    )


def union_months(frames: list[DataFrame]) -> DataFrame:
    return reduce(lambda a, b: a.unionByName(b), frames)


# ---------- silver ----------

def with_reject_reason(df: DataFrame, start: str, end: str) -> DataFrame:
    """First failing rule wins. Rules are explicit so the client can discuss them."""
    minutes = (F.col("dropoff_ts").cast("long") - F.col("pickup_ts").cast("long")) / 60
    reason = (F.when(F.col("pickup_ts").isNull() | F.col("dropoff_ts").isNull(), "missing timestamp")
              .when((F.col("pickup_ts") < F.lit(start).cast("timestamp")) |
                    (F.col("pickup_ts") >= F.lit(end).cast("timestamp")), "pickup outside loaded period")
              .when(minutes <= 0, "dropoff before pickup")
              .when(minutes > 360, "trip longer than 6 h")
              .when((F.col("distance_mi") <= 0) | (F.col("distance_mi") > 200), "distance out of range")
              .when(F.col("fare_amount") < 0, "negative fare")
              .when(F.col("total_amount") <= 0, "non-positive total")
              .when(F.col("distance_mi") / (minutes / 60) > 100, "speed above 100 mph")
              .otherwise(None))
    return df.withColumn("trip_minutes", F.round(minutes, 2)).withColumn("reject_reason", reason)


def dedupe(df: DataFrame) -> DataFrame:
    """The same trip can be sent twice by a vendor: keep one row per natural key."""
    key = ["vendorid", "pickup_ts", "dropoff_ts", "pu_zone_id", "do_zone_id", "total_amount"]
    w = Window.partitionBy(*key).orderBy(F.col("source_file").desc())
    return df.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")


def enrich(df: DataFrame, zones: DataFrame) -> DataFrame:
    """Zone lookup is ~265 rows: broadcast it so the 9 M-row table is never shuffled for the join."""
    z = F.broadcast(zones.select(F.col("LocationID").cast("int").alias("zid"), "Borough", "Zone"))
    pu = z.select(F.col("zid").alias("pu_zone_id"), F.col("Borough").alias("pu_borough"), F.col("Zone").alias("pu_zone"))
    do = z.select(F.col("zid").alias("do_zone_id"), F.col("Borough").alias("do_borough"), F.col("Zone").alias("do_zone"))
    pay = F.create_map(*[x for k, v in PAYMENT.items() for x in (F.lit(k), F.lit(v))])
    return (df.join(pu, "pu_zone_id", "left").join(do, "do_zone_id", "left")
              .withColumn("pickup_date", F.to_date("pickup_ts"))
              .withColumn("pickup_hour", F.hour("pickup_ts"))
              .withColumn("weekday", F.date_format("pickup_ts", "E"))
              .withColumn("speed_mph", F.round(F.col("distance_mi") / (F.col("trip_minutes") / 60), 1))
              .withColumn("payment", F.coalesce(pay[F.col("payment_type")], F.lit("unknown")))
              .withColumn("tip_pct", F.when(F.col("fare_amount") > 0,
                                            F.round(100 * F.col("tip_amount") / F.col("fare_amount"), 1))))


def to_silver(bronze: DataFrame, zones: DataFrame, start: str, end: str) -> tuple[DataFrame, DataFrame]:
    checked = with_reject_reason(bronze, start, end)
    rejected = checked.filter(F.col("reject_reason").isNotNull())
    clean = dedupe(checked.filter(F.col("reject_reason").isNull()).drop("reject_reason"))
    return enrich(clean, zones), rejected


# ---------- gold ----------

def daily_borough_kpis(silver: DataFrame) -> DataFrame:
    return (silver.groupBy("pickup_date", "pu_borough")
            .agg(F.count("*").alias("trips"), F.round(F.sum("total_amount"), 2).alias("revenue_usd"),
                 F.round(F.avg("distance_mi"), 2).alias("avg_distance_mi"),
                 F.round(F.avg("trip_minutes"), 1).alias("avg_minutes"),
                 F.round(F.percentile_approx("total_amount", 0.5), 2).alias("median_fare_usd")))


def hourly_demand(silver: DataFrame) -> DataFrame:
    days = silver.select("pickup_date", "weekday").distinct().groupBy("weekday").count()
    return (silver.groupBy("weekday", "pickup_hour").agg(F.count("*").alias("trips"))
            .join(days, "weekday")
            .withColumn("avg_trips_per_hour", F.round(F.col("trips") / F.col("count"), 1)).drop("count"))


def top_routes(silver: DataFrame, n: int = 20) -> DataFrame:
    w = Window.orderBy(F.col("trips").desc())
    return (silver.filter(F.col("pu_zone").isNotNull() & F.col("do_zone").isNotNull())
            .groupBy("pu_zone", "do_zone")
            .agg(F.count("*").alias("trips"), F.round(F.avg("total_amount"), 2).alias("avg_total_usd"),
                 F.round(F.avg("trip_minutes"), 1).alias("avg_minutes"))
            .withColumn("rank", F.row_number().over(w)).filter(F.col("rank") <= n))


def tipping(silver: DataFrame) -> DataFrame:
    return (silver.groupBy("payment")
            .agg(F.count("*").alias("trips"), F.round(F.avg("tip_pct"), 1).alias("avg_tip_pct"),
                 F.round(F.sum("tip_amount"), 0).alias("total_tips_usd")))
