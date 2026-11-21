"""Quality: split rows into valid / rejected instead of failing the whole run.
Rejected rows are kept in a quarantine table with the reason, so nothing is silently lost."""
from . import config


def validate(row: dict) -> str | None:
    """Return a rejection reason, or None if the row is valid."""
    if not row.get("city") or not row.get("ts_utc"):
        return "missing key"
    if row.get("temperature_c") is None:
        return "missing temperature"
    for col, (lo, hi) in config.BOUNDS.items():
        v = row.get(col)
        if v is not None and not lo <= v <= hi:
            return f"{col}={v} outside [{lo}, {hi}]"
    return None


def split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    good, bad = [], []
    for r in rows:
        reason = validate(r)
        if reason:
            bad.append({**r, "reason": reason})
        else:
            good.append(r)
    return good, bad
