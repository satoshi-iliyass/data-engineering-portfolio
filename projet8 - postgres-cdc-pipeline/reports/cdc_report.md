# CDC run report (PostgreSQL 17, logical decoding)

- Snapshot: 200 customers, 1000 orders (slot created at LSN 0/42187848)
- Source activity over 6 hours: 720 new orders, 1020 order updates, 90 hard deletes, 60 customer changes
- WAL events captured: **1890** (1080 updates, 90 deletes), applied in 1.4 s total (1327 events/s)
- Crash test (hour 3): after restart 315 events re-read, 0 re-applied -> no duplicates
- SCD2: 260 versions for 200 customers (max 4 versions for one customer)
- Deleted orders kept as soft-deletes: 90

## Reconciliation against the source (row by row)

| Method | Source rows | Target rows | Missing | Ghost rows (deleted in source) | Different | Source revenue | Target revenue | Match |
|---|---|---|---|---|---|---|---|---|
| CDC (WAL) | 1630 | 1630 | 0 | 0 | 0 | 742,300.68 | 742,300.68 | YES |
| Watermark updated_at | 1630 | 1695 | 0 | 65 | 0 | 742,300.68 | 768,277.95 | NO |

Customers (every column): 200 rows in the source, 0 different, match: YES

## Example customer history (SCD type 2)

| id | segment | email | valid_from | valid_to | is_current |
|---|---|---|---|---|---|
| 89 | bronze | c89@mail.com | 2026-11-22 14:47:21 | 2026-11-22 14:47:22 | False |
| 89 | silver | c89.39@mail.com | 2026-11-22 14:47:22 | 2026-11-22 14:47:23 | False |
| 89 | gold | c89.93@mail.com | 2026-11-22 14:47:23 | 2026-11-22 14:47:24 | False |
| 89 | bronze | c89.24@mail.com | 2026-11-22 14:47:24 |  | True |
