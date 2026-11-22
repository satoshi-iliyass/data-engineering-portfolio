# AWS Serverless Data Lake — S3 + Lambda + Glue + SQL on Parquet

Event-driven ingestion on AWS: each time a shop drops an order export in S3, a Lambda validates it, converts it to typed **Parquet partitioned by day**, quarantines bad rows with the reason, and registers the partition in the **Glue catalog**, so it can be queried with SQL right away (Athena-style).

Typical client request: *"Our app exports JSON files to S3 every day. We want clean, queryable data and dashboards without running servers."*

```
 shop export            S3 raw bucket                     Lambda (Python)                     S3 data lake bucket                 SQL
 .jsonl / .jsonl.gz ──► incoming/orders/ ──ObjectCreated──► validate · dedupe · FX→EUR ──┬──► curated/orders/dt=YYYY-MM-DD/*.parquet ──► Glue table shop.orders ──► Athena / DuckDB
                        (immutable,                          typed schema (pyarrow)       ├──► quarantine/orders/<file>.jsonl  (reason per line)        view orders_latest
                         lifecycle → IA → Glacier)                                        └──► _manifests/orders/<file>.json   (row counts)             (corrections applied)
```

## Engineering features

| Feature | How |
|---|---|
| Serverless, event-driven | S3 `ObjectCreated` → Lambda, no server to maintain, pay per file |
| Columnar + partitioned | Snappy Parquet in `dt=` folders: queries scan only the days they need |
| Catalog | Partitions added to Glue at write time (no slow crawler / `MSCK REPAIR`) |
| Idempotent | Output keys derived from the full source path; a replay rewrites its own files, removes the ones it no longer produces, and changes no value |
| Late corrections | The file that **arrived** last wins (`ROW_NUMBER()` on arrival time), so replaying an old file never cancels a correction |
| Quarantine, not crash | Bad JSON, missing id, negative or decimal quantity, infinite price, unknown currency, bad date → quarantine file with reason |
| Security | Buckets encrypted (SSE), public access blocked, least-privilege IAM role for the Lambda |
| Cost | Raw files move to Infrequent Access after 30 days, Glacier after 90 |
| Infrastructure as code | `src/infra.py` (boto3) creates buckets, Glue DB/table, IAM role, Lambda, trigger — re-runnable |
| Portable config | Everything through env vars: same code on real AWS and on the local mock |

## Run without an AWS account

The whole stack runs on [moto](https://github.com/getmoto/moto), a local AWS mock (S3, Glue, IAM, Lambda APIs):

```bash
pip install -r requirements.txt
python -m src.run_local      # deploy infra, ingest 15 files, replay one, run KPIs -> reports/lake_report.md
python -m pytest -q
```

Deploy to real AWS (credentials configured): `python -m src.infra`, then upload files to `s3://shop-raw-landing/incoming/orders/`.

## Verified result (local mock, 2026-11-21)

- **15 files** ingested (14 daily exports, some gzipped, + 1 corrections file) in **5.4 s**
- **4,966 rows** received → **93 quarantined (1.87 %)** with reason, 26 Parquet objects, 14 Glue partitions
- **40 corrected orders** applied: 4,859 Parquet rows → **4,819 unique orders**, no double counting
- Replaying a file: 4,819 orders before, 4,819 after, **every value identical**
- KPIs by SQL straight on S3: daily revenue, revenue by country (EUR after FX), top products

**35 tests**: validation rules, currency conversion, partitioning + typing + quarantine, Glue partitions registered once, idempotent replay that also clears an old quarantine, duplicate lines, ignored prefixes, infra security + re-run, and an end-to-end test over a real HTTP S3 endpoint with SQL and corrections.

## Limits

Verified on a local AWS mock (moto). Not yet deployed on a real AWS account: the Lambda runtime, the pyarrow layer ARN and Athena itself are untested.

## Stack

Python · AWS S3 · Lambda · Glue Data Catalog · IAM · Parquet (pyarrow) · DuckDB (Athena-compatible SQL) · moto · pytest
