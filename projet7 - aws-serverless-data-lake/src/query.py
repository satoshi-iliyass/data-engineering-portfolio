"""Serverless SQL on the lake (Athena-style): DuckDB reads the Parquet files straight from S3.

On real AWS the same SQL runs in Athena on the Glue table `shop.orders`; here DuckDB is used because it
runs locally and against the moto S3 endpoint.
"""
import json
import os
from urllib.parse import urlparse

import boto3
import duckdb

from . import config

ORDERS_LATEST = f"""
CREATE OR REPLACE VIEW orders_latest AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY received_at DESC, source_file DESC) AS rn
    FROM read_parquet('s3://{config.LAKE_BUCKET}/{config.CURATED_PREFIX}*/*.parquet', hive_partitioning = true)
) WHERE rn = 1
"""   # an order re-sent in a later file (correction) replaces the old version: no double counting.
# 'Latest' = when the source file ARRIVED, not when we processed it: replaying an old file changes nothing.

KPIS = {
    "daily_revenue": """SELECT dt, COUNT(*) AS orders, ROUND(SUM(amount_eur), 2) AS revenue_eur
                        FROM orders_latest GROUP BY dt ORDER BY dt""",
    "by_country": """SELECT country, COUNT(*) AS orders, ROUND(SUM(amount_eur), 2) AS revenue_eur
                     FROM orders_latest GROUP BY country ORDER BY revenue_eur DESC""",
    "top_products": """SELECT product, SUM(quantity) AS units, ROUND(SUM(amount_eur), 2) AS revenue_eur
                       FROM orders_latest GROUP BY product ORDER BY revenue_eur DESC LIMIT 5""",
}


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    endpoint = os.getenv("AWS_ENDPOINT_URL")
    if endpoint:                                    # local moto server
        u = urlparse(endpoint)
        con.execute(f"""CREATE SECRET lake (TYPE s3, KEY_ID 'test', SECRET 'test', REGION '{config.REGION}',
                        ENDPOINT '{u.netloc}', URL_STYLE 'path', USE_SSL {str(u.scheme == 'https').lower()})""")
    else:                                           # real AWS: standard credential chain
        con.execute(f"CREATE SECRET lake (TYPE s3, PROVIDER credential_chain, REGION '{config.REGION}')")
    con.execute(ORDERS_LATEST)
    return con


def quality_summary() -> dict:
    """Aggregate the per-file manifests: how much data was rejected, per file."""
    s3 = boto3.client("s3", region_name=config.REGION)
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=config.LAKE_BUCKET, Prefix=config.MANIFEST_PREFIX)
    mans = [json.loads(s3.get_object(Bucket=config.LAKE_BUCKET, Key=o["Key"])["Body"].read())
            for p in pages for o in p.get("Contents", [])]
    rows_in = sum(m["rows_in"] for m in mans)
    rejected = sum(m["rows_rejected"] for m in mans)
    return {"files": len(mans), "rows_in": rows_in, "rows_rejected": rejected,
            "reject_rate_pct": round(100 * rejected / rows_in, 2) if rows_in else 0.0}


def run_kpis(con=None) -> dict:
    con = con or connect()
    out = {}
    for name, sql in KPIS.items():
        rel = con.sql(sql)
        out[name] = {"columns": rel.columns, "rows": rel.fetchall()}
    return out
