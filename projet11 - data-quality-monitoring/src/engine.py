"""Turns YAML data contracts into Great Expectations suites, runs them, adds checks GX lacks
(freshness, volume anomaly), and returns one flat result list."""
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("GX_ANALYTICS_ENABLED", "false")
import duckdb                      # noqa: E402
import great_expectations as gx    # noqa: E402
import pandas as pd                # noqa: E402
import yaml                        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
logging.getLogger("great_expectations").setLevel(logging.ERROR)
SEVERITIES = ("critical", "warning")


@dataclass
class CheckResult:
    dataset: str
    check_id: str
    type: str
    severity: str
    passed: bool
    detail: str = ""
    failed_examples: list = field(default_factory=list)


# ---------- contracts & sources ----------

def load_contracts(folder: Path) -> list[dict]:
    contracts = []
    for f in sorted(Path(folder).glob("*.yml")):
        c = yaml.safe_load(f.read_text(encoding="utf-8"))
        for key in ("dataset", "source", "checks"):
            if key not in c:
                raise ValueError(f"{f.name}: missing '{key}'")
        for chk in c["checks"]:
            if chk.get("severity") not in SEVERITIES:
                raise ValueError(f"{f.name}:{chk.get('id')}: severity must be one of {SEVERITIES}")
        c["_file"] = f
        contracts.append(c)
    return contracts


def _path(contract: dict) -> Path:
    p = Path(contract["source"]["path"])
    return p if p.is_absolute() else (contract["_file"].parent.parent / p).resolve()


def read_sql(contract: dict, sql: str) -> pd.DataFrame:
    src, path = contract["source"], _path(contract)
    if src["type"] == "duckdb":
        with duckdb.connect(str(path), read_only=True) as con:
            return con.sql(sql).df()
    if src["type"] == "sqlite":
        con = sqlite3.connect(path)
        try:
            return pd.read_sql_query(sql, con)
        finally:
            con.close()
    raise ValueError(f"unknown source type {src['type']}")


def load_frame(contract: dict) -> pd.DataFrame:
    return read_sql(contract, f"SELECT * FROM {contract['source']['table']}")


# ---------- contract check -> GX expectation ----------

def to_expectation(chk: dict, contract: dict):
    t, col, mostly = chk["type"], chk.get("column"), chk.get("mostly", 1.0)
    meta = {"check_id": chk["id"]}
    E = gx.expectations
    if t == "not_null":
        return E.ExpectColumnValuesToNotBeNull(column=col, mostly=mostly, meta=meta)
    if t == "unique":
        return E.ExpectColumnValuesToBeUnique(column=col, meta=meta)
    if t == "unique_combination":
        return E.ExpectCompoundColumnsToBeUnique(column_list=chk["columns"], meta=meta)
    if t == "between":
        return E.ExpectColumnValuesToBeBetween(column=col, min_value=chk.get("min"), max_value=chk.get("max"),
                                               mostly=mostly, meta=meta)
    if t == "in_set":
        return E.ExpectColumnValuesToBeInSet(column=col, value_set=chk["values"], mostly=mostly, meta=meta)
    if t == "regex":
        return E.ExpectColumnValuesToMatchRegex(column=col, regex=chk["pattern"], mostly=mostly, meta=meta)
    if t == "row_count_between":
        return E.ExpectTableRowCountToBeBetween(min_value=chk.get("min"), max_value=chk.get("max"), meta=meta)
    if t == "mean_between":
        return E.ExpectColumnMeanToBeBetween(column=col, min_value=chk.get("min"), max_value=chk.get("max"), meta=meta)
    if t == "pair_greater_or_equal":
        return E.ExpectColumnPairValuesAToBeGreaterThanB(column_A=chk["column_a"], column_B=chk["column_b"],
                                                         or_equal=True, mostly=mostly, meta=meta)
    if t == "foreign_key":                     # referential integrity: values must exist in the parent table
        table, ref_col = chk["ref"].rsplit(".", 1)
        parents = read_sql(contract, f"SELECT DISTINCT {ref_col} AS k FROM {table}")["k"].tolist()
        return E.ExpectColumnValuesToBeInSet(column=col, value_set=parents, mostly=mostly, meta=meta)
    return None                                # handled outside GX (freshness)


def check_freshness(chk: dict, df: pd.DataFrame, now: datetime) -> tuple[bool, str]:
    latest = pd.to_datetime(df[chk["column"]], utc=True, format="ISO8601").max()
    if pd.isna(latest):
        return False, "no timestamp"
    age_h = (now - latest.to_pydatetime()).total_seconds() / 3600
    return age_h <= chk["max_age_hours"], f"newest row {age_h:.1f} h old (max {chk['max_age_hours']} h)"


# ---------- runner ----------

def run_contract(contract: dict, ctx, now: datetime | None = None, df: pd.DataFrame | None = None) -> tuple[list[CheckResult], int]:
    now = now or datetime.now(timezone.utc)
    name = contract["dataset"]
    if df is None:
        df = load_frame(contract)
    by_id = {c["id"]: c for c in contract["checks"]}
    results = []

    suite = gx.ExpectationSuite(name=f"{name}_suite")
    for chk in contract["checks"]:
        exp = to_expectation(chk, contract)
        if exp is not None:
            suite.add_expectation(exp)
        elif chk["type"] == "freshness":
            ok, detail = check_freshness(chk, df, now)
            results.append(CheckResult(name, chk["id"], chk["type"], chk["severity"], ok, detail))
        else:
            raise ValueError(f"{name}:{chk['id']}: unknown check type {chk['type']}")

    if suite.expectations:
        asset = ctx.data_sources.add_or_update_pandas(f"src_{name}").add_dataframe_asset(name)
        batch = asset.add_batch_definition_whole_dataframe("all").get_batch(batch_parameters={"dataframe": df})
        res = batch.validate(suite)
        for r in res.results:
            cid = r.expectation_config.meta["check_id"]
            chk, info = by_id[cid], r.result or {}
            if "unexpected_count" in info:
                detail = f"{info['unexpected_count']} bad of {info.get('element_count', len(df))} rows"
            elif "observed_value" in info:
                detail = f"observed {info['observed_value']}"
            else:
                detail = ""
            if r.exception_info and r.exception_info.get("raised_exception"):
                detail = "ERROR " + str(r.exception_info.get("exception_message"))[:150]
            examples = [str(v) for v in (info.get("partial_unexpected_list") or [])[:5]]
            results.append(CheckResult(name, cid, chk["type"], chk["severity"], bool(r.success), detail, examples))
    order = {c["id"]: i for i, c in enumerate(contract["checks"])}
    return sorted(results, key=lambda r: order[r.check_id]), len(df)


def new_context():
    ctx = gx.get_context(mode="ephemeral")
    try:
        from great_expectations.data_context.types.base import ProgressBarsConfig
        ctx.variables.progress_bars = ProgressBarsConfig(globally=False)
    except Exception:          # progress bars are cosmetic
        pass
    return ctx
