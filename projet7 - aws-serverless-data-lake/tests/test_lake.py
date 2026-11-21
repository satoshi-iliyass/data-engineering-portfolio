"""Tests on an in-memory AWS mock (moto). No account, no network except DuckDB's httpfs extension download."""
import gzip
import io
import json
import os

import boto3
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

os.environ.update(AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test", AWS_DEFAULT_REGION="eu-west-3",
                  PYARROW_LAYER_ARN="")
os.environ.pop("AWS_ENDPOINT_URL", None)

from src import config, generate, handler, infra  # noqa: E402

GOOD = {"order_id": "O1", "customer_id": "C1", "country": "fr", "product": "Mouse", "quantity": 2,
        "unit_price": 25.0, "currency": "eur", "created_at": "2026-11-20T10:00:00Z"}


@pytest.fixture
def aws():
    with mock_aws():
        infra.deploy(with_lambda=True)
        yield boto3.client("s3", region_name=config.REGION)


def put(s3, name, rows, gz=False):
    key = config.INCOMING_PREFIX + name
    body = "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows).encode()
    s3.put_object(Bucket=config.RAW_BUCKET, Key=key, Body=gzip.compress(body) if gz else body)
    return handler.lambda_handler({"Records": [{"s3": {"bucket": {"name": config.RAW_BUCKET},
                                                        "object": {"key": key}}}]})


def read_parquet(s3, key):
    return pq.read_table(io.BytesIO(s3.get_object(Bucket=config.LAKE_BUCKET, Key=key)["Body"].read())).to_pylist()


@pytest.mark.parametrize("change,reason", [
    ({"order_id": None}, "missing order_id"), ({"quantity": -1}, "quantity out of range"),
    ({"currency": "XYZ"}, "unknown currency"), ({"created_at": "31/02/2026"}, "bad field"),
    ({"unit_price": "N/A"}, "bad field"), ({"unit_price": -3}, "negative price"),
])
def test_validation_rejects_with_reason(change, reason):
    row, why = handler.validate({**GOOD, **change})
    assert row is None and why.startswith(reason)


def test_validation_normalises_and_converts_currency():
    row, _ = handler.validate({**GOOD, "currency": "usd", "unit_price": 10})
    assert row["currency"] == "USD" and row["country"] == "FR"
    assert row["amount_eur"] == round(2 * 10 * config.FX_TO_EUR["USD"], 2)


def test_file_is_partitioned_typed_and_bad_rows_quarantined(aws):
    rows = [GOOD, {**GOOD, "order_id": "O2", "created_at": "2026-11-19T23:59:00Z"},
            {**GOOD, "order_id": "O3", "quantity": 0}, "{not json"]
    res = put(aws, "orders_x.jsonl.gz", rows, gz=True)["results"][0]
    assert (res["rows_in"], res["rows_ok"], res["rows_rejected"]) == (4, 2, 2)
    assert res["partitions"] == ["2026-11-19", "2026-11-20"]
    day = read_parquet(aws, f"{config.CURATED_PREFIX}dt=2026-11-20/orders_x.parquet")
    assert [r["order_id"] for r in day] == ["O1"] and day[0]["amount_eur"] == 50.0
    q = aws.get_object(Bucket=config.LAKE_BUCKET, Key=f"{config.QUARANTINE_PREFIX}orders_x.jsonl")["Body"].read()
    reasons = [json.loads(line)["reason"] for line in q.decode().splitlines()]
    assert reasons == ["quantity out of range: 0", "invalid JSON"]


def test_glue_partitions_registered_once(aws):
    put(aws, "a.jsonl", [GOOD])
    put(aws, "b.jsonl", [{**GOOD, "order_id": "O9"}])          # same day, second file
    glue = boto3.client("glue", region_name=config.REGION)
    parts = glue.get_partitions(DatabaseName=config.GLUE_DB, TableName=config.GLUE_TABLE)["Partitions"]
    assert [p["Values"] for p in parts] == [["2026-11-20"]]


def test_replay_is_idempotent_and_clears_fixed_quarantine(aws):
    put(aws, "day.jsonl", [GOOD, {**GOOD, "order_id": None}])
    put(aws, "day.jsonl", [GOOD, {**GOOD, "order_id": "O2"}])  # source re-sent a fixed file
    objs = aws.list_objects_v2(Bucket=config.LAKE_BUCKET, Prefix=config.CURATED_PREFIX)["Contents"]
    assert len(objs) == 1
    assert len(read_parquet(aws, objs[0]["Key"])) == 2
    q = aws.list_objects_v2(Bucket=config.LAKE_BUCKET, Prefix=config.QUARANTINE_PREFIX)
    assert q.get("KeyCount", 0) == 0


def test_duplicate_lines_keep_last_version(aws):
    res = put(aws, "dup.jsonl", [GOOD, {**GOOD, "quantity": 7}])["results"][0]
    assert res["rows_ok"] == 1 and res["duplicates_dropped"] == 1
    assert read_parquet(aws, res["outputs"][0])[0]["quantity"] == 7


def test_events_outside_incoming_prefix_are_ignored(aws):
    aws.put_object(Bucket=config.RAW_BUCKET, Key="other/x.jsonl", Body=b"{}")
    out = handler.lambda_handler({"Records": [{"s3": {"bucket": {"name": config.RAW_BUCKET},
                                                       "object": {"key": "other/x.jsonl"}}}]})
    assert out["processed"] == 0


def test_infra_is_secure_and_rerunnable(aws):
    infra.deploy()                                                  # second run must not fail
    enc = aws.get_bucket_encryption(Bucket=config.LAKE_BUCKET)
    assert enc["ServerSideEncryptionConfiguration"]["Rules"][0]["ApplyServerSideEncryptionByDefault"][
        "SSEAlgorithm"] == "AES256"
    assert aws.get_public_access_block(Bucket=config.RAW_BUCKET)["PublicAccessBlockConfiguration"]["BlockPublicAcls"]


def test_generator_contains_defects_and_corrections():
    files = generate.generate(days=3)
    assert len(files) == 4 and files[-1][0].endswith("_corrections.jsonl")
    assert files[0][0].endswith(".gz")


def test_end_to_end_sql_on_s3():
    """Full path through a real HTTP S3 endpoint: lambda -> Parquet on S3 -> DuckDB SQL with corrections."""
    from moto.server import ThreadedMotoServer

    from src import query
    from src.run_local import free_port
    port = free_port()
    srv = ThreadedMotoServer(port=port, verbose=False)
    srv.start()
    os.environ["AWS_ENDPOINT_URL"] = f"http://127.0.0.1:{port}"
    try:
        infra.deploy(with_lambda=False)
        s3 = boto3.client("s3", region_name=config.REGION)
        put(s3, "d1.jsonl", [GOOD, {**GOOD, "order_id": "O2", "quantity": 1}])
        put(s3, "d2_corrections.jsonl", [{**GOOD, "quantity": 4}])        # O1 corrected 2 -> 4
        con = query.connect()
        rows = con.sql("SELECT order_id, quantity FROM orders_latest ORDER BY order_id").fetchall()
        assert rows == [("O1", 4), ("O2", 1)]
        assert query.run_kpis(con)["daily_revenue"]["rows"][0][1:] == (2, 125.0)
        assert query.quality_summary()["files"] == 2
    finally:
        os.environ.pop("AWS_ENDPOINT_URL")
        srv.stop()
