"""Write the consolidated Excel report: data, pivots, chart, data-quality log."""
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

FILL = PatternFill("solid", fgColor="1F4E78")
FONT = Font(bold=True, color="FFFFFF")


def _style(ws) -> None:
    for c in ws[1]:
        c.fill, c.font = FILL, FONT
    for col in ws.columns:
        width = max(len(str(c.value or "")) for c in col[:300]) + 2
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(width, 45)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def build(good: pd.DataFrame, rejected: pd.DataFrame, errors: list[str], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    by_month = good.pivot_table(index="month", columns="branch", values="revenue", aggfunc="sum",
                                fill_value=0).round(2)
    by_month["Total"] = by_month.sum(axis=1)
    by_product = (good.groupby("product")
                  .agg(units=("quantity", "sum"), revenue=("revenue", "sum"), orders=("product", "size"))
                  .sort_values("revenue", ascending=False).round(2))
    summary = pd.DataFrame({"Metric": ["Files processed", "Clean rows", "Rejected rows", "Files skipped",
                                       "Total revenue", "Period"],
                            "Value": [good["source_file"].nunique(), len(good), len(rejected), len(errors),
                                      round(good["revenue"].sum(), 2),
                                      f"{good['date'].min():%Y-%m-%d} -> {good['date'].max():%Y-%m-%d}"]})
    data = good.assign(date=good["date"].dt.strftime("%Y-%m-%d"))

    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        by_month.reset_index().to_excel(xw, sheet_name="Revenue by month", index=False)
        by_product.reset_index().to_excel(xw, sheet_name="By product", index=False)
        data.to_excel(xw, sheet_name="Clean data", index=False)
        rejected.astype(str).to_excel(xw, sheet_name="Rejected rows", index=False)
        pd.DataFrame({"skipped_file_error": errors or ["none"]}).to_excel(xw, sheet_name="Errors", index=False)

    wb = load_workbook(out)
    for ws in wb.worksheets:
        _style(ws)
    ws = wb["Revenue by month"]
    chart = BarChart()
    chart.title, chart.y_axis.title = "Revenue by month and branch", "Revenue"
    n_branches = len(by_month.columns) - 1
    chart.add_data(Reference(ws, min_col=2, max_col=1 + n_branches, min_row=1, max_row=ws.max_row),
                   titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=ws.max_row))
    chart.width, chart.height = 18, 9
    ws.add_chart(chart, f"A{ws.max_row + 3}")
    wb.save(out)
    return out
