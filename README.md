# Data Engineering Portfolio

Five end-to-end projects covering what clients most often need: collecting data (scraping, APIs, messy files), cleaning and validating it, storing it with history, and delivering it as SQL tables, Excel reports or KPIs.

| # | Project | What it shows | Stack | Verified result |
|---|---|---|---|---|
| 1 | [Price Tracker Pipeline](projet1%20-%20price-tracker-pipeline) | Daily web scraping → SQL history → Excel report | Python, requests, BeautifulSoup, SQLite, openpyxl | 1,000 products/run, idempotent re-runs, 9 tests |
| 2 | [Weather API Pipeline](projet2%20-%20weather-api-pipeline) | Incremental REST-API ingestion, quarantine of bad rows | Python, REST API, SQLite, SQL views | 600 rows, re-run = 0 duplicates, 5 tests |
| 3 | [Excel Automation](projet3%20-%20excel-automation) | Merge & clean dozens of inconsistent Excel files into one report | pandas, openpyxl | 9 files → 549 clean rows, 18 rejected with reason, 10 tests |
| 4 | [Sensor Analytics](projet4%20-%20sensor-analytics-duckdb) | 10 M rows on a laptop, Parquet, anomaly & wear detection | DuckDB, Parquet, SQL | CSV 387 MB → Parquet 23 MB, queries ~11× faster |
| 5 | [Sales Data Warehouse](projet5%20-%20dbt-sales-warehouse) | Staging → star schema → KPI mart, fully tested | dbt, DuckDB, SQL | 29/29 dbt tests pass, revenue reconciles |

## Common engineering principles

- **Idempotent loads** — re-running never creates duplicates
- **Raw data kept** — any run can be rebuilt without re-downloading
- **Quality gates / quarantine** — bad data is blocked or isolated with a reason, never silently "fixed"
- **Observability** — logs and run tables (status, row counts, errors)
- **Tested** — pytest and dbt tests on every project
- **Documented** — each folder has its own README with architecture, results and run commands
