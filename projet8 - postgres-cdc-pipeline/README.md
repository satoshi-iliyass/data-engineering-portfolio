# PostgreSQL CDC Pipeline — log-based Change Data Capture, SCD2, exactly-once

Replicates a live application database (PostgreSQL) into an analytics copy by reading the **write-ahead log** (logical decoding) — the technique behind Debezium / Fivetran / Airbyte CDC — instead of the usual `WHERE updated_at > last_run`.

Typical client request: *"Our reporting copy of the production database drifts: deleted orders still show up, numbers don't match the app."*

```
 application DB (OLTP)                                    analytics DB
 customers, orders ── WAL ──► replication slot ──► CDC worker ──► raw.cdc_events         append-only log of every change (audit, replay)
 INSERT/UPDATE/DELETE        (test_decoding,     parse, batch,  ├► core.orders/customers  current state, deletes = soft-delete
                              logical)           one target tx  ├► core.customers_history SCD type 2 (one row per version)
                                   ▲                            └► ops.cdc_offsets        last applied WAL position
                                   └──── acknowledge (advance slot) ONLY after the target commit
```

## Why it matters — measured, not claimed

Same source, same 6 hours of activity (720 new orders, 1,020 updates, **90 hard deletes**, 60 customer changes), both copies reconciled **row by row** against the source:

| Method | Source rows | Target rows | Ghost rows (deleted in source) | Source revenue | Target revenue | Match |
|---|---|---|---|---|---|---|
| **CDC (WAL)** | 1,630 | 1,630 | **0** | 739,685.60 | 739,685.60 | **YES** |
| Watermark `updated_at` | 1,630 | 1,691 | **61** | 739,685.60 | 766,479.47 | NO — revenue overstated by **3.6 %** |

The watermark method cannot see a row that no longer exists, so every hard delete stays in the report forever.

## Engineering features

| Feature | How |
|---|---|
| No missed change | Slot created **before** the initial snapshot: nothing committed in between can be lost |
| Deletes captured | WAL contains DELETEs; target keeps them as `_deleted = true` (history preserved) |
| Exactly-once effect | Target batch committed in one transaction with its offset; slot advanced only afterwards. Crash test: 315 events re-read after restart, **0 re-applied** |
| Ordering guard | Each row stores the WAL position (`_lsn`) of its last change; older events can never overwrite newer data |
| Only committed data | Rolled-back transactions never appear in the WAL stream |
| SCD type 2 | Customer segment / e-mail / country changes create a new version with `valid_from / valid_to / is_current` (260 versions for 200 customers) |
| Audit log | `raw.cdc_events`: every change with transaction id and commit time, replayable |
| Monitoring | `pending_changes()` = replication lag in number of changes |
| Low impact on production | No queries scanning the big tables; reads the log the database writes anyway |

Throughput on a laptop: **~1,150 events/s** (batched, single worker).

## Run

Needs PostgreSQL ≥ 13 with `wal_level = logical` (settings via env vars `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`).

```bash
pip install -r requirements.txt
python -m src.run_demo        # builds both databases, 6 simulated hours, crash test, reconciliation -> reports/cdc_report.md
python -m pytest -q
```

**6 integration tests on a real PostgreSQL**: parser (quotes, NULLs, types), insert/update/delete replicated, crash between commit and acknowledgement → no duplicates, rolled-back transactions ignored, SCD2 versions only on tracked changes, watermark misses deletes while CDC does not.
**CI**: GitHub Actions starts PostgreSQL 17 with `wal_level=logical` and runs the tests on every push.

## Stack

PostgreSQL 17 (logical replication slots, `test_decoding`, `pg_lsn`) · Python · psycopg 3 · SQL (upserts, SCD2, window functions) · GitHub Actions · pytest
