from datetime import date

import pytest

from src import load, quality, transform

HTML = """
<ol>
<article class="product_pod">
  <p class="star-rating Three"></p>
  <h3><a href="book-a_1/index.html" title="Book A">Book A</a></h3>
  <p class="price_color">Â£51.77</p>
  <p class="instock availability">In stock</p>
</article>
<article class="product_pod">
  <p class="star-rating Five"></p>
  <h3><a href="book-b_2/index.html" title="Book B">Book B</a></h3>
  <p class="price_color">£10.00</p>
  <p class="availability">Out of stock</p>
</article>
</ol>
<ul class="pager"><li class="next"><a href="page-2.html">next</a></li></ul>
"""
D1, D2 = date(2026, 1, 1), date(2026, 1, 2)


def test_parse_page():
    rows = transform.parse_page(HTML, D1)
    assert len(rows) == 2
    assert rows[0] == {"product_id": "book-a_1", "title": "Book A", "price_gbp": 51.77,
                       "in_stock": True, "rating": 3, "scraped_on": "2026-01-01"}
    assert rows[1]["in_stock"] is False and rows[1]["rating"] == 5


def test_next_page():
    assert transform.next_page(HTML) == "page-2.html"
    assert transform.next_page("<html></html>") is None


@pytest.mark.parametrize("text,expected", [("£51.77", 51.77), ("Â£9.5", 9.5), ("12", 12.0)])
def test_parse_price(text, expected):
    assert transform.parse_price(text) == expected


def test_dedupe():
    rows = transform.parse_page(HTML, D1)
    assert len(transform.dedupe(rows + rows)) == 2


def test_quality_flags_bad_data(monkeypatch):
    monkeypatch.setattr(quality.config, "MIN_ROWS", 1)
    rows = transform.parse_page(HTML, D1)
    assert quality.check(rows) == []
    rows[0]["price_gbp"] = -5
    assert any("prices outside" in p for p in quality.check(rows))
    with pytest.raises(quality.DataQualityError):
        quality.assert_ok(rows)


def test_quality_min_rows():
    assert any("only" in p for p in quality.check([]))


def test_upsert_is_idempotent(tmp_path):
    conn = load.connect(tmp_path / "t.db")
    rows = transform.parse_page(HTML, D1)
    load.upsert(conn, rows)
    load.upsert(conn, rows)  # re-run same day
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 2
    load.upsert(conn, transform.parse_page(HTML, D2))  # new day = new history rows
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 2
