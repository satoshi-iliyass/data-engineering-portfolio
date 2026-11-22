# Price Tracker Pipeline — automated web scraping → SQL → Excel

A production-style data pipeline that **scrapes product prices every day, keeps the full price history in a database, checks data quality, and delivers a ready-to-use Excel report**.

Typical business use: competitor price monitoring, stock tracking, catalogue monitoring.

> Demo source: [books.toscrape.com](https://books.toscrape.com), a public sandbox site built for scraping practice (1,000 products, 50 pages).

## What it does

```
 Website ──► EXTRACT ──► RAW ZONE ──► TRANSFORM ──► QUALITY GATE ──► LOAD ──► REPORT
            (requests,   data/raw/    (parse HTML,   (row count,      (SQLite    (Excel:
             retries)    YYYY-MM-DD/   type, dedupe)  prices, dupes,   upsert,    summary,
                         *.html                       ratings)         history)   prices,
                                                          │                       changes)
                                                          └── fail ──► run logged as FAILED,
                                                                       nothing loaded
```

## Engineering features

| Feature | How |
|---|---|
| **Reliable extraction** | Retries on temporary failures only (a 404 is not retried), timeouts, polite delay between requests |
| **Raw zone** | Every page saved untouched per date → any day can be rebuilt without re-scraping (`--from-raw`) |
| **Idempotent loads** | `UPSERT` on `(product_id, scraped_on)` → re-running a day never duplicates data |
| **Price history** | One row per product per day; price changes computed with SQL window functions (`LAG`) |
| **Data quality gate** | Minimum row count, price range, duplicate keys, empty fields, valid ratings — bad data is blocked before loading |
| **Fault isolation** | Each step loads its own part: a broken report never loses the data (`--report` rebuilds it alone) |
| **No guessing** | A price that is not exactly like `£51.77` is refused, not interpreted |
| **Observability** | Log file + a `runs` table (date, status, rows, error message) |
| **Business output** | Formatted Excel report: summary, today's prices, price changes vs previous run |
| **Tested** | 57 tests: every function, plus the assembly with one broken part at a time |

## Quick start

```bash
pip install -r requirements.txt
python -m src.pipeline                      # scrape + load + report
python -m src.pipeline --from-raw 2026-11-21  # rebuild a day from raw HTML (no network)
python -m pytest -q                         # run tests
```

Output:
- `data/prices.db` — SQLite database (`products`, `prices`, `runs`)
- `reports/price_report_<date>.xlsx` — Excel report
- `logs/pipeline.log` — run logs

### Schedule it daily (Windows Task Scheduler)

```bash
schtasks /Create /SC DAILY /ST 08:00 /TN "PriceTracker" /TR "<path>\run_daily.bat"
```

## Data model

```
products (product_id PK, title, rating)
prices   (product_id FK, scraped_on, price_gbp, in_stock)   PK = (product_id, scraped_on)
runs     (run_id, run_date, status, rows, message, started_at)
```

## Results (first run)

- 50 pages scraped, **1,000 products** loaded, quality checks passed
- Re-run on the same day: still **1,000 rows** (idempotent)
- Prices: min £10.00 · max £59.99 · average £35.07

## Adapting it to a client's site

Only `src/transform.py` (HTML selectors) and `src/config.py` (URL, thresholds) change. Extraction, quality gate, loading, history and reporting stay the same.

## Project structure

```
src/
  config.py     settings and thresholds
  extract.py    HTTP fetch with retries, raw storage
  lire_prix.py  one price -> number, or refusal
  transform.py  HTML parsing, typing, dedupe
  quality.py    data quality gate
  load.py       SQLite schema + idempotent upsert + run log
  report.py     Excel report (openpyxl)
  pipeline.py   orchestrator / CLI
tests/          pytest unit tests
```
