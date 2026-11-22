# Weather API Pipeline — incremental ingestion from a REST API to SQL

Pulls hourly weather for 5 cities from the free [Open-Meteo](https://open-meteo.com) API, validates every row, and loads it **incrementally** into a SQL database with daily aggregates.

Typical business use: any "pull data from an API every day into a database" job (CRM, e-commerce, ads, IoT, finance APIs).

```
Open-Meteo API ──► EXTRACT ──────► RAW JSON ──► FLATTEN ──► VALIDATE ──┬─► UPSERT hourly_weather ──► VIEW daily_weather
 (5 cities)       retries, 429/5xx  per run      columns→rows  bounds   │     (PK city+hour)
                  per-city isolation                                    └─► rejected_rows (quarantine + reason)
                                                                        runs table: inserted / updated / rejected
```

## Engineering features

| Feature | How |
|---|---|
| Incremental + idempotent | `UPSERT` on `(city, ts_utc)`; each run re-reads a 3-day overlap window so late API corrections are applied, never duplicated |
| Fault isolation | One failing or unreadable city is skipped and the run is marked `partial` with its name (exit code 2), never `success`; the run fails only if every city fails |
| Scope note | Rows include 2 days of forecast: they are overwritten by observed values on the next runs |
| Retries | Backoff on network errors, HTTP 429 and 5xx; a wrong request (4xx) is not retried |
| Quarantine instead of crash | Implausible values (e.g. 999 °C) go to `rejected_rows` with the reason — nothing silently lost |
| Run metrics | `runs` table records inserted / updated / rejected per run |
| SQL aggregation | `daily_weather` view (min/max/avg temperature, rain, max wind) computed in the database |
| Tested | 43 tests: API failures, bounds, quarantine, partial runs, idempotent upsert, late corrections, daily view |

## Run

```bash
pip install -r requirements.txt
python -m src.pipeline
python -m pytest -q
```

Verified: first run inserted **600 rows** (5 cities × 120 h); immediate re-run → **0 inserted, 600 updated**, still 600 rows.

```sql
SELECT * FROM daily_weather WHERE city = 'Paris' ORDER BY day;
```
