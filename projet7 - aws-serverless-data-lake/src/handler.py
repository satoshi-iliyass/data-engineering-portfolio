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
import math
from collections import defaultdict
from datetime import datetime, timezone
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
    ("source_file", pa.string()), ("received_at", pa.timestamp("us", tz="UTC")), ("processed_at", pa.timestamp("us", tz="UTC")),
])
GLUE_COLUMNS = [{"Name": f.name, "Type": {"string": "string", "int32": "int", "double": "double"}.get(
    str(f.type), "timestamp")} for f in SCHEMA]


def whole_number(value) -> int:
    """2 / 2.0 / '2' -> 2; 2.7 is refused, never rounded."""
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"not a whole number: {value}")
    return int(number)


def validate(rec: dict) -> tuple[dict | None, str | None]:
    """Return (clean_row, None) or (None, reason). Never 'fixes' data silently."""
    if not isinstance(rec, dict):
        return None, "not a JSON object"
    if not rec.get("order_id"):
        return None, "missing order_id"
    try:
        qty = whole_number(rec["quantity"])
        price = float(rec["unit_price"])
        created = datetime.fromisoformat(str(rec["created_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as err:
        return None, f"bad field: {err.__class__.__name__} {err}"[:120]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if not 0 < qty <= config.MAX_QUANTITY:
        return None, f"quantity out of range: {qty}"
    if not math.isfinite(price) or price < 0:
        return None, f"negative or infinite price: {price}"
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


def output_stem(key: str) -> str:
    """One source file = one output name. The whole path is kept: 'x.v1.jsonl' and 'x.v2.jsonl' never collide."""
    return key.removeprefix(config.INCOMING_PREFIX).replace("/", "__")


def split_records(records: list, key: str, received_at: datetime, now: datetime) -> tuple[dict, list[dict]]:
    """Return (good rows by order_id, last version wins; rejected lines with the reason)."""
    rows, bad = {}, []
    for n, rec, raw in records:
        clean, reason = validate(rec) if rec is not None else (None, "invalid JSON")
        if clean is None:
            bad.append({"line": n, "reason": reason, "raw": raw[:500]})
        else:
            rows[clean["order_id"]] = {**clean, "source_file": key, "received_at": received_at, "processed_at": now}
    return rows, bad


def write_days(s3, glue, rows: dict, stem: str) -> list[str]:
    """One Parquet file per order day; returns the keys written."""
    by_day = defaultdict(list)
    for r in rows.values():
        by_day[r["created_at"].strftime("%Y-%m-%d")].append(r)
    written = []
    for dt, day_rows in sorted(by_day.items()):
        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(day_rows, schema=SCHEMA), buf, compression="snappy")
        out_key = f"{config.CURATED_PREFIX}dt={dt}/{stem}.parquet"
        s3.put_object(Bucket=config.LAKE_BUCKET, Key=out_key, Body=buf.getvalue())
        add_partition(glue, dt)
        written.append(out_key)
    return written


def previous_outputs(s3, stem: str) -> list[str]:
    """What this same source file produced last time (from its manifest)."""
    try:
        body = s3.get_object(Bucket=config.LAKE_BUCKET, Key=f"{config.MANIFEST_PREFIX}{stem}.json")["Body"].read()
    except s3.exceptions.NoSuchKey:
        return []
    return json.loads(body)["outputs"]


def write_quarantine(s3, bad: list[dict], stem: str) -> None:
    q_key = f"{config.QUARANTINE_PREFIX}{stem}.jsonl"
    if bad:
        s3.put_object(Bucket=config.LAKE_BUCKET, Key=q_key, Body="\n".join(json.dumps(b) for b in bad).encode())
    else:
        s3.delete_object(Bucket=config.LAKE_BUCKET, Key=q_key)   # re-run of a fixed file clears old quarantine


def process_object(bucket: str, key: str, s3=None, glue=None) -> dict:
    """Assembly: read -> split -> write days -> remove what the old version of this file left -> manifest."""
    s3 = s3 or boto3.client("s3", region_name=config.REGION)
    glue = glue or boto3.client("glue", region_name=config.REGION)
    stem, now = output_stem(key), datetime.now(timezone.utc)
    obj = s3.get_object(Bucket=bucket, Key=key)
    records = read_jsonl(obj["Body"].read(), key)
    rows, bad = split_records(records, key, obj["LastModified"], now)

    old = previous_outputs(s3, stem)
    written = write_days(s3, glue, rows, stem)
    for stale in set(old) - set(written):
        s3.delete_object(Bucket=config.LAKE_BUCKET, Key=stale)
    write_quarantine(s3, bad, stem)

    manifest = {"source": f"s3://{bucket}/{key}", "processed_at": now.isoformat(timespec="seconds"),
                "rows_in": len(records), "rows_ok": len(rows), "rows_rejected": len(bad),
                "duplicates_dropped": len(records) - len(bad) - len(rows),
                "partitions": [k.split("dt=")[1][:10] for k in written], "outputs": written}
    s3.put_object(Bucket=config.LAKE_BUCKET, Key=f"{config.MANIFEST_PREFIX}{stem}.json",
                  Body=json.dumps(manifest, indent=1).encode())
    log.info("processed %s: %d ok, %d rejected, partitions %s", key, len(rows), len(bad), manifest["partitions"])
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
