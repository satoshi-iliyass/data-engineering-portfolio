"""End-to-end demo on a real PostgreSQL 17 (wal_level = logical).

    python -m src.run_demo

Creates the shop database, snapshots it, then runs 6 'hours' of activity. After each hour the CDC pipeline and
the classic watermark load both sync; a crash is simulated in hour 3. Finally both copies are reconciled
row by row against the source. Writes reports/cdc_report.md.
"""
import random
import time
from pathlib import Path

import psycopg

from . import cdc, config, reconcile, source_app, watermark

ROOT = Path(__file__).resolve().parent.parent


def main(rounds: int = 6, seed: int = 42) -> dict:
    rng = random.Random(seed)
    admin = config.dsn("postgres")
    source_app.create_database(admin, config.SOURCE_DB)
    source_app.create_database(admin, config.TARGET_DB)
    source_app.seed(rng)
    cdc.setup_target()
    snap = cdc.initial_snapshot()
    watermark.sync()
    print("snapshot:", snap)

    activity = {"inserted": 0, "updated": 0, "deleted": 0, "customers_changed": 0}
    t_sync, crash_recovered = 0.0, None
    for h in range(1, rounds + 1):
        for k, v in source_app.simulate_round(rng).items():
            activity[k] += v
        lag = cdc.pending_changes()
        t0 = time.perf_counter()
        if h == 3:                                      # process dies after commit, before acknowledging
            try:
                cdc.sync_once(crash_before_ack=True)
            except RuntimeError:
                pass
            replay = cdc.sync_all()                     # restart: same changes are read again ...
            crash_recovered = replay                    # ... and skipped (applied = 0 for that batch)
        else:
            cdc.sync_all()
        t_sync += time.perf_counter() - t0
        watermark.sync()
        print(f"hour {h}: {lag} changes pending before sync -> {cdc.pending_changes()} after")

    with psycopg.connect(config.dsn(config.TARGET_DB)) as tgt:
        q = lambda sql: tgt.execute(sql).fetchone()                                   # noqa: E731
        events = q("SELECT count(*), count(*) FILTER (WHERE op = 'DELETE'), count(*) FILTER (WHERE op = 'UPDATE') "
                   "FROM raw.cdc_events")
        scd = q("SELECT count(*), count(DISTINCT id), max(n) FROM (SELECT id, count(*) OVER (PARTITION BY id) n "
                "FROM core.customers_history) x")
        soft_deleted = q("SELECT count(*) FROM core.orders WHERE _deleted")[0]
        example = tgt.execute("SELECT id, segment, email, valid_from::timestamp(0), valid_to::timestamp(0), is_current "
                              "FROM core.customers_history WHERE id = (SELECT id FROM core.customers_history "
                              "GROUP BY id ORDER BY count(*) DESC, id LIMIT 1) ORDER BY valid_from").fetchall()
    rc, rw, cust = reconcile.cdc(), reconcile.watermark(), reconcile.customers()
    res = {"snapshot": snap, "activity": activity, "events": events, "scd": scd, "soft_deleted": soft_deleted,
           "cdc": rc, "watermark": rw, "customers": cust, "crash_replay": crash_recovered, "sync_seconds": round(t_sync, 2)}

    lines = ["# CDC run report (PostgreSQL 17, logical decoding)", "",
             f"- Snapshot: {snap['customers']} customers, {snap['orders']} orders (slot created at LSN {snap['slot_lsn']})",
             f"- Source activity over {rounds} hours: {activity['inserted']} new orders, {activity['updated']} order "
             f"updates, {activity['deleted']} hard deletes, {activity['customers_changed']} customer changes",
             f"- WAL events captured: **{events[0]}** ({events[2]} updates, {events[1]} deletes), "
             f"applied in {t_sync:.1f} s total ({events[0] / t_sync:.0f} events/s)",
             f"- Crash test (hour 3): after restart {crash_recovered['read']} events re-read, "
             f"{crash_recovered['applied']} re-applied -> no duplicates",
             f"- SCD2: {scd[0]} versions for {scd[1]} customers (max {scd[2]} versions for one customer)",
             f"- Deleted orders kept as soft-deletes: {soft_deleted}", "",
             "## Reconciliation against the source (row by row)", "",
             "| Method | Source rows | Target rows | Missing | Ghost rows (deleted in source) | Different | Source revenue | Target revenue | Match |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, r in (("CDC (WAL)", rc), ("Watermark updated_at", rw)):
        lines.append(f"| {name} | {r['source_rows']} | {r['target_rows']} | {r['missing']} | {r['ghost_rows']} | "
                     f"{r['different']} | {r['source_revenue']:,.2f} | {r['target_revenue']:,.2f} | "
                     f"{'YES' if r['match'] else 'NO'} |")
    lines += ["", f"Customers (every column): {cust['source_rows']} rows in the source, {cust['different']} different, "
              f"match: {'YES' if cust['match'] else 'NO'}"]
    lines += ["", "## Example customer history (SCD type 2)", "",
              "| id | segment | email | valid_from | valid_to | is_current |", "|---|---|---|---|---|---|"]
    lines += ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in example]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "cdc_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return res


if __name__ == "__main__":
    main()
