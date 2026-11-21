# Sales Data Warehouse — dbt + DuckDB (staging → star schema → KPI mart, fully tested)

A modern analytics-engineering project: raw e-commerce exports are turned into a **tested star schema** and **business-ready KPI tables** using **dbt** on **DuckDB**.

```
seeds (raw CSV)            staging (views)             marts (tables)
───────────────            ───────────────             ──────────────
raw_customers   ──►  stg_customers   (emails, country codes)  ──►  dim_customers  (lifetime value, segment)
raw_products    ──►  stg_products                             ──►  dim_products
raw_orders      ──►  stg_orders      (dedupe source dupes)    ──┐
raw_order_items ──►  stg_order_items (line_amount)            ──┴► fct_order_items (grain: 1 order line)
                                                                         │
                                                                         ▼
                                                               mart_monthly_revenue (orders, units,
                                                               revenue, discounts, AOV per category)
```

## Data problems handled in staging

| Problem in source | Fix |
|---|---|
| `FR`, `fr`, `France`, `Morocco`… | one ISO country code |
| `Customer10@Example.com ` | lowercase + trimmed email |
| order exported twice | `row_number()` dedupe on `order_id` (4,001 → 4,000) |
| prices as text | typed `decimal` |

## Tests (29 checks, all passing)

- `unique` / `not_null` on every primary key
- `accepted_values` on order status, country code, customer segment
- `relationships`: every fact row points to an existing customer and product
- **Custom tests**: no negative amounts; **mart revenue reconciles exactly with the fact table** (no rows lost or doubled)

## Run

```bash
pip install dbt-duckdb
python scripts/make_seeds.py
set DBT_PROFILES_DIR=.        # (PowerShell: $env:DBT_PROFILES_DIR=".")
dbt build                     # seeds + models + tests, in dependency order
dbt docs generate && dbt docs serve   # lineage graph + documentation site
```

Result: 300 customers, 60 products, 4,000 orders, ~10,000 order lines → 23 months of KPIs; customers segmented bronze 99 / silver 128 / gold 73.

## Why dbt

SQL transformations written like software: version-controlled, modular (`ref()`), tested, documented, with an automatic lineage graph. The same project runs on Snowflake/BigQuery by changing only `profiles.yml`.
