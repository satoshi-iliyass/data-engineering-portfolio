"""All settings come from environment variables, so the same code runs on real AWS and on the local moto mock."""
import os

REGION = os.getenv("AWS_REGION", "eu-west-3")                 # Paris
RAW_BUCKET = os.getenv("RAW_BUCKET", "shop-raw-landing")       # immutable landing zone (what the source sent)
LAKE_BUCKET = os.getenv("LAKE_BUCKET", "shop-data-lake")       # curated/ quarantine/ _manifests/
GLUE_DB = os.getenv("GLUE_DB", "shop")
GLUE_TABLE = "orders"

INCOMING_PREFIX = "incoming/orders/"
CURATED_PREFIX = "curated/orders/"
QUARANTINE_PREFIX = "quarantine/orders/"
MANIFEST_PREFIX = "_manifests/orders/"

# Fixed reference rates (a real project would load a daily FX table)
FX_TO_EUR = {"EUR": 1.0, "USD": 0.92, "GBP": 1.17, "MAD": 0.092}
MAX_QUANTITY = 1000
