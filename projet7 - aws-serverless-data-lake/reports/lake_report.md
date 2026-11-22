# Data lake run report (local AWS mock)

- Files ingested: **15** (26 Parquet objects, 14 Glue partitions)
- Rows received: **4966**, rejected to quarantine: **93** (1.87 %)
- Parquet rows: 4859; unique orders after corrections: **4819**
- Idempotency: replaying a file -> 4819 orders before, 4819 after, same values: True
- Ingestion time: 5.4 s

## daily_revenue

| dt | orders | revenue_eur |
|---|---|---|
| 2026-11-07 | 326 | 67517.91 |
| 2026-11-08 | 384 | 98014.71 |
| 2026-11-09 | 345 | 85081.6 |
| 2026-11-10 | 403 | 88827.0 |
| 2026-11-11 | 366 | 86010.75 |
| 2026-11-12 | 299 | 71700.81 |
| 2026-11-13 | 311 | 78767.68 |
| 2026-11-14 | 248 | 64317.27 |
| 2026-11-15 | 402 | 92280.56 |
| 2026-11-16 | 334 | 76001.54 |
| 2026-11-17 | 385 | 84102.3 |
| 2026-11-18 | 307 | 66228.48 |
| 2026-11-19 | 379 | 90833.52 |
| 2026-11-20 | 330 | 79391.91 |

## by_country

| country | orders | revenue_eur |
|---|---|---|
| CA | 810 | 195810.34 |
| US | 799 | 191912.32 |
| MA | 803 | 190077.16 |
| DE | 782 | 187961.0 |
| FR | 833 | 182581.0 |
| GB | 792 | 180734.22 |

## top_products

| product | units | revenue_eur |
|---|---|---|
| Monitor 27in | 1254 | 312997.8 |
| Office chair | 1359 | 257496.25 |
| USB-C dock | 1348 | 174332.24 |
| Headset | 1173 | 116375.82 |
| Webcam | 1365 | 108104.29 |
