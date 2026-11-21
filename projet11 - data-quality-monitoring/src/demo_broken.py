"""Demo: copy the warehouse, break it the way real pipelines break, and show the gate catching it.

    python -m src.demo_broken      -> reports/broken/quality_report.html (exit code 1)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE = ROOT.parent / "projet5 - dbt-sales-warehouse" / "warehouse.duckdb"

DEFECTS = [
    # a bad deploy duplicated a batch of lines
    "INSERT INTO main_marts.fct_order_items SELECT * FROM main_marts.fct_order_items WHERE order_item_id <= 25",
    # a source system sent negative quantities
    "UPDATE main_marts.fct_order_items SET quantity = -2 WHERE order_item_id BETWEEN 100 AND 104",
    # orders for customers that do not exist (late-arriving dimension)
    "UPDATE main_marts.fct_order_items SET customer_id = 9999 WHERE order_item_id BETWEEN 200 AND 211",
    # a new status value nobody told the analysts about
    "UPDATE main_marts.fct_order_items SET status = 'on_hold' WHERE order_item_id BETWEEN 300 AND 307",
    # 40 % of the customer table lost in a bad incremental load
    "DELETE FROM main_marts.dim_customers WHERE customer_id % 5 IN (0, 1)",
    "UPDATE main_marts.dim_customers SET email = 'not-an-email' WHERE customer_id IN (1, 2, 3, 4, 5)",
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "warehouse.duckdb"
    shutil.copy(WAREHOUSE, db)
    with duckdb.connect(str(db)) as con:
        for sql in DEFECTS:
            con.execute(sql)
    contracts = tmp / "contracts"
    contracts.mkdir()
    for f in (ROOT / "contracts").glob("sales_*.yml"):
        text = f.read_text(encoding="utf-8").replace("../projet5 - dbt-sales-warehouse/warehouse.duckdb", db.as_posix())
        (contracts / f.name).write_text(text, encoding="utf-8")
    report_dir = ROOT / "reports" / "broken"
    shutil.rmtree(report_dir, ignore_errors=True)
    # reuse the clean run history so the volume check has a baseline
    report_dir.mkdir(parents=True)
    if (ROOT / "reports" / "quality_history.db").exists():
        shutil.copy(ROOT / "reports" / "quality_history.db", report_dir / "quality_history.db")
    return subprocess.call([sys.executable, "-m", "src.run", "--contracts", str(contracts),
                            "--reports", str(report_dir)], cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
