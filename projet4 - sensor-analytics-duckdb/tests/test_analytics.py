"""Runs the real generate -> convert -> analytics chain on a smaller dataset (~6.5 days) in a temp folder."""
import duckdb

from src import analytics, convert, generate


def test_full_chain(tmp_path, monkeypatch):
    raw = tmp_path / "raw" / "s.csv"
    pq = tmp_path / "parquet"
    monkeypatch.setattr(generate, "RAW_CSV", raw)
    monkeypatch.setattr(convert, "RAW_CSV", raw)
    monkeypatch.setattr(convert, "PARQUET_DIR", pq)
    monkeypatch.setattr("sys.argv", ["generate", "--rows", "3000000"])

    generate.main()
    convert.main()

    pq_glob = pq.as_posix() + "/**/*.parquet"
    n = duckdb.sql(f"SELECT COUNT(*) FROM read_parquet('{pq_glob}', hive_partitioning=true)").fetchall()[0][0]
    assert n == 3_000_000                                        # nothing lost in conversion

    sql = analytics.QUERIES["degradation_ranking"].replace(analytics.PQ, pq_glob)
    ranking = duckdb.sql(sql).fetchall()
    flagged = {row[0] for row in ranking if row[-1] == "INSPECT"}
    assert flagged == {7, 13}                                  # the two degrading machines are found
