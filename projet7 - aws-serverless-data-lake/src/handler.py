"""AWS Lambda: triggered when a raw order file lands in S3.

raw JSON-lines  ->  validate  ->  typed Parquet partitioned by day (dt=YYYY-MM-DD)  ->  Glue partitions
                     +->  quarantine file with the rejection reason
                     +->  manifest (counts) for monitoring

Idempotent: output keys are derived from the source file name, so re-processing a file overwrites
its own output instead of duplicating rows.
"""
import gzip
import io
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import unquote_plus

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

from . import config

log = logging.getLogger()
log.setLevel(logging.INFO)

SCHEMA = pa.schema([
    ("order_id", pa.string()), ("customer_id", pa.string()), ("country", pa.string()),
    ("product", pa.string()), ("quantity", pa.int32()), ("unit_price", pa.float64()),
    ("currency", pa.string()), ("amount_eur", pa.float64()),
    ("created_at", pa.timestamp("us", tz="UTC")),
    ("source_file", pa.string()), ("processed_at", pa.timestamp("us", tz="UTC")),
])
GLUE_COLUMNS = [{"Name": f.name, "Type": {"string": "string", "int32": "int", "double": "double"}.get(
    str(f.type), "timestamp")} for f in SCHEMA]


def validate(rec: dict) -> tuple[dict | None, str | None]:
    """Return (clean_row, None) or (None, reason). Never 'fixes' data silently."""
    if not rec.get("order_id"):
        return None, "missing order_id"
    try:
        qty = int(rec["quantity"])
        price = float(rec["unit_price"])
        created = datetime.fromisoformat(str(rec["created_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as err:
        return None, f"bad field: {err.__class__.__name__} {err}"[:120]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if not 0 < qty <= config.MAX_QUANTITY:
        return None, f"quantity out of range: {qty}"
    if price < 0:
        return None, f"negative price: {price}"
    cur = str(rec.get("currency", "")).upper()
    if cur not in config.FX_TO_EUR:
        return None, f"unknown currency: {cur or 'empty'}"
    return {
        "order_id": str(rec["order_id"]), "customer_id": str(rec.get("customer_id") or ""),
        "country": str(rec.get("country") or "").upper(), "product": str(rec.get("product") or ""),
        "quantity": qty, "unit_price": price, "currency": cur,
        "amount_eur": round(qty * price * config.FX_TO_EUR[cur], 2),
        "created_at": created.astimezone(timezone.utc),
    }, None


def read_jsonl(body: bytes, key: str) -> list[tuple[int, dict | None, str]]:
    if key.endswith(".gz"):
        body = gzip.decompress(body)
    out = []
    for n, line in enumerate(body.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append((n, json.loads(line), line))
        except json.JSONDecodeError:
            out.append((n, None, line))
    return out


def process_object(bucket: str, key: str, s3=None, glue=None) -> dict:
    s3 = s3 or boto3.client("s3", region_name=config.REGION)
    glue = glue or boto3.client("glue", region_name=config.REGION)
    stem = PurePosixPath(key).name.split(".")[0]
    now = datetime.now(timezone.utc)

    rows, bad, seen = {}, [], set()
    records = read_jsonl(s3.get_object(Bucket=bucket, Key=key)["Body"].read(), key)
    for n, rec, raw in records:
        clean, reason = validate(rec) if rec is not None else (None, "invalid JSON")
        if clean is None:
            bad.append({"line": n, "reason": reason, "raw": raw[:500]})
            continue
        if clean["order_id"] in seen:
            log.info("duplicate %s in %s: last version kept", clean["order_id"], key)
        seen.add(clean["order_id"])
        rows[clean["order_id"]] = {**clean, "source_file": key, "processed_at": now}

    by_day = defaultdict(list)
    for r in rows.values():
        by_day[r["created_at"].strftime("%Y-%m-%d")].append(r)

    written = []
    for dt, day_rows in sorted(by_day.items()):
        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(day_rows, schema=SCHEMA), buf, compression="snappy")
        out_key = f"{config.CURATED_PREFIX}dt={dt}/{stem}.parquet"
        s3.put_object(Bucket=config.LAKE_BUCKET, Key=out_key, Body=buf.getvalue())
        written.append(out_key)
        add_partition(glue, dt)

    q_key = f"{config.QUARANTINE_PREFIX}{stem}.jsonl"
    if bad:
        s3.put_object(Bucket=config.LAKE_BUCKET, Key=q_key,
                      Body="\n".join(json.dumps(b) for b in bad).encode())
    else:
        s3.delete_object(Bucket=config.LAKE_BUCKET, Key=q_key)   # re-run of a fixed file clears old quarantine

    manifest = {"source": f"s3://{bucket}/{key}", "processed_at": now.isoformat(timespec="seconds"),
                "rows_in": len(records), "rows_ok": len(rows), "rows_rejected": len(bad),
                "duplicates_dropped": len(records) - len(bad) - len(rows), "partitions": sorted(by_day), "outputs": written}
    s3.put_object(Bucket=config.LAKE_BUCKET, Key=f"{config.MANIFEST_PREFIX}{stem}.json",
                  Body=json.dumps(manifest, indent=1).encode())
    log.info("processed %s: %d ok, %d rejected, partitions %s", key, len(rows), len(bad), sorted(by_day))
    return manifest


def add_partition(glue, dt: str) -> None:
    """Register dt=... in the Glue catalog so Athena sees it (no slow MSCK REPAIR needed)."""
    try:
        glue.create_partition(DatabaseName=config.GLUE_DB, TableName=config.GLUE_TABLE, PartitionInput={
            "Values": [dt],
            "StorageDescriptor": {
                "Columns": GLUE_COLUMNS,
                "Location": f"s3://{config.LAKE_BUCKET}/{config.CURATED_PREFIX}dt={dt}/",
                "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                "SerdeInfo": {"SerializationLibrary":
                              "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"}}})
    except glue.exceptions.AlreadyExistsException:
        pass


def lambda_handler(event, context=None):
    """Entry point configured in AWS Lambda (S3 ObjectCreated trigger)."""
    results = []
    for rec in event.get("Records", []):
        bucket = rec["s3"]["bucket"]["name"]
        key = unquote_plus(rec["s3"]["object"]["key"])
        if not key.startswith(config.INCOMING_PREFIX):
            log.info("ignored %s", key)
            continue
        results.append(process_object(bucket, key))
    return {"processed": len(results), "results": results}
