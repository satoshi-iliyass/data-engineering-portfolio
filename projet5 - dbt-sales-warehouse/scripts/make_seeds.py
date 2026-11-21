"""Generate raw e-commerce source tables as CSV seeds (with realistic dirt).

    python scripts/make_seeds.py
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEEDS = Path(__file__).resolve().parent.parent / "seeds"
rng = random.Random(7)

COUNTRIES = ["FR", "fr", "France", "MA", "Morocco", "CA", "DE"]          # inconsistent country codes
CATEGORIES = ["Bearings", "Motors", "Hydraulics", "Fasteners"]
STATUSES = ["completed", "completed", "completed", "shipped", "cancelled", "returned"]


def write(name, header, rows):
    SEEDS.mkdir(exist_ok=True)
    with open(SEEDS / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


customers = []
for i in range(1, 301):
    email = f"Customer{i}@Example.com " if i % 10 == 0 else f"customer{i}@example.com"   # case/space dirt
    customers.append([i, f"Customer {i}", email, rng.choice(COUNTRIES),
                      (date(2024, 1, 1) + timedelta(days=rng.randint(0, 700))).isoformat()])
write("raw_customers", ["id", "name", "email", "country", "signup_date"], customers)

products = [[i, f"{rng.choice(CATEGORIES)[:-1]} model {i}", rng.choice(CATEGORIES),
             round(rng.uniform(5, 600), 2)] for i in range(1, 61)]
write("raw_products", ["id", "name", "category", "list_price"], products)

orders, items, item_id = [], [], 1
for oid in range(1, 4001):
    d = date(2025, 1, 1) + timedelta(days=rng.randint(0, 670))
    orders.append([oid, rng.randint(1, 300), d.isoformat(), rng.choice(STATUSES)])
    for _ in range(rng.randint(1, 4)):
        p = rng.choice(products)
        price = round(p[3] * rng.uniform(0.85, 1.0), 2)                  # discounts
        items.append([item_id, oid, p[0], rng.randint(1, 10), price])
        item_id += 1
orders.append(orders[10])                                                 # duplicated order row in source
write("raw_orders", ["id", "customer_id", "order_date", "status"], orders)
write("raw_order_items", ["id", "order_id", "product_id", "quantity", "unit_price"], items)
print(f"seeds: {len(customers)} customers, {len(products)} products, {len(orders)} orders, {len(items)} items")
