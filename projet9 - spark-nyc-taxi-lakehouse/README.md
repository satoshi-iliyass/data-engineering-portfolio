# Spark Lakehouse — 9.5 M real NYC taxi trips, bronze → silver → gold

A PySpark batch pipeline on the **official NYC TLC open data** (yellow taxi trips, January–March 2024): ingestion with schema-drift handling, validation with a quarantine, de-duplication, zone enrichment with a broadcast join, and business-ready gold tables — stored as a partitioned Parquet lakehouse.

Typical client request: *"Our pandas scripts died when the data reached millions of rows. We need something that scales and that we can move to Databricks / EMR later."*

```
 NYC TLC open data            BRONZE                         SILVER                                    GOLD
 3 monthly Parquet files ──► normalised schema      ──► typed + validated + de-duplicated   ──► daily_borough_kpis
 + zone lookup CSV            (drift: Airport_fee /      + zones (broadcast join)             ├► hourly_demand (weekday × hour)
 (160 MB, 9.5 M rows)         airport_fee, types)        partitioned by pickup_date (91 days) ├► top_routes
                              lineage: source_file       └► quarantine/ with reject_reason    └► tipping by payment type
```

## Verified result (GitHub Actions, standard 4-core runner)

| Metric | Value |
|---|---|
| Rows ingested (bronze) | **9,554,778** |
| Clean rows (silver) | **9,203,598** in 91 daily partitions |
| Rejected to quarantine | **351,179 (3.68 %)**, each with its reason |
| Total pipeline time | **~60–85 s** depending on the runner (last run: bronze 10.8 s · silver 43.4 s · gold 5.7 s) |
| Queries on silver | revenue for one day: 0.5 s · all 91 days: 0.6 s. Data is stored in 91 day folders, but no measurable gain at this size; partition pruning is not proven here |
| Zone join | executed as **BroadcastHashJoin** (the 9.5 M-row table is never shuffled for it) |

**Rejection reasons** (rules are explicit, so they can be discussed with the client): distance out of range 213,413 · negative fare 126,030 · trip longer than 6 h 5,434 · speed above 100 mph 2,841 · drop-off before pick-up 2,801 · non-positive total 639 · pick-up outside the loaded period 21.

**What the gold tables say**
- Manhattan: 8.28 M trips, **$191 M**; Queens: 0.79 M trips but $57 M (airport runs are long and expensive)
- Busiest route: Upper East Side South → Upper East Side North (63,545 trips)
- Credit-card riders tip **25.6 %** of the fare on average; cash tips are not recorded (0 %) — a known data bias worth flagging to analysts
- Peak demand: **Thursday 18:00**, ~8,960 trips per hour

## Engineering features

| Feature | How |
|---|---|
| Schema drift | Column names lower-cased, missing columns added as NULL, one typed contract for all months |
| Lineage | `source_file` + `ingested_at` on every bronze row |
| Quarantine, not silent drop | Invalid rows written to `quarantine/` with `reject_reason` |
| De-duplication | Window `row_number()` on the natural key of a trip |
| Join optimisation | Broadcast of the 265-row zone table |
| Layout | `repartition(pickup_date)` before writing → one set of files per day, no tiny-file explosion |
| Adaptive execution | AQE on, shuffle partitions tuned, zstd compression |
| Portable | Spark master and memory come from environment variables. Only run on a single Linux machine so far (not on Databricks or EMR) |

## Run

```bash
pip install -r requirements.txt          # needs Java 17
python -m src.download                   # official TLC files (idempotent)
python -m src.pipeline                   # -> data/lake/{bronze,silver,quarantine,gold} + reports/spark_report.md
python -m pytest -q
```

**13 unit tests** on in-memory DataFrames: schema drift, each of the 7 reject rules, valid trip kept, duplicates removed, enrichment + broadcast join in the physical plan, unknown zones kept, every gold table.
**CI**: on every push, GitHub Actions runs the tests, downloads the data (cached) and runs the **full pipeline on the 9.5 M rows**; the report is shown in the job summary and attached as an artifact.

## Stack

PySpark 4 (DataFrame API, window functions, broadcast joins, AQE) · Parquet (zstd, Hive partitioning) · NYC TLC open data · GitHub Actions · pytest
