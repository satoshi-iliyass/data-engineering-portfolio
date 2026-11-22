"""Infrastructure as code (boto3): buckets, Glue catalog, IAM role, Lambda + S3 trigger.

Same script works on real AWS (credentials in env) or on the local moto server (AWS_ENDPOINT_URL set).
Idempotent: safe to run again.
"""
import io
import json
import os
import zipfile
from pathlib import Path

import boto3

from . import config
from .handler import GLUE_COLUMNS

ROOT = Path(__file__).resolve().parent.parent
# Public AWS layer that ships pyarrow (AWS SDK for pandas). Region-specific; shown for eu-west-3.
PYARROW_LAYER = "arn:aws:lambda:eu-west-3:336392948345:layer:AWSSDKPandas-Python312:13"


def _bucket(s3, name):
    try:
        s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": config.REGION})
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        pass
    s3.put_public_access_block(Bucket=name, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_encryption(Bucket=name, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})


def _layers() -> list[str]:
    """The public layer only exists on real AWS; the local mock (or PYARROW_LAYER_ARN='') skips it."""
    arn = os.getenv("PYARROW_LAYER_ARN", "" if os.getenv("AWS_ENDPOINT_URL") else PYARROW_LAYER)
    return [arn] if arn else []


def _lambda_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in ("__init__.py", "config.py", "handler.py"):
            z.write(ROOT / "src" / f, f"src/{f}")
    return buf.getvalue()


def deploy(with_lambda: bool = True) -> dict:
    s3 = boto3.client("s3", region_name=config.REGION)
    glue = boto3.client("glue", region_name=config.REGION)
    for b in (config.RAW_BUCKET, config.LAKE_BUCKET):
        _bucket(s3, b)
    # raw files expire after 90 days in cheap storage tiers; curated data is kept
    s3.put_bucket_lifecycle_configuration(Bucket=config.RAW_BUCKET, LifecycleConfiguration={"Rules": [{
        "ID": "raw-to-ia-then-glacier", "Status": "Enabled", "Filter": {"Prefix": ""},
        "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}, {"Days": 90, "StorageClass": "GLACIER"}]}]})

    try:
        glue.create_database(DatabaseInput={"Name": config.GLUE_DB})
    except glue.exceptions.AlreadyExistsException:
        pass
    try:
        glue.create_table(DatabaseName=config.GLUE_DB, TableInput={
            "Name": config.GLUE_TABLE, "TableType": "EXTERNAL_TABLE",
            "PartitionKeys": [{"Name": "dt", "Type": "string"}],
            "Parameters": {"classification": "parquet"},
            "StorageDescriptor": {
                "Columns": GLUE_COLUMNS,
                "Location": f"s3://{config.LAKE_BUCKET}/{config.CURATED_PREFIX}",
                "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                "SerdeInfo": {"SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"}}})
    except glue.exceptions.AlreadyExistsException:
        pass
    if not with_lambda:
        return {"buckets": [config.RAW_BUCKET, config.LAKE_BUCKET]}

    iam = boto3.client("iam", region_name=config.REGION)
    lam = boto3.client("lambda", region_name=config.REGION)
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
                                                     "Principal": {"Service": "lambda.amazonaws.com"}}]}
    try:
        role_arn = iam.create_role(RoleName="shop-ingest-lambda",
                                   AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName="shop-ingest-lambda")["Role"]["Arn"]
    # least privilege: read raw, write lake, add Glue partitions, write logs
    iam.put_role_policy(RoleName="shop-ingest-lambda", PolicyName="ingest", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{config.RAW_BUCKET}/*"},
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
             "Resource": f"arn:aws:s3:::{config.LAKE_BUCKET}/*"},
            # without ListBucket, S3 answers 403 instead of 'not found' for a missing manifest
            {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{config.LAKE_BUCKET}"},
            {"Effect": "Allow", "Action": ["glue:CreatePartition", "glue:GetTable"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": "*"}]}))
    env = {"Variables": {"RAW_BUCKET": config.RAW_BUCKET, "LAKE_BUCKET": config.LAKE_BUCKET,
                         "GLUE_DB": config.GLUE_DB}}
    try:
        fn_arn = lam.create_function(
            FunctionName="shop-orders-ingest", Runtime="python3.12", Role=role_arn,
            Handler="src.handler.lambda_handler", Code={"ZipFile": _lambda_zip()},
            Timeout=120, MemorySize=512, Environment=env,
            Layers=_layers())["FunctionArn"]
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName="shop-orders-ingest", ZipFile=_lambda_zip())
        fn_arn = lam.get_function(FunctionName="shop-orders-ingest")["Configuration"]["FunctionArn"]
    return {"buckets": [config.RAW_BUCKET, config.LAKE_BUCKET], "lambda": fn_arn, "role": role_arn,
            "trigger": f"s3://{config.RAW_BUCKET}/{config.INCOMING_PREFIX}*.jsonl[.gz] -> {fn_arn}"}


def wire_trigger(fn_arn: str) -> None:
    """S3 ObjectCreated -> Lambda (real AWS only: moto would need Docker to execute the function)."""
    boto3.client("lambda", region_name=config.REGION).add_permission(
        FunctionName=fn_arn, StatementId="s3-invoke", Action="lambda:InvokeFunction",
        Principal="s3.amazonaws.com", SourceArn=f"arn:aws:s3:::{config.RAW_BUCKET}")
    boto3.client("s3", region_name=config.REGION).put_bucket_notification_configuration(
        Bucket=config.RAW_BUCKET, NotificationConfiguration={"LambdaFunctionConfigurations": [{
            "LambdaFunctionArn": fn_arn, "Events": ["s3:ObjectCreated:*"],
            "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": config.INCOMING_PREFIX}]}}}]})


if __name__ == "__main__":
    out = deploy()
    wire_trigger(out["lambda"])
    print(json.dumps(out, indent=1))
