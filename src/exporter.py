"""分析结果导出为 CSV 和 Excel。"""

from __future__ import annotations

from copy import copy
from datetime import datetime
from pathlib import Path

import pandas as pd


def export_results(data: pd.DataFrame, output_dir: str | Path = "outputs") -> dict[str, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = directory / f"feedback_analysis_{timestamp}.csv"
    excel_path = directory / f"feedback_analysis_{timestamp}.xlsx"

    data.to_csv(csv_path, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        data.to_excel(writer, sheet_name="分析结果", index=False)
        worksheet = writer.book["分析结果"]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for cell in worksheet[1]:
            header_font = copy(cell.font)
            header_font.bold = True
            header_font.color = "FFFFFF"
            cell.font = header_font
            header_fill = copy(cell.fill)
            header_fill.fill_type = "solid"
            header_fill.fgColor.rgb = "1F4E78"
            cell.fill = header_fill
        for column_cells in worksheet.columns:
            sample_lengths = [len(str(cell.value or "")) for cell in list(column_cells)[:200]]
            width = min(max(sample_lengths + [8]) + 2, 60)
            worksheet.column_dimensions[column_cells[0].column_letter].width = width

    return {"csv": csv_path, "excel": excel_path}
