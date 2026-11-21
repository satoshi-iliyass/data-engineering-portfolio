"""Data quality checks. A failed check stops the load: bad data never reaches the report."""
from . import config


class DataQualityError(Exception):
    pass


def check(rows: list[dict]) -> list[str]:
    """Return a list of problems (empty list = data is OK)."""
    problems = []
    if len(rows) < config.MIN_ROWS:
        problems.append(f"only {len(rows)} rows (< {config.MIN_ROWS}): site layout may have changed")
    keys = [(r["product_id"], r["scraped_on"]) for r in rows]
    if len(keys) != len(set(keys)):
        problems.append("duplicate (product_id, scraped_on) keys")
    lo, hi = config.PRICE_RANGE
    bad_price = [r["product_id"] for r in rows if not lo <= r["price_gbp"] <= hi]
    if bad_price:
        problems.append(f"{len(bad_price)} prices outside {config.PRICE_RANGE}, e.g. {bad_price[:3]}")
    missing = [r["product_id"] for r in rows if not r["title"] or not r["product_id"]]
    if missing:
        problems.append(f"{len(missing)} rows with empty id/title")
    bad_rating = [r["product_id"] for r in rows if r["rating"] not in (1, 2, 3, 4, 5)]
    if bad_rating:
        problems.append(f"{len(bad_rating)} rows with invalid rating")
    return problems


def assert_ok(rows: list[dict]) -> None:
    problems = check(rows)
    if problems:
        raise DataQualityError("; ".join(problems))
