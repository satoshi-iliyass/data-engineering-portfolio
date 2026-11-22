"""Tricky cells seen in real Excel files, the report, the sample generator and the command line."""
import random
import sys
from datetime import date, datetime

import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook

from src import clean, main, make_sample_data, report

HEADER = ["Date", "Product", "Qty", "Unit price"]
OK = ["05/09/2026", "Pump", 1, "1,00"]


def folder(tmp_path, *rows, name="paris_2026_09.xlsx"):
    wb = Workbook()
    for r in [HEADER, *rows]:
        wb.active.append(r)
    tmp_path.mkdir(exist_ok=True)
    wb.save(tmp_path / name)
    return tmp_path


def test_real_excel_date_cell_is_accepted(tmp_path):
    good, rejected, _ = clean.load_folder(folder(tmp_path, [datetime(2026, 9, 5), "Pump", 2, 10.0]))
    assert len(rejected) == 0 and good["date"].item() == pd.Timestamp("2026-09-05")
    assert good["revenue"].item() == 20.0 and good["month"].item() == "2026-09" and good["branch"].item() == "Paris"


@pytest.mark.parametrize("row,reason", [
    (["31/02/2026", "Pump", 1, 1.0], "bad date; "), (["09/05/26", "Pump", 1, 1.0], "bad date; "),
    (["05/09/2026", None, 1, 1.0], "missing product; "), (["05/09/2026", "  ", 1, 1.0], "missing product; "),
    (["05/09/2026", "Pump", 2.7, 1.0], "bad quantity; "), (["05/09/2026", "Pump", 0, 1.0], "bad quantity; "),
    (["05/09/2026", "Pump", -1, 1.0], "bad quantity; "), (["05/09/2026", "Pump", "inf", 1.0], "bad quantity; "),
    (["05/09/2026", "Pump", None, 1.0], "bad quantity; "), (["05/09/2026", "Pump", "two", 1.0], "bad quantity; "),
    (["05/09/2026", "Pump", 1, "1,299"], "bad price; "), (["05/09/2026", "Pump", 1, "1.299"], "bad price; "),
    (["05/09/2026", "Pump", 1, "1.299,00"], "bad price; "), (["05/09/2026", "Pump", 1, 0], "bad price; "),
    (["05/09/2026", "Pump", 1, "n/a"], "bad price; "), (["x", "Pump", 0, 0], "bad date; bad quantity; bad price; "),
    (OK, "duplicate line; "),
])
def test_doubtful_row_is_set_aside_with_its_reason(tmp_path, row, reason):
    good, rejected, _ = clean.load_folder(folder(tmp_path, OK, row))
    assert len(good) == 1 and rejected["reason"].tolist() == [reason]


@pytest.mark.parametrize("value,expected", [("1 299,50", 1299.5), ("1 299,50", 1299.5), ("1 299,50", 1299.5),
                                            ("12,5", 12.5), (1.299, 1.299), ("1299", 1299.0), ("1,2999", 1.2999)])
def test_numbers_that_are_clear(value, expected):
    assert clean.to_number(value) == expected


def test_same_sale_in_two_branches_is_not_a_duplicate(tmp_path):
    folder(tmp_path, OK)
    good, rejected, _ = clean.load_folder(folder(tmp_path, OK, name="lyon_2026_09.xlsx"))
    assert len(good) == 2 and len(rejected) == 0 and good["branch"].tolist() == ["Lyon", "Paris"]      # sorted


def test_nothing_valid_is_a_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match=r"no valid rows \(1 rejected\)"):
        clean.load_folder(folder(tmp_path, ["xx", "Pump", 1, 1.0]))


def test_empty_folder_is_a_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="no readable files"):
        clean.load_folder(tmp_path)


def test_missing_column_skips_the_file(tmp_path):
    wb = Workbook()
    wb.active.append(["Date", "Product", "Qty", "Other"])
    wb.save(tmp_path / "a_x.xlsx")
    _, _, errors = clean.load_folder(folder(tmp_path, OK))
    assert len(errors) == 1 and "missing columns {'unit_price'}" in errors[0]


def test_report_content(tmp_path):
    good, rejected, _ = clean.load_folder(folder(tmp_path, OK, ["06/10/2026", "seal kit", 3, "9,90"], OK))
    path = report.build(good, rejected, ["bad.xlsx: broken"], tmp_path / "out" / "r.xlsx")
    wb = load_workbook(path)
    sheet = {ws.title: [[c.value for c in r] for r in ws.iter_rows()] for ws in wb}
    assert dict(sheet["Summary"][1:]) == {"Files processed": 1, "Clean rows": 2, "Rejected rows": 1, "Files skipped": 1,
                                          "Total revenue": 30.7, "Period": "2026-09-05 -> 2026-10-06"}
    assert sheet["Revenue by month"][:3] == [["month", "Paris", "Total"], ["2026-09", 1, 1], ["2026-10", 29.7, 29.7]]
    assert sheet["By product"] == [["product", "units", "revenue", "orders"], ["Seal Kit", 3, 29.7, 1], ["Pump", 1, 1, 1]]
    assert sheet["Rejected rows"][1][-1] == "duplicate line; " and sheet["Errors"][1] == ["bad.xlsx: broken"]
    assert len(sheet["Clean data"]) == 3 and len(wb["Revenue by month"]._charts) == 1
    assert wb["Summary"]["A1"].font.bold and wb["Summary"].freeze_panes == "A2"


def test_sample_files_are_readable_and_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(make_sample_data, "INPUT_DIR", tmp_path / "in")
    make_sample_data.main(seed=1)
    good, rejected, errors = clean.load_folder(tmp_path / "in")
    assert len(list((tmp_path / "in").glob("*.xlsx"))) == 9 and errors == []
    assert set(good["branch"]) == {"Paris", "Lyon", "Marseille"} and len(set(good["product"])) == 5
    assert rejected["reason"].str.contains("bad price").sum() == 9       # one 'n/a' price per file
    assert rejected["reason"].str.contains("bad date").sum() == 9        # one impossible date per file
    assert rejected["reason"].str.contains("duplicate").sum() >= 27      # three copied lines per file


def test_messy_name_keeps_the_product():
    rng = random.Random(0)
    names = {make_sample_data.messy_name("Seal kit", rng) for _ in range(50)}
    assert len(names) > 1 and {clean.normalise_product(n) for n in names} == {"Seal Kit"}


def test_make_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(make_sample_data, "INPUT_DIR", tmp_path)
    path = make_sample_data.make_file("lyon", make_sample_data.BRANCHES["lyon"], date(2026, 8, 1), random.Random(0))
    assert path.name == "lyon_2026_08.xlsx" and path.exists()


def test_command_line(tmp_path, monkeypatch):
    out = tmp_path / "out" / "r.xlsx"
    monkeypatch.setattr(sys, "argv", ["main", "--input", str(folder(tmp_path / "in", OK)), "--output", str(out)])
    main.main()
    assert out.exists()
