"""Each query is checked on a small hand-made dataset whose answer is known in advance."""
from datetime import datetime, timedelta

import duckdb
import pytest

from src import analytics, convert

START = datetime(2026, 1, 30)


@pytest.fixture
def lake(tmp_path, monkeypatch):
    """4 days, 2 machines. Machine 1: vibration rises by 1 per day. Machine 2: flat, one spike, one missing value."""
    raw, pq = tmp_path / "s.csv", tmp_path / "parquet"
    lines = ["machine_id,sensor,ts,value"]
    for h in range(96):
        ts = START + timedelta(hours=h)
        lines.append(f"1,vibration,{ts},{2 + h // 24}")
        lines.append(f"2,vibration,{ts},{'' if h == 5 else 50 if h == 10 else 2}")
        lines.append(f"1,temperature,{ts},{60 + h % 2 * 10}")
    raw.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(convert, "RAW_CSV", raw)
    monkeypatch.setattr(convert, "PARQUET_DIR", pq)
    convert.main()
    glob = pq.as_posix() + "/**/*.parquet"
    monkeypatch.setattr(analytics, "QUERIES", {k: q.replace(analytics.PQ, glob) for k, q in analytics.QUERIES.items()})
    monkeypatch.setattr(analytics, "PQ", glob)
    monkeypatch.setattr(analytics, "RAW_CSV", raw)
    monkeypatch.setattr(analytics, "OUT", tmp_path / "results")
    return tmp_path


def ask(name):
    return duckdb.sql(analytics.QUERIES[name]).fetchall()


def test_one_folder_per_month(lake):
    assert sorted(p.name for p in (lake / "parquet").iterdir()) == ["month=2026-01", "month=2026-02"]


def test_folder_size(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.bin").write_bytes(b"0" * 1_500_000)
    (tmp_path / "y.bin").write_bytes(b"0" * 500_000)
    assert convert.folder_size_mb(tmp_path) == 2.0


def test_missing_readings_are_counted(lake):
    assert ask("data_quality") == [("temperature", 96, 0, 0.0), ("vibration", 192, 1, 0.521)]


def test_daily_kpis(lake):
    rows = {(m, d.isoformat()): rest for m, d, *rest in ask("daily_machine_kpis")}
    assert len(rows) == 8
    assert rows[(1, "2026-01-31")] == [65.0, 70.0, 3.0]
    assert rows[(2, "2026-02-01")] == [None, None, 2.0]


def test_only_the_real_spike_is_found(lake):
    assert ask("spikes") == [(2, "vibration", 1)]


def test_only_the_worn_machine_is_flagged(lake):
    ranking = {r[0]: r for r in ask("degradation_ranking")}
    assert ranking[1][1:] == (1.0, 2.0, 5.0, "INSPECT")
    assert ranking[2][-1] == "ok"


def test_main_writes_every_result(lake, capsys):
    analytics.main()
    names = {p.name for p in (lake / "results").iterdir()}
    assert names == {f"{n}.csv" for n in analytics.QUERIES} | {"benchmark.txt"}
    assert "x faster" in (lake / "results" / "benchmark.txt").read_text()
    assert "2,vibration,1" in (lake / "results" / "spikes.csv").read_text()


def test_timed_really_runs_the_query():
    assert analytics.timed("SELECT 1") > 0
    with pytest.raises(duckdb.Error):
        analytics.timed("SELECT * FROM table_that_does_not_exist")
