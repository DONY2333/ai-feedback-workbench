"""读取并清洗 data 文件夹中的 Excel 评论数据。"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


COMMENT_ALIASES = ["评论原文", "评论", "用户评论", "反馈内容", "内容", "review", "comment", "feedback"]
PRODUCT_ALIASES = ["产品", "产品名称", "应用", "app", "product"]


def _find_column(columns: list[object], aliases: list[str]) -> object | None:
    normalized = {str(column).strip().lower(): column for column in columns}
    for alias in aliases:
        if alias.lower() in normalized:
            return normalized[alias.lower()]
    for column in columns:
        name = str(column).strip().lower()
        # 只对短列名做模糊匹配，避免把“公开评论第一批样本”这类说明页标题
        # 误识别为真正的评论字段。
        if len(name) <= 14 and any(alias.lower() in name for alias in aliases):
            return column
    return None


def _normalize_text(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    return re.sub(r"\s+", " ", text).strip()


def find_excel_files(data_dir: str | Path = "data") -> list[Path]:
    directory = Path(data_dir)
    if not directory.exists():
        return []
    files = [*directory.glob("*.xlsx"), *directory.glob("*.xls")]
    return sorted(path for path in files if not path.name.startswith("~$"))


def load_excel_comments(data_dir: str | Path = "data") -> tuple[pd.DataFrame, dict[str, int]]:
    """读取所有包含评论列的工作表，统一为 `评论原文`，并去空、去重。"""
    frames: list[pd.DataFrame] = []
    skipped_sheets = 0

    for file_path in find_excel_files(data_dir):
        workbook = pd.ExcelFile(file_path)
        for sheet_name in workbook.sheet_names:
            frame = pd.read_excel(file_path, sheet_name=sheet_name)
            comment_column = _find_column(frame.columns.tolist(), COMMENT_ALIASES)
            if comment_column is None:
                skipped_sheets += 1
                continue

            product_column = _find_column(frame.columns.tolist(), PRODUCT_ALIASES)
            frame = frame.copy()
            if comment_column != "评论原文":
                frame = frame.rename(columns={comment_column: "评论原文"})
            if product_column is not None and product_column != "产品":
                frame = frame.rename(columns={product_column: "产品"})
            if "产品" not in frame.columns:
                frame["产品"] = "未知产品"

            frame["来源文件"] = file_path.name
            frame["来源工作表"] = sheet_name
            frame["来源行号"] = range(2, len(frame) + 2)
            frames.append(frame)

    if not frames:
        empty = pd.DataFrame(columns=["产品", "评论原文", "来源文件", "来源工作表", "来源行号"])
        return empty, {"读取行数": 0, "清洗后行数": 0, "空评论数": 0, "重复评论数": 0, "跳过工作表数": skipped_sheets}

    combined = pd.concat(frames, ignore_index=True, sort=False)
    loaded_count = len(combined)
    combined["评论原文"] = combined["评论原文"].map(_normalize_text)
    combined["产品"] = combined["产品"].map(_normalize_text).replace("", "未知产品")

    empty_mask = combined["评论原文"].eq("")
    empty_count = int(empty_mask.sum())
    combined = combined.loc[~empty_mask].copy()

    combined["_去重键"] = (
        combined["产品"].str.lower().str.replace(r"\s+", "", regex=True)
        + "|"
        + combined["评论原文"].str.lower().str.replace(
            r"[\s\.,!?，。！？、；;：:\"“”'‘’（）()\[\]{}<>《》…—_-]+",
            "",
            regex=True,
        )
    )
    duplicate_count = int(combined.duplicated("_去重键", keep="first").sum())
    combined = combined.drop_duplicates("_去重键", keep="first").drop(columns="_去重键")
    combined = combined.reset_index(drop=True)
    combined.insert(0, "分析序号", range(1, len(combined) + 1))

    stats = {
        "读取行数": loaded_count,
        "清洗后行数": len(combined),
        "空评论数": empty_count,
        "重复评论数": duplicate_count,
        "跳过工作表数": skipped_sheets,
    }
    return combined, stats
