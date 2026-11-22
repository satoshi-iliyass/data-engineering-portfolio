"""Read any number of messy Excel files and return ONE clean, standardised table."""
import logging
import math
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Every known way branches name their columns -> one standard name
COLUMN_ALIASES = {
    "date": "date", "order_date": "date",
    "product": "product", "item": "product", "produit": "product",
    "qty": "quantity", "quantity": "quantity", "quantite": "quantity",
    "unit price": "unit_price", "price": "unit_price", "prix unitaire": "unit_price",
}
STANDARD = ["date", "product", "quantity", "unit_price"]
DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"]


def find_header_row(raw: pd.DataFrame) -> int:
    """The header is the first row where at least 3 cells are known column names."""
    for i, row in raw.iterrows():
        known = sum(str(v).strip().lower() in COLUMN_ALIASES for v in row if pd.notna(v))
        if known >= 3:
            return i
    raise ValueError("no recognisable header row")


def parse_date(value) -> pd.Timestamp:
    if isinstance(value, datetime):          # a real Excel date cell (pd.Timestamp is a datetime too)
        return pd.Timestamp(value)
    s = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return pd.to_datetime(s, format=fmt)
        except ValueError:
            continue
    return pd.NaT


def is_ambiguous(text: str) -> bool:
    """'1,299' or '1.299': thousand or decimal? We cannot know, so we do not guess."""
    return re.fullmatch(r"-?\d{1,3}[.,]\d{3}", text) is not None


def to_number(value) -> float:
    """'320,15' / '320.15' / 320.15 -> 320.15; anything doubtful -> NaN (the row is then rejected)."""
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
        if is_ambiguous(text):
            return float("nan")
        value = text.replace(",", ".")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def normalise_product(name) -> str:
    """Missing name -> '' (the row is then rejected)."""
    if pd.isna(name):
        return ""
    return re.sub(r"\s+", " ", str(name)).strip().title()


def read_one(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, header=None, dtype=object)
    h = find_header_row(raw)
    df = raw.iloc[h + 1:].copy()
    df.columns = [COLUMN_ALIASES.get(str(c).strip().lower(), str(c)) for c in raw.iloc[h]]
    missing = set(STANDARD) - set(df.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")
    df = df[STANDARD]
    df["source_file"] = path.name
    df["branch"] = path.stem.split("_")[0].title()
    return df


def standardise(df: pd.DataFrame) -> pd.DataFrame:
    """Drop blank lines and TOTAL footers, give every column its type."""
    df = df.dropna(how="all", subset=STANDARD).copy()
    df = df[~df["date"].astype(str).str.upper().str.startswith("TOTAL")].copy()
    df["date"] = df["date"].map(parse_date)
    df["product"] = df["product"].map(normalise_product)
    df["quantity"] = df["quantity"].map(to_number)
    df["unit_price"] = df["unit_price"].map(to_number)
    return df


def reject_reasons(df: pd.DataFrame) -> pd.Series:
    """'' = the row is good. A line identical to a previous one is set aside, never deleted in silence."""
    reason = pd.Series("", index=df.index)
    reason[df["date"].isna()] += "bad date; "
    reason[df["product"] == ""] += "missing product; "
    reason[~((df["quantity"] > 0) & (df["quantity"] % 1 == 0))] += "bad quantity; "
    reason[~(df["unit_price"] > 0)] += "bad price; "
    key = ["branch", "date", "product", "quantity", "unit_price"]
    reason[(reason == "") & df.duplicated(subset=key)] += "duplicate line; "
    return reason


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (clean rows, rejected rows with reason)."""
    df = standardise(df)
    reason = reject_reasons(df)
    rejected = df[reason != ""].assign(reason=reason[reason != ""])
    good = df[reason == ""].copy()
    if good.empty:
        raise RuntimeError(f"no valid rows ({len(rejected)} rejected)")
    good["quantity"] = good["quantity"].astype(int)
    good["revenue"] = (good["quantity"] * good["unit_price"]).round(2)
    good["month"] = good["date"].dt.to_period("M").astype(str)
    return good.sort_values(["date", "branch"]).reset_index(drop=True), rejected


def load_folder(folder: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    frames, errors = [], []
    for path in sorted(folder.glob("*.xlsx")):
        try:
            frames.append(read_one(path))
        except Exception as err:            # one broken file must not stop the batch
            errors.append(f"{path.name}: {err}")
            log.error("skipped %s: %s", path.name, err)
    if not frames:
        raise RuntimeError("no readable files")
    good, rejected = clean(pd.concat(frames, ignore_index=True))
    return good, rejected, errors
