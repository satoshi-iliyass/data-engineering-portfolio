"""Data quality gate.

    python -m src.run                      # all contracts, exit 1 if a CRITICAL check fails
    python -m src.run --fail-on warning    # stricter gate
    python -m src.run --contracts path/to/contracts

Outputs: reports/quality_report.html (scorecard), reports/quality_results.json, history in reports/quality_history.db
"""
import argparse
import html
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import engine, history

ROOT = engine.ROOT


def run(contracts_dir: Path, report_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    ctx = engine.new_context()
    hist = history.connect(report_dir / "quality_history.db")
    results, volumes, skipped = [], {}, []
    for c in engine.load_contracts(contracts_dir):
        name = c["dataset"]
        if not engine._path(c).exists():
            if c.get("optional"):
                skipped.append(name)
                continue
            results.append(engine.CheckResult(name, "source_exists", "source", "critical", False,
                                              f"source not found: {engine._path(c)}"))
            continue
        res, n_rows = engine.run_contract(c, ctx, now)
        if c.get("volume_anomaly"):
            ok, detail = history.volume_anomaly(history.previous_counts(hist, name), n_rows)
            res.append(engine.CheckResult(name, "volume_anomaly", "volume", "warning", ok, detail))
        results += res
        volumes[name] = n_rows
    run_id = history.save_run(hist, now.isoformat(timespec="seconds"), results, volumes)
    hist.close()

    crit = [r for r in results if not r.passed and r.severity == "critical"]
    warn = [r for r in results if not r.passed and r.severity == "warning"]
    summary = {"run_id": run_id, "run_at": now.isoformat(timespec="seconds"), "checks": len(results),
               "passed": sum(r.passed for r in results), "critical_failed": len(crit), "warning_failed": len(warn),
               "score_pct": round(100 * sum(r.passed for r in results) / len(results), 1) if results else 100.0,
               "datasets": volumes, "skipped_optional": skipped}
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "quality_results.json").write_text(
        json.dumps({"summary": summary, "results": [asdict(r) for r in results]}, indent=1, default=str),
        encoding="utf-8")
    (report_dir / "quality_report.html").write_text(render_html(summary, results), encoding="utf-8")
    return {"summary": summary, "results": results}


def render_html(s: dict, results) -> str:
    color = "#1a7f37" if s["critical_failed"] == 0 else "#cf222e"
    rows = []
    for r in results:
        badge = "PASS" if r.passed else ("FAIL" if r.severity == "critical" else "WARN")
        bg = {"PASS": "#dafbe1", "FAIL": "#ffebe9", "WARN": "#fff8c5"}[badge]
        ex = ", ".join(r.failed_examples)
        rows.append(f"<tr style='background:{bg}'><td>{html.escape(r.dataset)}</td><td>{html.escape(r.check_id)}</td>"
                    f"<td>{r.type}</td><td>{r.severity}</td><td><b>{badge}</b></td><td>{html.escape(r.detail)}</td>"
                    f"<td>{html.escape(ex)}</td></tr>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Data quality report</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#1f2328}}table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{border:1px solid #d0d7de;padding:6px 8px;text-align:left}}th{{background:#f6f8fa}}.k{{display:inline-block;margin-right:24px}}
.k b{{font-size:26px;display:block}}</style></head><body>
<h1>Data quality report</h1><p>Run #{s['run_id']} - {s['run_at']} UTC</p>
<div class="k"><b style="color:{color}">{s['score_pct']} %</b>checks passed</div>
<div class="k"><b>{s['checks']}</b>checks</div><div class="k"><b style="color:#cf222e">{s['critical_failed']}</b>critical failures</div>
<div class="k"><b style="color:#9a6700">{s['warning_failed']}</b>warnings</div>
<div class="k"><b>{len(s['datasets'])}</b>datasets</div>
<h2>Checks</h2><table><tr><th>Dataset</th><th>Check</th><th>Type</th><th>Severity</th><th>Result</th><th>Detail</th><th>Failing examples</th></tr>
{''.join(rows)}</table></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contracts", type=Path, default=ROOT / "contracts")
    ap.add_argument("--reports", type=Path, default=ROOT / "reports")
    ap.add_argument("--fail-on", choices=["critical", "warning"], default="critical")
    a = ap.parse_args()
    out = run(a.contracts, a.reports)
    s = out["summary"]
    for r in out["results"]:
        if not r.passed:
            print(f"[{r.severity.upper():8}] {r.dataset}.{r.check_id}: {r.detail} {r.failed_examples or ''}")
    print(f"score {s['score_pct']} % - {s['passed']}/{s['checks']} passed, {s['critical_failed']} critical, "
          f"{s['warning_failed']} warning(s), datasets {s['datasets']}, skipped {s['skipped_optional']}")
    gate = s["critical_failed"] + (s["warning_failed"] if a.fail_on == "warning" else 0)
    sys.exit(1 if gate else 0)


if __name__ == "__main__":
    main()
