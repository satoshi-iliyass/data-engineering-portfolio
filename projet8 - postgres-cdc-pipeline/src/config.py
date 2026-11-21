"""Connection settings from environment variables (never hard-coded passwords)."""
import os
from pathlib import Path

HOST = os.getenv("PG_HOST", "localhost")
PORT = int(os.getenv("PG_PORT", "5433"))
USER = os.getenv("PG_USER", "postgres")
SOURCE_DB = os.getenv("SOURCE_DB", "shop_oltp")      # the application database (OLTP)
TARGET_DB = os.getenv("TARGET_DB", "analytics")      # the analytics copy
SLOT = os.getenv("CDC_SLOT", "analytics_cdc")
BATCH_CHANGES = int(os.getenv("CDC_BATCH", "5000"))

TABLES = {                                           # replicated tables and their primary key
    "customers": "id",
    "orders": "id",
}


def password() -> str:
    pw = os.getenv("PG_PASSWORD") or os.getenv("PGPASSWORD")
    if pw:
        return pw
    local = Path(__file__).resolve().parents[2] / "_outils" / "pg_motdepasse_local.txt"   # local dev only
    return local.read_text().strip() if local.exists() else ""


def dsn(db: str) -> str:
    return f"host={HOST} port={PORT} user={USER} password={password()} dbname={db}"
