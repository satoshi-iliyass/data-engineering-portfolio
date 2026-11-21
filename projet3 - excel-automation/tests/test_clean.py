import pandas as pd
import pytest
from openpyxl import Workbook

from src import clean


@pytest.mark.parametrize("v,exp", [("320,15", 320.15), ("4.5", 4.5), (12, 12.0), (" 1 200,5 ", 1200.5)])
def test_to_number(v, exp):
    assert clean.to_number(v) == exp


def test_to_number_garbage():
    assert pd.isna(clean.to_number("abc"))


@pytest.mark.parametrize("v", ["05/09/2026", "2026-09-05", "05-09-2026"])
def test_parse_date_formats(v):
    assert clean.parse_date(v) == pd.Timestamp("2026-09-05")


def test_normalise_product():
    assert clean.normalise_product("  HYDRAULIC   pump ") == "Hydraulic Pump"


def _write(path, rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    wb.save(path)


def test_end_to_end(tmp_path):
    _write(tmp_path / "paris_2026_09.xlsx", [
        ["Sales export"], [],
        ["Date", "Product", "Qty", "Unit price"],
        ["05/09/2026", "gear motor", 2, "185,00"],
        ["05/09/2026", "gear motor", 2, "185,00"],          # duplicate
        [None, None, None, None],                          # blank
        ["xx/09/2026", "Seal kit", 1, "9,90"],             # bad date -> rejected
        ["TOTAL", None, 5, None],                          # footer
    ])
    _write(tmp_path / "lyon_2026_09.xlsx", [
        ["order_date", "item", "quantity", "price"],
        ["2026-09-06", "Seal Kit", 3, 9.9],
    ])
    _write(tmp_path / "broken_2026_09.xlsx", [["foo", "bar"], [1, 2]])   # unreadable -> skipped

    good, rejected, errors = clean.load_folder(tmp_path)
    assert len(good) == 2
    assert set(good["branch"]) == {"Paris", "Lyon"}
    assert good.loc[good["branch"] == "Paris", "revenue"].item() == 370.0
    assert len(rejected) == 1 and "bad date" in rejected["reason"].item()
    assert len(errors) == 1 and "broken" in errors[0]
