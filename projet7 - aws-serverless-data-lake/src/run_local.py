"""End-to-end demo on a local AWS mock (moto server) - no AWS account, no cost.

    python -m src.run_local

1. start a local AWS endpoint (S3, Glue, IAM, Lambda APIs)
2. deploy the infrastructure with the same code used for real AWS
3. upload 14 daily order files + 1 corrections file to the raw bucket
4. deliver the S3 ObjectCreated event to the Lambda handler (what AWS does automatically)
5. re-deliver one event to prove idempotency
6. run the KPIs with SQL directly on S3 and write reports/lake_report.md
"""
import os
import socket
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def s3_event(bucket: str, key: str) -> dict:
    return {"Records": [{"eventSource": "aws:s3", "eventName": "ObjectCreated:Put",
                         "s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def main() -> None:
    from moto.server import ThreadedMotoServer
    port = free_port()
    server = ThreadedMotoServer(port=port, verbose=False)
    server.start()
    os.environ.update(AWS_ENDPOINT_URL=f"http://127.0.0.1:{port}", AWS_ACCESS_KEY_ID="test",
                      AWS_SECRET_ACCESS_KEY="test", AWS_DEFAULT_REGION="eu-west-3")
    try:
        import boto3

        from . import config, generate, handler, infra, query
        out = infra.deploy()
        print("deployed:", out["buckets"], "| lambda:", out["lambda"].split(":")[-1])

        s3 = boto3.client("s3", region_name=config.REGION)
        t0 = time.perf_counter()
        files = generate.generate()
        for name, body in files:
            key = config.INCOMING_PREFIX + name
            s3.put_object(Bucket=config.RAW_BUCKET, Key=key, Body=body)
            handler.lambda_handler(s3_event(config.RAW_BUCKET, key))
        ingest_s = time.perf_counter() - t0
        print(f"ingested {len(files)} files in {ingest_s:.1f}s")

        con = query.connect()
        before = con.sql("SELECT COUNT(*) FROM orders_latest").fetchall()[0][0]
        handler.lambda_handler(s3_event(config.RAW_BUCKET, config.INCOMING_PREFIX + files[0][0]))  # replay
        after = con.sql("SELECT COUNT(*) FROM orders_latest").fetchall()[0][0]
        raw_rows = con.sql(f"SELECT COUNT(*) FROM read_parquet('s3://{config.LAKE_BUCKET}/"
                           f"{config.CURATED_PREFIX}*/*.parquet')").fetchall()[0][0]

        kpis = query.run_kpis(con)
        q = query.quality_summary()
        glue = boto3.client("glue", region_name=config.REGION)
        parts = glue.get_partitions(DatabaseName=config.GLUE_DB, TableName=config.GLUE_TABLE)["Partitions"]
        objs = s3.list_objects_v2(Bucket=config.LAKE_BUCKET, Prefix=config.CURATED_PREFIX)["Contents"]

        lines = ["# Data lake run report (local AWS mock)", "",
                 f"- Files ingested: **{q['files']}** ({len(objs)} Parquet objects, {len(parts)} Glue partitions)",
                 f"- Rows received: **{q['rows_in']}**, rejected to quarantine: **{q['rows_rejected']}** "
                 f"({q['reject_rate_pct']} %)",
                 f"- Parquet rows: {raw_rows}; unique orders after corrections: **{after}**",
                 f"- Idempotency: replaying a file -> {before} orders before, {after} after",
                 f"- Ingestion time: {ingest_s:.1f} s", ""]
        for name, res in kpis.items():
            lines += [f"## {name}", "", "| " + " | ".join(res["columns"]) + " |",
                      "|" + "---|" * len(res["columns"])]
            lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in res["rows"]]
            lines.append("")
        (ROOT / "reports").mkdir(exist_ok=True)
        (ROOT / "reports" / "lake_report.md").write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines[:8]))
        print("report -> reports/lake_report.md")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
