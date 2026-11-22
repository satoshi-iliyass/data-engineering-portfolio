"""Transform: parse raw HTML into clean, typed rows."""
from datetime import date

from bs4 import BeautifulSoup

from .lire_prix import lire_prix

RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def parse_page(html: str, run_date: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select("article.product_pod"):
        link = card.select_one("h3 > a")
        rating_cls = [c for c in card.select_one("p.star-rating")["class"] if c != "star-rating"]
        rows.append({
            "product_id": link["href"].split("/")[0],       # stable slug, e.g. a-light-in-the-attic_1000
            "title": link["title"].strip(),
            "price_gbp": lire_prix(card.select_one("p.price_color").get_text()),
            "in_stock": "In stock" in card.select_one("p.availability").get_text(),
            "rating": RATINGS.get(rating_cls[0]) if rating_cls else None,
            "scraped_on": run_date.isoformat(),
        })
    return rows


def dedupe(rows: list[dict]) -> list[dict]:
    """Keep one row per (product_id, scraped_on)."""
    seen, out = set(), []
    for r in rows:
        key = (r["product_id"], r["scraped_on"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
