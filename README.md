# Data Engineering Portfolio

End-to-end projects covering what clients most often need: collecting data (scraping, APIs, messy files), cleaning and validating it, storing it with history, and delivering it as SQL tables, Excel reports or KPIs.

| # | Project | What it shows | Stack | Verified result |
|---|---|---|---|---|
| 1 | [Price Tracker Pipeline](projet1%20-%20price-tracker-pipeline) | Daily web scraping → SQL history → Excel report | Python, requests, BeautifulSoup, SQLite, openpyxl | 1,000 products/run, idempotent re-runs, 9 tests |
| 2 | [Weather API Pipeline](projet2%20-%20weather-api-pipeline) | Incremental REST-API ingestion, quarantine of bad rows | Python, REST API, SQLite, SQL views | 600 rows, re-run = 0 duplicates, 5 tests |
| 3 | [Excel Automation](projet3%20-%20excel-automation) | Merge & clean dozens of inconsistent Excel files into one report | pandas, openpyxl | 9 files → 549 clean rows, 18 rejected with reason, 10 tests |
| 4 | [Sensor Analytics](projet4%20-%20sensor-analytics-duckdb) | 10 M rows on a laptop, Parquet, anomaly & wear detection | DuckDB, Parquet, SQL | CSV 387 MB → Parquet 23 MB, queries ~11× faster |
| 5 | [Sales Data Warehouse](projet5%20-%20dbt-sales-warehouse) | Staging → star schema → KPI mart, fully tested | dbt, DuckDB, SQL | 29/29 dbt tests pass, revenue reconciles |
| 6 | [Pipeline Orchestration](projet6%20-%20pipeline-orchestration) | Scheduled platform: retries, dependencies, data-freshness SLA, Slack alerts, CI | Prefect 3, GitHub Actions, pytest | 3 pipelines orchestrated, failures retried/alerted, 6 tests |
| 7 | [AWS Serverless Data Lake](projet7%20-%20aws-serverless-data-lake) | S3 → Lambda → partitioned Parquet + Glue catalog, SQL on S3, IaC, least-privilege IAM | AWS S3, Lambda, Glue, IAM, pyarrow, DuckDB, moto | 4,966 rows, 1.9 % quarantined, corrections applied, 15 tests |
| 11 | [Data Quality Monitoring](projet11%20-%20data-quality-monitoring) | YAML data contracts → Great Expectations, volume anomaly, freshness, CI quality gate after dbt | Great Expectations, DuckDB, YAML, GitHub Actions | 28/28 on clean data; 6/6 injected defects caught, pipeline blocked, 14 tests |

## Common engineering principles

- **Idempotent loads** — re-running never creates duplicates
- **Raw data kept** — any run can be rebuilt without re-downloading
- **Quality gates / quarantine** — bad data is blocked or isolated with a reason, never silently "fixed"
- **Observability** — logs and run tables (status, row counts, errors)
- **Tested** — pytest and dbt tests on every project, run automatically by GitHub Actions on each push
- **Documented** — each folder has its own README with architecture, results and run commands
