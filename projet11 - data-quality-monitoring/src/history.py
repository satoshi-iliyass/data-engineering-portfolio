"""Run history in SQLite + volume anomaly detection (row count far from the recent norm)."""
import sqlite3
import statistics
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT, passed INTEGER,
                                 critical_failed INTEGER, warning_failed INTEGER);
CREATE TABLE IF NOT EXISTS dataset_volume (run_id INTEGER, dataset TEXT, row_count INTEGER);
CREATE TABLE IF NOT EXISTS check_results (run_id INTEGER, dataset TEXT, check_id TEXT, severity TEXT,
                                          passed INTEGER, detail TEXT);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def previous_counts(con, dataset: str, n: int = 14) -> list[int]:
    rows = con.execute("SELECT row_count FROM dataset_volume WHERE dataset = ? ORDER BY run_id DESC LIMIT ?",
                       (dataset, n)).fetchall()
    return [r[0] for r in rows]


def volume_anomaly(history: list[int], current: int, max_change_pct: float = 30.0, z_max: float = 3.0):
    """Returns (is_ok, detail). Needs 3+ previous runs; flags big jumps/drops vs. the recent mean."""
    if len(history) < 3:
        return True, f"{current} rows (learning: {len(history)} previous run(s))"
    mean = statistics.fmean(history)
    sd = statistics.pstdev(history)
    change = 100 * (current - mean) / mean if mean else 0.0
    z = (current - mean) / sd if sd else (0.0 if current == mean else float("inf"))
    ok = abs(change) <= max_change_pct or abs(z) <= z_max
    return ok, f"{current} rows vs mean {mean:.0f} ({change:+.1f} %, z={z:.1f})"


def save_run(con, run_at: str, results, volumes: dict[str, int]) -> int:
    crit = sum(1 for r in results if not r.passed and r.severity == "critical")
    warn = sum(1 for r in results if not r.passed and r.severity == "warning")
    cur = con.execute("INSERT INTO runs (run_at, passed, critical_failed, warning_failed) VALUES (?,?,?,?)",
                      (run_at, int(crit == 0), crit, warn))
    run_id = cur.lastrowid
    con.executemany("INSERT INTO dataset_volume VALUES (?,?,?)", [(run_id, d, n) for d, n in volumes.items()])
    con.executemany("INSERT INTO check_results VALUES (?,?,?,?,?,?)",
                    [(run_id, r.dataset, r.check_id, r.severity, int(r.passed), r.detail) for r in results])
    con.commit()
    return run_id
