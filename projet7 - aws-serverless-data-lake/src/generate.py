"""Synthetic e-commerce order exports (what a shop API would drop in S3 every day), with realistic defects."""
import gzip
import json
import random
from datetime import datetime, timedelta, timezone

PRODUCTS = {"Desk lamp": 39.0, "Office chair": 189.0, "Monitor 27in": 249.0, "Keyboard": 59.0,
            "Mouse": 25.0, "Webcam": 79.0, "Headset": 99.0, "USB-C dock": 129.0}
COUNTRIES = {"FR": "EUR", "DE": "EUR", "MA": "MAD", "GB": "GBP", "US": "USD", "CA": "USD"}
PRICE_FACTOR = {"EUR": 1.0, "USD": 1.09, "GBP": 0.86, "MAD": 10.9}


def make_day(day: datetime, rng: random.Random, start_id: int) -> list[dict]:
    rows = []
    for i in range(rng.randint(250, 450)):
        country = rng.choice(list(COUNTRIES))
        cur = COUNTRIES[country]
        product = rng.choice(list(PRODUCTS))
        ts = day + timedelta(seconds=rng.randint(0, 86399))
        rows.append({"order_id": f"O{start_id + i:07d}", "customer_id": f"C{rng.randint(1, 4000):05d}",
                     "country": country, "product": product, "quantity": rng.choice([1, 1, 1, 2, 2, 3, 5]),
                     "unit_price": round(PRODUCTS[product] * PRICE_FACTOR[cur], 2), "currency": cur,
                     "created_at": ts.isoformat().replace("+00:00", "Z")})
    # defects seen in real exports (~2 %)
    for r in rng.sample(rows, k=max(1, len(rows) // 50)):
        kind = rng.choice(["no_id", "neg_qty", "bad_cur", "bad_date", "text_price"])
        if kind == "no_id":
            r["order_id"] = None
        elif kind == "neg_qty":
            r["quantity"] = -1
        elif kind == "bad_cur":
            r["currency"] = "XYZ"
        elif kind == "bad_date":
            r["created_at"] = "31/02/2026"
        else:
            r["unit_price"] = "N/A"
    rows.append(dict(rng.choice(rows)))   # duplicated line
    return rows


def to_bytes(rows: list[dict], gz: bool) -> bytes:
    body = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
    return gzip.compress(body) if gz else body


def generate(days: int = 14, seed: int = 7, end: datetime | None = None) -> list[tuple[str, bytes]]:
    """Returns [(file_name, bytes)]: one file per day + one late 'corrections' file."""
    rng = random.Random(seed)
    end = end or datetime(2026, 11, 20, tzinfo=timezone.utc)
    files, all_rows, next_id = [], [], 1
    for d in range(days):
        day = end - timedelta(days=days - 1 - d)
        rows = make_day(day, rng, next_id)
        next_id += len(rows)
        all_rows += [r for r in rows if r.get("order_id")]
        gz = d % 3 == 0                                   # the source sometimes sends gzip
        files.append((f"orders_{day:%Y%m%d}.jsonl" + (".gz" if gz else ""), to_bytes(rows, gz)))
    # corrections: 40 old orders re-sent with a new quantity, + 1 invalid line
    corr = [dict(r, quantity=r["quantity"] + 1) for r in rng.sample(
        [r for r in all_rows if isinstance(r["quantity"], int) and r["quantity"] > 0 and r["currency"] != "XYZ"
         and r["created_at"] != "31/02/2026" and r["unit_price"] != "N/A"], 40)]
    corr.append({"order_id": "O9999999", "quantity": "two"})
    files.append((f"orders_{end:%Y%m%d}_corrections.jsonl", to_bytes(corr, False)))
    return files
