# Data Quality Monitoring — data contracts, Great Expectations, CI quality gate

Business users stop trusting a dashboard the first time it shows a wrong number. This project puts a **quality gate** in front of the data: rules are written as simple YAML **data contracts**, executed with **Great Expectations**, tracked over time, and enforced in **CI** — bad data blocks the pipeline before anyone sees it.

Typical client request: *"Our reports were wrong last month and nobody noticed for two weeks. We need automatic checks and alerts."*

```
 contracts/*.yml ──► engine: contract → GX expectations ──► run on DuckDB / SQLite tables ──┬─► reports/quality_report.html  (scorecard)
 (owner, severity,    + custom checks GX lacks:              (warehouse from project 5,     ├─► reports/quality_results.json (machine-readable)
  rules per table)      freshness, volume anomaly            weather DB from project 2)     ├─► quality_history.db (trend, volume baseline)
                                                                                           └─► exit code 1 on CRITICAL → CI / orchestrator stops
```

## A contract (what consumers can rely on)

```yaml
dataset: fct_order_items
owner: data-engineering
source: {type: duckdb, path: ../projet5 - dbt-sales-warehouse/warehouse.duckdb, table: main_marts.fct_order_items}
volume_anomaly: true
checks:
  - {id: pk_unique,    type: unique,      column: order_item_id, severity: critical}
  - {id: status_known, type: in_set,      column: status, values: [completed, shipped, returned, cancelled], severity: critical}
  - {id: fk_customer,  type: foreign_key, column: customer_id, ref: main_marts.dim_customers.customer_id, severity: critical}
  - {id: avg_basket,   type: mean_between, column: quantity, min: 1, max: 6, severity: warning}
```

Check types: `not_null`, `unique`, `unique_combination`, `between`, `in_set`, `regex`, `row_count_between`, `mean_between`, `pair_greater_or_equal`, `foreign_key`, `freshness`, plus automatic **volume anomaly** (row count vs. the last 14 runs: % change and z-score). Each check is `critical` (blocks) or `warning` (reported). Non-engineers can read and edit the rules.

## Verified result

**Clean warehouse** (4 datasets, 10,011 order lines): **28/28 checks pass**, exit code 0.

**Broken copy** (`python -m src.demo_broken` injects the defects real pipelines produce):

| Injected defect | Caught by | Severity |
|---|---|---|
| Batch of 25 lines loaded twice | `pk_unique` — 50 bad rows | critical |
| Negative quantities from the source | `qty_range` — 5 rows, examples `-2` | critical |
| New status `on_hold` nobody announced | `status_known` — 8 rows | critical |
| Orders pointing to missing customers | `fk_customer` — 4,022 rows | critical |
| 40 % of customers lost in a bad load | `volume_anomaly` — 180 vs 300 rows, −40 % | warning |
| Malformed e-mails | `email_format` — 3 rows | warning |

→ score 72.7 %, **exit code 1: pipeline stopped**. Sample report: [`docs/sample_report_broken_data.html`](docs/sample_report_broken_data.html).

**14 tests**: every defect type caught with the right severity and nothing else flagged, failing examples reported, volume learning period + anomaly, freshness, contract validation, missing source (critical unless `optional`), CLI exit code as gate.

**CI**: on every push GitHub Actions runs `dbt build` (project 5) **then this gate on the fresh warehouse**, and uploads the HTML report as a build artifact.

## Run

```bash
pip install -r requirements.txt
python -m src.run                      # exit 1 if a critical check fails
python -m src.run --fail-on warning    # stricter
python -m src.demo_broken              # see it catch real defects
python -m pytest -q
```

## Stack

Python · Great Expectations 1.x · YAML data contracts · DuckDB · SQLite · pandas · GitHub Actions · pytest
