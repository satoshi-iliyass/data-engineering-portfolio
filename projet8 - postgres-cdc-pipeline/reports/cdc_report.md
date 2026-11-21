# CDC run report (PostgreSQL 17, logical decoding)

- Snapshot: 200 customers, 1000 orders (slot created at LSN 0/5ACE368)
- Source activity over 6 hours: 720 new orders, 1020 order updates, 90 hard deletes, 60 customer changes
- WAL events captured: **1890** (1080 updates, 90 deletes), applied in 1.6 s total (1152 events/s)
- Crash test (hour 3): after restart 315 events re-read, 0 re-applied -> no duplicates
- SCD2: 260 versions for 200 customers (max 4 versions for one customer)
- Deleted orders kept as soft-deletes: 90

## Reconciliation against the source (row by row)

| Method | Source rows | Target rows | Missing | Ghost rows (deleted in source) | Different | Source revenue | Target revenue | Match |
|---|---|---|---|---|---|---|---|---|
| CDC (WAL) | 1630 | 1630 | 0 | 0 | 0 | 740,539.41 | 740,539.41 | YES |
| Watermark updated_at | 1630 | 1697 | 0 | 67 | 0 | 740,539.41 | 768,641.37 | NO |

## Example customer history (SCD type 2)

| id | segment | email | valid_from | valid_to | is_current |
|---|---|---|---|---|---|
| 89 | bronze | c89@mail.com | 2026-11-22 00:14:43 | 2026-11-22 00:14:43 | False |
| 89 | silver | c89.39@mail.com | 2026-11-22 00:14:43 | 2026-11-22 00:14:45 | False |
| 89 | gold | c89.93@mail.com | 2026-11-22 00:14:45 | 2026-11-22 00:14:46 | False |
| 89 | bronze | c89.24@mail.com | 2026-11-22 00:14:46 |  | True |
