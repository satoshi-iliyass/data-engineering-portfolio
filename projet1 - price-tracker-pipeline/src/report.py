"""Report: build the Excel file a non-technical client opens every morning."""
import sqlite3
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")

LATEST_SQL = """
SELECT p.title, pr.price_gbp, CASE pr.in_stock WHEN 1 THEN 'yes' ELSE 'no' END, p.rating
FROM prices pr JOIN products p USING (product_id)
WHERE pr.scraped_on = ?
ORDER BY pr.price_gbp DESC
"""

# Price change vs the previous scrape date of each product
CHANGES_SQL = """
WITH ranked AS (
    SELECT product_id, scraped_on, price_gbp,
           LAG(price_gbp) OVER (PARTITION BY product_id ORDER BY scraped_on) AS prev_price
    FROM prices
)
SELECT p.title, r.prev_price, r.price_gbp,
       ROUND(r.price_gbp - r.prev_price, 2),
       ROUND(100.0 * (r.price_gbp - r.prev_price) / r.prev_price, 1)
FROM ranked r JOIN products p USING (product_id)
WHERE r.scraped_on = ? AND r.prev_price IS NOT NULL AND r.price_gbp <> r.prev_price
ORDER BY ABS(r.price_gbp - r.prev_price) DESC
"""

SUMMARY_SQL = """
SELECT COUNT(*), ROUND(AVG(price_gbp), 2), MIN(price_gbp), MAX(price_gbp), SUM(in_stock)
FROM prices WHERE scraped_on = ?
"""


def _sheet(wb, title, headers, rows):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
    for row in rows:
        ws.append(list(row))
    for i, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows][:200]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(width, 60)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def build(conn: sqlite3.Connection, run_date: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    n, avg, lo, hi, stock = conn.execute(SUMMARY_SQL, (run_date,)).fetchone()
    changes = conn.execute(CHANGES_SQL, (run_date,)).fetchall()
    _sheet(wb, "Summary", ["Metric", "Value"], [
        ("Date", run_date), ("Products tracked", n), ("Average price (GBP)", avg),
        ("Min price (GBP)", lo), ("Max price (GBP)", hi), ("In stock", stock),
        ("Price changes vs previous run", len(changes)),
    ])
    _sheet(wb, "Prices today", ["Title", "Price (GBP)", "In stock", "Rating"],
           conn.execute(LATEST_SQL, (run_date,)).fetchall())
    _sheet(wb, "Price changes", ["Title", "Previous", "Now", "Change", "Change %"], changes)
    path = out_dir / f"price_report_{run_date}.xlsx"
    wb.save(path)
    return path
