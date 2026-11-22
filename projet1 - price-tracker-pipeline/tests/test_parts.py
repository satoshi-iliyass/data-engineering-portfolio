"""Parts that had no test: download, raw copy, load details, report, and the assembly when one part breaks."""
import logging
import sys
from datetime import date

import openpyxl
import pytest
import requests

from src import config, extract, load, pipeline, quality, report

DAY, DAY2 = date(2026, 1, 1), date(2026, 1, 2)


class Answer:
    def __init__(self, code=200, text="page"):
        self.status_code, self.text, self.encoding = code, text, None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"error {self.status_code}", response=self)


class FakeSession:
    """Gives the prepared answers one by one and counts the calls."""
    def __init__(self, *answers):
        self.answers, self.calls, self.headers, self.timeouts = list(answers), 0, {}, []

    def get(self, url, timeout):
        self.calls += 1
        self.timeouts.append(timeout)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def waits(monkeypatch):
    seen = []
    monkeypatch.setattr(extract.time, "sleep", seen.append)
    return seen


def test_fetch_ok_first_time(waits):
    answer = Answer(text="hello")
    s = FakeSession(answer)
    assert extract.fetch("u", s) == "hello"
    assert s.calls == 1 and waits == [] and answer.encoding == "utf-8" and s.timeouts == [config.TIMEOUT_S]


def test_fetch_retries_temporary_failure(waits):
    s = FakeSession(requests.ConnectionError("cut"), Answer(503), Answer(text="ok"))
    assert extract.fetch("u", s) == "ok"
    assert s.calls == 3 and waits == [1, 2]


def test_fetch_gives_up_after_max_retries(waits):
    s = FakeSession(*[requests.ConnectionError("cut")] * 5)
    with pytest.raises(RuntimeError, match="giving up on u: cut"):
        extract.fetch("u", s)
    assert s.calls == config.MAX_RETRIES and waits == [1, 2]


@pytest.mark.parametrize("code", [400, 404, 499])
def test_fetch_does_not_retry_missing_page(waits, code):
    s = FakeSession(Answer(code), Answer())
    with pytest.raises(RuntimeError, match=str(code)):
        extract.fetch("u", s)
    assert s.calls == 1 and waits == []


@pytest.mark.parametrize("code", [429, 500])
def test_fetch_retries_when_site_is_busy(waits, code):
    s = FakeSession(Answer(code), Answer(text="ok"))
    assert extract.fetch("u", s) == "ok" and s.calls == 2


def test_save_raw_keeps_each_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    p1 = extract.save_raw("£ un", "page-1.html", DAY)
    p2 = extract.save_raw("deux", "page-2.html", DAY)
    assert p1 == tmp_path / "2026-01-01" / "page-1.html"
    assert p1.read_text(encoding="utf-8") == "£ un" and p2.read_text(encoding="utf-8") == "deux"


def test_extract_all_follows_next_links(tmp_path, monkeypatch, waits):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    pages = {"page-1.html": '<li class="next"><a href="page-2.html">n</a></li>', "page-2.html": "<p>end</p>"}
    monkeypatch.setattr(extract, "fetch", lambda url, s: pages[url.removeprefix(config.BASE_URL)])
    assert [f.name for f in extract.extract_all(DAY)] == ["page-1.html", "page-2.html"]
    assert waits == [config.REQUEST_DELAY_S] * 2


def test_extract_all_stops_at_the_safety_cap(tmp_path, monkeypatch, waits):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(config, "MAX_PAGES", 3)
    answers = iter(['<li class="next"><a href="page-1.html">n</a></li>'] * 5)      # a 6th call would fail the test
    monkeypatch.setattr(extract, "fetch", lambda url, s: next(answers))
    assert len(extract.extract_all(DAY)) == 3


def row(price=10.0, day=DAY, pid="a", title="A", rating=3, stock=True):
    return {"product_id": pid, "title": title, "price_gbp": price, "in_stock": stock, "rating": rating,
            "scraped_on": day.isoformat()}


@pytest.fixture
def db(tmp_path):
    conn = load.connect(tmp_path / "t.db")
    yield conn
    conn.close()


def test_upsert_same_day_replaces_price_and_stock(db):
    load.upsert(db, [row(10.0)])
    assert load.upsert(db, [row(12.5, stock=False, title="New", rating=5)]) == 1
    assert db.execute("SELECT price_gbp, in_stock FROM prices").fetchall() == [(12.5, 0)]
    assert db.execute("SELECT title, rating FROM products").fetchall() == [("New", 5)]
    assert load.count_day(db, "2026-01-01") == 1 and load.count_day(db, "2026-01-02") == 0


def test_upsert_is_all_or_nothing(db):
    with pytest.raises(Exception):
        load.upsert(db, [row(), row(None, pid="b")])
    assert load.count_day(db, "2026-01-01") == 0


@pytest.mark.parametrize("change,word", [({"title": ""}, "empty id/title"), ({"rating": 9}, "invalid rating"),
                                         ({"price_gbp": 5000.0}, "prices outside")])
def test_quality_rules(monkeypatch, change, word):
    monkeypatch.setattr(config, "MIN_ROWS", 1)
    assert quality.check([row()]) == []
    assert any(word in p for p in quality.check([{**row(), **change}]))


def test_quality_sees_duplicates(monkeypatch):
    monkeypatch.setattr(config, "MIN_ROWS", 1)
    assert any("duplicate" in p for p in quality.check([row(), row()]))


def test_report_content(db, tmp_path):
    load.upsert(db, [row(10.0), row(4.0, pid="b", title="B")])
    load.upsert(db, [row(15.0, DAY2), row(4.0, DAY2, pid="b", title="B", stock=False)])
    wb = openpyxl.load_workbook(report.build(db, "2026-01-02", tmp_path / "out"))
    lines = [[c.value for c in r] for ws in wb for r in ws.iter_rows()]
    assert ["A", 15, "yes", 3] in lines and ["B", 4, "no", 3] in lines      # latest prices
    assert ["A", 10, 15, 5, 50] in lines                                    # price change
    assert not any(r[0] == "B" and len(r) == 5 for r in lines)              # unchanged price = not listed
    assert [r[0].value for r in wb["Prices today"].iter_rows(min_row=2)] == ["A", "B"]      # most expensive first
    summary = {r[0].value: r[1].value for r in wb["Summary"].iter_rows(min_row=2)}
    assert summary == {"Date": "2026-01-02", "Products tracked": 2, "Average price (GBP)": 9.5, "Min price (GBP)": 4,
                       "Max price (GBP)": 15, "In stock": 1, "Price changes vs previous run": 1}


# ---------- assembly: one broken part must stay in its place ----------

PAGE = """<article class="product_pod"><p class="star-rating Three"></p>
<h3><a href="a_1/index.html" title="A">A</a></h3><p class="price_color">£51.77</p>
<p class="availability">In stock</p></article>"""


@pytest.fixture
def factory(tmp_path, monkeypatch):
    for name, folder in (("RAW_DIR", "raw"), ("REPORT_DIR", "reports"), ("LOG_DIR", "logs")):
        monkeypatch.setattr(config, name, tmp_path / folder)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "p.db")
    monkeypatch.setattr(config, "MIN_ROWS", 1)
    extract.save_raw(PAGE, "page-1.html", DAY)
    return tmp_path


def state():
    conn = load.connect(config.DB_PATH)
    try:
        return load.count_day(conn, DAY.isoformat()), conn.execute("SELECT status, rows FROM runs").fetchall()
    finally:
        conn.close()


def test_assembly_ok(factory):
    assert pipeline.run(DAY, from_raw=True) == 0
    assert state() == (1, [("success", 1)])
    assert len(list((factory / "reports").glob("*.xlsx"))) == 1


def test_assembly_uses_the_site_when_not_from_raw(factory, monkeypatch):
    monkeypatch.setattr(extract, "extract_all", lambda d: [factory / "raw" / d.isoformat() / "page-1.html"])
    assert pipeline.run(DAY) == 0


def test_report_crash_keeps_the_data(factory, monkeypatch):
    def crash(*args):
        raise ValueError("report broken")
    monkeypatch.setattr(report, "build", crash)
    assert pipeline.run(DAY, from_raw=True) == 2
    assert state() == (1, [("loaded_without_report", 1)])


def test_report_that_cannot_even_be_loaded_keeps_the_data(factory, monkeypatch):
    """Same effect as a typo in report.py: importing it fails."""
    monkeypatch.delattr("src.report")
    monkeypatch.setitem(sys.modules, "src.report", None)
    assert pipeline.run(DAY, from_raw=True) == 2
    assert state() == (1, [("loaded_without_report", 1)])


def test_report_can_be_rebuilt_alone(factory):
    pipeline.run(DAY, from_raw=True)
    assert pipeline.report_only(DAY) == 0 and pipeline.report_only(DAY2) == 1


def test_doubtful_price_stores_nothing(factory):
    extract.save_raw(PAGE.replace("£51.77", "51,77 €"), "page-1.html", DAY)
    assert pipeline.run(DAY, from_raw=True) == 1
    assert state() == (0, [("failed", 0)])


def test_changed_site_stores_nothing(factory):
    extract.save_raw("<html>new design</html>", "page-1.html", DAY)
    assert pipeline.run(DAY, from_raw=True) == 1 and state()[0] == 0


def test_no_raw_files(factory):
    assert pipeline.run(DAY2, from_raw=True) == 1


@pytest.mark.parametrize("args,called", [(["--from-raw", "2026-01-01"], ("run", DAY, True)),
                                         (["--report", "2026-01-01"], ("report", DAY)),
                                         ([], ("run", date.today(), False))])
def test_command_line(monkeypatch, args, called):
    seen = []
    monkeypatch.setattr(sys, "argv", ["pipeline", *args])
    monkeypatch.setattr(pipeline, "setup_logging", lambda: None)
    monkeypatch.setattr(pipeline, "run", lambda d, from_raw: seen.append(("run", d, from_raw)) or 0)
    monkeypatch.setattr(pipeline, "report_only", lambda d: seen.append(("report", d)) or 0)
    with pytest.raises(SystemExit) as e:
        pipeline.main()
    assert seen == [called] and e.value.code == 0


def test_setup_logging_creates_the_log(factory, monkeypatch):
    monkeypatch.setattr(logging, "basicConfig", lambda **k: [h.close() for h in k["handlers"][:1]])
    pipeline.setup_logging()
    assert (factory / "logs" / "pipeline.log").exists()
