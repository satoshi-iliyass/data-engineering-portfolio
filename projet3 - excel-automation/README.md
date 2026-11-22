# Excel Automation — merge & clean dozens of messy Excel files in one command

Clients often receive monthly Excel exports from several branches/suppliers, each with **different column names, date formats, decimal commas, title rows, blank lines, duplicates and "TOTAL" rows**. Someone spends hours copy-pasting them every month.

This tool turns a folder of messy `.xlsx` files into **one clean, consolidated, formatted Excel report** in seconds.

```
input/*.xlsx  ──►  detect header row  ──►  map column aliases  ──►  parse dates & numbers  ──►  validate
(any layout)       (skips titles)          (Qty/quantity/QUANTITE)   (3 date formats, 1 200,5)    │
                                                                                                 ├─► clean rows ──► dedupe ──► revenue, month
                                                                                                 └─► rejected rows (with reason)
                                                                                     ──► output/consolidated_sales.xlsx
```

## What the report contains

| Sheet | Content |
|---|---|
| Summary | files processed, clean / rejected rows, files skipped, total revenue, period |
| Revenue by month | pivot month × branch + bar chart |
| By product | units, revenue, number of orders |
| Clean data | the standardised table (filterable) |
| Rejected rows | every row that could not be trusted, with the reason |
| Errors | files that could not be read (the batch continues) |

## Engineering choices

- **Header detection**: finds the real header even with title rows above it.
- **Column alias map**: new branch layout = one line in `COLUMN_ALIASES`.
- **No silent guessing**: an unreadable price or impossible date is **rejected with a reason**, never filled with a made-up value.
- **Fault tolerant**: one corrupt file is reported in "Errors", the others are still processed.
- **Tested**: 44 tests, including real Excel date cells, ambiguous numbers (`1,299`), missing products, infinite or decimal quantities, every sheet of the report.

## Run

```bash
pip install -r requirements.txt
python -m src.make_sample_data          # creates 9 realistic messy files in input/
python -m src.main                      # -> output/consolidated_sales.xlsx
python -m src.main --input "C:/client/files"
python -m pytest -q
```

Demo result: 9 files from 3 branches → **549 clean rows**, 45 rows set aside with their reason (27 duplicate lines, 9 bad prices, 9 bad dates), total revenue computed.
