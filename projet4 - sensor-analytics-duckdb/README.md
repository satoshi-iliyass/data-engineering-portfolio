# Industrial Sensor Analytics — 10 million rows on a laptop with DuckDB + Parquet

Predictive-maintenance analytics on **10 million IoT sensor readings** (20 machines × 4 sensors, every 15 s for ~3 weeks), processed locally in seconds — no server, no cloud bill.

```
generate (SQL) ──► raw CSV 387 MB ──► convert ──► Parquet 23 MB, partitioned by month, zstd
                                                        │
                                                        ▼
                                  DuckDB SQL: data quality · daily KPIs · spike detection
                                              · degradation ranking (which machine to inspect)
                                                        │
                                                        ▼
                                              results/*.csv + benchmark.txt
```

## Results (measured on a standard laptop)

| | |
|---|---|
| Rows | **10,000,000** |
| CSV → Parquet | **387 MB → 23 MB (17× smaller)**, converted in < 1 s |
| Same aggregation query | **CSV 0.32 s vs Parquet 0.03 s (≈11× faster)** |
| Missing readings detected | ≈0.2 % per sensor |
| Degradation ranking | machines **7 and 13** flagged `INSPECT` (vibration slope ≈0.28 /day vs ≈0.0003 for healthy machines) |

## What the analytics answer

| Query | Business question |
|---|---|
| `data_quality` | How many readings are missing, per sensor? |
| `daily_machine_kpis` | Daily average/max temperature and vibration per machine |
| `spikes` | Which readings are > 4σ from that machine's normal behaviour? |
| `degradation_ranking` | Which machines are wearing out? (linear-regression slope of daily vibration) |

## Engineering choices

- **Columnar Parquet + zstd** instead of CSV: smaller, typed, much faster to query.
- **Hive partitioning by month**: queries on one month only read that folder.
- **DuckDB**: in-process analytical SQL engine, handles larger-than-memory data on a laptop.
- **SQL-generated data**: 10 M rows created in ~4 s with realistic defects (wear trends, spikes, dropped readings).
- **Tested end-to-end**: the test regenerates a smaller dataset, checks no row is lost in conversion, and checks that exactly the two degrading machines are flagged.

## Run

```bash
pip install -r requirements.txt
python -m src.generate --rows 10000000
python -m src.convert
python -m src.analytics
python -m pytest -q
```
