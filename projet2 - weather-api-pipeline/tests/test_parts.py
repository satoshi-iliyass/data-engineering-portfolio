"""Parts that had no test: API call, raw copy, quarantine, run log, and the assembly when one city breaks."""
import json
import logging
from datetime import datetime, timezone

import pytest
import requests

from src import config, extract, load, pipeline, quality

NOW = datetime(2026, 1, 1, 3, 4, 5, tzinfo=timezone.utc)
PAYLOAD = {"hourly": {"time": ["2026-01-01T00:00", "2026-01-01T01:00"], "temperature_2m": [5.0, 999.0],
                      "relative_humidity_2m": [80, 82], "precipitation": [0.0, 1.2], "wind_speed_10m": [10.0, 12.0]}}


class Answer:
    def __init__(self, code=200, body=None):
        self.status_code, self.body = code, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"error {self.status_code}", response=self)

    def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


@pytest.fixture
def api(monkeypatch):
    """api.answers = prepared answers; api.calls / api.waits = what happened."""
    class Api:
        answers, calls, waits = [], [], []
    Api.answers, Api.calls, Api.waits = [], [], []

    def get(url, params, timeout):
        Api.calls.append((url, params, timeout))
        answer = Api.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(extract.requests, "get", get)
    monkeypatch.setattr(extract.time, "sleep", Api.waits.append)
    return Api


def test_fetch_sends_the_right_question(api):
    api.answers = [Answer(body={"a": 1})]
    assert extract.fetch("Paris", 48.8, 2.3) == {"a": 1}
    url, params, timeout = api.calls[0]
    assert url == config.API_URL and timeout == config.TIMEOUT_S and api.waits == []
    assert params == {"latitude": 48.8, "longitude": 2.3, "hourly": ",".join(config.HOURLY_VARS),
                      "past_days": config.PAST_DAYS, "forecast_days": config.FORECAST_DAYS, "timezone": "UTC"}


@pytest.mark.parametrize("failure", [requests.ConnectionError("cut"), Answer(429), Answer(500), Answer(body=None)])
def test_fetch_retries_temporary_failures(api, failure):
    api.answers = [failure, failure, Answer(body={"ok": 1})]
    assert extract.fetch("Paris", 0, 0) == {"ok": 1}
    assert len(api.calls) == 3 and api.waits == [1, 2]


def test_fetch_gives_up_after_max_retries(api):
    api.answers = [requests.ConnectionError("cut")] * 9
    with pytest.raises(RuntimeError, match=r"Paris: API failed after 3 attempt\(s\): cut"):
        extract.fetch("Paris", 0, 0)
    assert len(api.calls) == config.MAX_RETRIES


@pytest.mark.parametrize("code", [400, 404, 499])
def test_fetch_does_not_retry_a_wrong_question(api, code):
    api.answers = [Answer(code), Answer(body={})]
    with pytest.raises(RuntimeError, match=f"1 attempt.*{code}"):
        extract.fetch("Paris", 0, 0)
    assert len(api.calls) == 1 and api.waits == []


def test_save_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    extract.save_raw("Paris", PAYLOAD, NOW)
    assert json.loads((tmp_path / "2026-01-01" / "Paris_030405.json").read_text(encoding="utf-8")) == PAYLOAD


def test_one_bad_city_does_not_stop_the_others(tmp_path, monkeypatch):
    def fetch(city, lat, lon):
        if city == "B":
            raise RuntimeError("down")
        return {"c": city, "pos": (lat, lon)}
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(config, "CITIES", {"A": (1, 2), "B": (3, 4), "C": (5, 6)})
    monkeypatch.setattr(extract, "fetch", fetch)
    assert extract.extract_all() == {"A": {"c": "A", "pos": (1, 2)}, "C": {"c": "C", "pos": (5, 6)}}
    assert len(list(tmp_path.rglob("*.json"))) == 2


def test_all_cities_down_is_an_error(tmp_path, monkeypatch):
    def fetch(*args):
        raise RuntimeError("down")
    monkeypatch.setattr(extract, "fetch", fetch)
    with pytest.raises(RuntimeError, match="all cities failed"):
        extract.extract_all()


BOUND_CASES = [(col, v, ok) for col, (lo, hi) in config.BOUNDS.items()
               for v, ok in ((lo, True), (hi, True), (lo - 0.1, False), (hi + 0.1, False))]


@pytest.mark.parametrize("col,value,ok", BOUND_CASES)
def test_bounds(col, value, ok):
    row = {"city": "A", "ts_utc": "t", "temperature_c": 5.0, col: value}
    assert (quality.validate(row) is None) == ok


@pytest.mark.parametrize("change,reason", [({"city": ""}, "missing key"), ({"ts_utc": None}, "missing key"),
                                           ({"temperature_c": None}, "missing temperature")])
def test_missing_values(change, reason):
    assert quality.validate({"city": "A", "ts_utc": "t", "temperature_c": 5.0, **change}) == reason


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "w.db")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "CITIES", {"A": (0, 0), "B": (0, 0)})
    return tmp_path


def state():
    conn = load.connect(config.DB_PATH)
    try:
        return (conn.execute("SELECT city, ts_utc, temperature_c FROM hourly_weather ORDER BY 1").fetchall(),
                conn.execute("SELECT city, ts_utc, reason FROM rejected_rows ORDER BY 1").fetchall(),
                conn.execute("SELECT status, inserted, updated, rejected, message FROM runs").fetchall())
    finally:
        conn.close()


def test_assembly_ok_with_quarantine(factory, monkeypatch):
    monkeypatch.setattr(extract, "extract_all", lambda: {"A": PAYLOAD, "B": PAYLOAD})
    assert pipeline.run() == 0
    good, bad, runs = state()
    assert good == [("A", "2026-01-01T00:00", 5.0), ("B", "2026-01-01T00:00", 5.0)]
    assert [b[:2] for b in bad] == [("A", "2026-01-01T01:00"), ("B", "2026-01-01T01:00")] and "999" in bad[0][2]
    assert runs == [("success", 2, 0, 2, "")]


def test_missing_city_is_never_called_a_success(factory, monkeypatch):
    monkeypatch.setattr(extract, "extract_all", lambda: {"A": PAYLOAD})
    assert pipeline.run() == 2
    good, _, runs = state()
    assert len(good) == 1 and runs == [("partial", 1, 0, 1, "missing cities: B")]


@pytest.mark.parametrize("broken", [{}, {"hourly": None}, {"hourly": {}}])
def test_unreadable_city_does_not_lose_the_others(factory, monkeypatch, broken):
    monkeypatch.setattr(extract, "extract_all", lambda: {"A": PAYLOAD, "B": broken})
    assert pipeline.run() == 2
    good, _, runs = state()
    assert len(good) == 1 and runs[0][0] == "partial" and runs[0][4] == "missing cities: B"


def test_nothing_usable_is_a_failure(factory, monkeypatch):
    monkeypatch.setattr(extract, "extract_all", lambda: {"A": {}, "B": {}})
    assert pipeline.run() == 1
    good, _, runs = state()
    assert good == [] and runs == [("failed", 0, 0, 0, "no usable rows")]


def test_setup_logging_creates_the_log(factory, monkeypatch):
    monkeypatch.setattr(logging, "basicConfig", lambda **k: [h.close() for h in k["handlers"][:1]])
    pipeline.setup_logging()
    assert (factory / "logs" / "pipeline.log").exists()
