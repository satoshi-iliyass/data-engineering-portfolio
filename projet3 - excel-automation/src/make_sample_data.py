"""Generate realistic MESSY monthly sales files, like the ones clients send.

Each branch exports differently: other column names, other date formats, comma decimals,
extra blank rows, duplicated lines, typos in product names, a total row at the bottom.

    python -m src.make_sample_data
"""
import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "input"

PRODUCTS = ["Bearing 6204", "Hydraulic pump", "Gear motor", "Steel bracket", "Seal kit"]
PRICES = {"Bearing 6204": 4.5, "Hydraulic pump": 320.0, "Gear motor": 185.0, "Steel bracket": 12.0, "Seal kit": 9.9}

# Each branch has its own export "style"
BRANCHES = {
    "paris":     {"cols": ["Date", "Product", "Qty", "Unit price"], "datefmt": "%d/%m/%Y", "comma": True},
    "lyon":      {"cols": ["order_date", "item", "quantity", "price"], "datefmt": "%Y-%m-%d", "comma": False},
    "marseille": {"cols": ["DATE", "PRODUIT", "QUANTITE", "PRIX UNITAIRE"], "datefmt": "%d-%m-%Y", "comma": True},
}


def messy_name(name: str, rng: random.Random) -> str:
    variants = [name, name.upper(), name.lower(), f"  {name} ", name.replace(" ", "  ")]
    return rng.choice(variants)


def make_file(branch: str, style: dict, month: date, rng: random.Random) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append([f"Sales export - {branch.title()} - {month:%B %Y}"])   # title row above the header
    ws.append([])
    ws.append(style["cols"])
    rows = []
    for _ in range(rng.randint(40, 70)):
        d = month + timedelta(days=rng.randint(0, 27))
        prod = rng.choice(PRODUCTS)
        qty = rng.randint(1, 20)
        price = PRICES[prod] * rng.uniform(0.95, 1.05)
        price_s = f"{price:.2f}".replace(".", ",") if style["comma"] else round(price, 2)
        rows.append([d.strftime(style["datefmt"]), messy_name(prod, rng), qty, price_s])
    rows += rng.sample(rows, 3)                     # duplicated lines
    rows.append([rows[0][0], rows[0][1], 2, "n/a"])  # missing price -> must be rejected, not guessed
    rows.append(["31/02/2026", rows[1][1], 1, rows[1][3]])  # impossible date
    rows.insert(rng.randint(0, len(rows)), [None, None, None, None])  # blank row in the middle
    for r in rows:
        ws.append(r)
    ws.append(["TOTAL", None, sum(r[2] or 0 for r in rows), None])   # footer that must be ignored
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = INPUT_DIR / f"{branch}_{month:%Y_%m}.xlsx"
    wb.save(path)
    return path


def main(seed: int = 42) -> None:
    rng = random.Random(seed)
    files = [make_file(b, s, date(2026, m, 1), rng) for b, s in BRANCHES.items() for m in (8, 9, 10)]
    print(f"{len(files)} messy files written to {INPUT_DIR}")


if __name__ == "__main__":
    main()
