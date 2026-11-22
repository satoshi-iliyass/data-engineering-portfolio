"""Bugs found by review: each test failed on the first version. Values are compared, not only row counts."""
import json
import os
import urllib.request

import boto3
import pytest
from moto import mock_aws

os.environ.update(AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test", AWS_DEFAULT_REGION="eu-west-3",
                  PYARROW_LAYER_ARN="")

from src import config, handler, infra, query, run_local  # noqa: E402

G = {"order_id": "O1", "customer_id": "C1", "country": "fr", "product": "Mouse", "quantity": 2, "unit_price": 25.0,
     "currency": "EUR", "created_at": "2026-11-20T10:00:00Z"}


@pytest.fixture
def lake(monkeypatch):
    """A fresh local AWS; lake(name, rows) uploads + processes, lake(name) replays an already uploaded file."""
    from moto.server import ThreadedMotoServer
    port = run_local.free_port()
    srv = ThreadedMotoServer(port=port, verbose=False)
    srv.start()
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/moto-api/reset", method="POST"))
    monkeypatch.setenv("AWS_ENDPOINT_URL", f"http://127.0.0.1:{port}")
    infra.deploy(with_lambda=False)
    s3 = boto3.client("s3", region_name=config.REGION)

    def send(name, rows=None):
        key = config.INCOMING_PREFIX + name
        if rows is not None:
            s3.put_object(Bucket=config.RAW_BUCKET, Key=key, Body="\n".join(json.dumps(r) for r in rows).encode())
        return handler.lambda_handler(run_local.s3_event(config.RAW_BUCKET, key))["results"][0]
    yield send
    srv.stop()


def orders():
    return query.connect().sql("SELECT order_id, quantity FROM orders_latest ORDER BY 1").fetchall()


def test_replaying_an_old_file_does_not_cancel_a_correction(lake):
    lake("a.jsonl", [G])
    lake("b_corrections.jsonl", [{**G, "quantity": 9}])
    lake("a.jsonl")
    assert orders() == [("O1", 9)]


def test_resent_file_removes_orders_it_no_longer_contains(lake):
    lake("a.jsonl", [G, {**G, "order_id": "O2", "created_at": "2026-11-19T10:00:00Z"}])
    lake("a.jsonl", [G])
    assert orders() == [("O1", 2)]


@pytest.mark.parametrize("first,second", [("orders.v1.jsonl", "orders.v2.jsonl"), ("x/a.jsonl", "y/a.jsonl")])
def test_two_files_never_overwrite_each_other(lake, first, second):
    lake(first, [G])
    lake(second, [{**G, "order_id": "O2"}])
    assert orders() == [("O1", 2), ("O2", 2)]


@pytest.mark.parametrize("bad,reason", [({**G, "quantity": 2.7}, "bad field"), ({**G, "quantity": "nan"}, "bad field"),
                                        ({**G, "quantity": "inf"}, "bad field"), ({**G, "quantity": 1001}, "quantity out"),
                                        ({**G, "unit_price": "nan"}, "negative or infinite"),
                                        ({**G, "unit_price": "inf"}, "negative or infinite"),
                                        ([1, 2], "not a JSON object"), ("text", "not a JSON object"), (5, "not a JSON object")])
def test_doubtful_rows_are_quarantined_not_guessed(bad, reason):
    row, why = handler.validate(bad)
    assert row is None and why.startswith(reason)


def test_one_strange_line_does_not_lose_the_file(lake):
    res = lake("a.jsonl", [G, [1, 2]])
    assert (res["rows_ok"], res["rows_rejected"]) == (1, 1) and orders() == [("O1", 2)]


@pytest.mark.parametrize("value", [2, 2.0, "2"])
def test_whole_number(value):
    assert handler.whole_number(value) == 2


def test_trigger_is_wired_to_the_lambda_for_the_right_folder():
    with mock_aws():
        fn = infra.deploy()["lambda"]
        infra.wire_trigger(fn)
        conf = boto3.client("s3", region_name=config.REGION).get_bucket_notification_configuration(Bucket=config.RAW_BUCKET)
        rule = conf["LambdaFunctionConfigurations"][0]
        assert rule["LambdaFunctionArn"] == fn and rule["Events"] == ["s3:ObjectCreated:*"]
        assert rule["Filter"]["Key"]["FilterRules"][0]["Value"] == config.INCOMING_PREFIX
        policy = json.loads(boto3.client("lambda", region_name=config.REGION).get_policy(FunctionName=fn)["Policy"])
        assert policy["Statement"][0]["Principal"]["Service"] == "s3.amazonaws.com"


def test_lambda_may_only_touch_its_own_buckets():
    with mock_aws():
        infra.deploy()
        doc = boto3.client("iam", region_name=config.REGION).get_role_policy(
            RoleName="shop-ingest-lambda", PolicyName="ingest")["PolicyDocument"]
        allowed = {(a, s["Resource"]) for s in doc["Statement"] if "s3" in str(s["Action"])
                   for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}
        lake, raw = f"arn:aws:s3:::{config.LAKE_BUCKET}", f"arn:aws:s3:::{config.RAW_BUCKET}"
        assert allowed == {("s3:GetObject", raw + "/*"), ("s3:GetObject", lake + "/*"), ("s3:PutObject", lake + "/*"),
                           ("s3:DeleteObject", lake + "/*"), ("s3:ListBucket", lake)}


def test_demo_runs_and_replay_changes_no_value(tmp_path, monkeypatch):
    for name in ("AWS_ENDPOINT_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"):
        monkeypatch.setenv(name, os.environ.get(name, ""))          # restored after the test
    monkeypatch.setattr(run_local, "ROOT", tmp_path)
    run_local.main()
    text = (tmp_path / "reports" / "lake_report.md").read_text(encoding="utf-8")
    assert "Files ingested: **15**" in text and "same values: True" in text
