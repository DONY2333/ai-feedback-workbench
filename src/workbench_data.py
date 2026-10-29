"""演示页面的只读文件适配：不导入模型、评测或 Langfuse 客户端。"""

from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
E1 = "analysis/results/e1_offline"
E2 = "analysis/results/e2_product_insights"
HOLDOUT = "evaluation/results/v3_holdout/analysis"
R3 = "evaluation/results/langfuse_phase_c2/r3/receipt.json"
FIELDS = ["是否为有效产品反馈", "反馈类型", "产品模块", "严重程度"]
FIELD_LABELS = dict(zip(FIELDS, ["Valid", "Feedback Type", "Product Module", "Severity"]))


class OfflineResultError(ValueError):
    """给页面显示的非敏感文件错误。"""


@st.cache_data(show_spinner=False, max_entries=40)
def _read_file(path: str, modified: int, size: int):
    # mtime/size 参与缓存键；文件缺失不缓存，恢复后可直接刷新。
    source = Path(path)
    if source.suffix == ".csv":
        return pd.read_csv(source, keep_default_na=False)
    if source.suffix == ".xlsx":
        return pd.read_excel(source).fillna("")
    if source.suffix == ".json":
        return json.loads(source.read_text(encoding="utf-8"))
    return source.read_text(encoding="utf-8")


def read_result(relative: str, required=()):
    path = ROOT / relative
    try:
        stat = path.stat()
        result = _read_file(str(path), stat.st_mtime_ns, stat.st_size)
        if required:
            keys = result.columns if isinstance(result, pd.DataFrame) else result.keys()
            if set(required) - set(keys):
                raise ValueError("结果缺少必需字段")
        return result
    except (OSError, ValueError, ImportError, TypeError, AttributeError, zipfile.BadZipFile):
        raise OfflineResultError(f"对应离线分析结果未找到或无法读取：{relative}。请恢复已有结果文件后刷新；本页面不会自动运行分析。") from None


def issue_evidence(package: dict, issue_id: str) -> list[dict]:
    """逐一按原 ID 找证据，不只返回代表评论。"""
    try:
        issues = {i["issue_id"]: i for i in package["issues"]}
        feedback = {r["feedback_id"]: r for r in package["feedback_mapping"]}
        issue = issues[issue_id]
        ids = issue["evidence_ids"]
        members = [feedback[i] for i in ids]
        if len(ids) != len(set(ids)) or len(ids) != issue["evidence_count"]:
            raise ValueError
        if any(r["issue_id"] != issue_id or (r["product"], r["product_module"]) != (issue["product"], issue["product_module"]) for r in members):
            raise ValueError
        return members
    except (KeyError, TypeError, ValueError):
        raise OfflineResultError("Issue 与原始证据映射不完整，请检查已有 E1 输出；未补造任何证据。") from None


def historical_dev_table(summaries: dict[str, pd.DataFrame]) -> pd.DataFrame:
    records = []
    for version, frame in summaries.items():
        rates = frame.set_index("字段名")["一致率"]
        records.append({"Version": version.upper(), **{FIELD_LABELS[f]: f"{float(rates[f]):.2%}" if f in rates else "未保存" for f in FIELDS}})
    return pd.DataFrame(records)


def holdout_table(metrics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for field in FIELDS:
        record = metrics.loc[metrics["field"] == field]
        if record.empty:
            continue
        row = record.iloc[0]
        rows.append({"Field": FIELD_LABELS[field], **{
            label: f"{float(row[key]):.2%}" if row[key] != "" else "未保存"
            for key, label in [("accuracy", "Accuracy"), ("macro_f1", "Macro-F1"), ("weighted_f1", "Weighted-F1")]
        }, "Samples": int(row["n_samples"])})
    return pd.DataFrame(rows)


def failed_samples(detail: pd.DataFrame, field: str) -> pd.DataFrame:
    """仅筛选已保存 match，不重算评测；严重程度跳过 Gold 不适用。"""
    required = ["ID", "评论原文", "gold_" + field, "pred_" + field, "match_" + field]
    if set(required) - set(detail.columns):
        return pd.DataFrame()
    column = detail["match_" + field].astype(str).str.lower()
    mask = column.isin(["false", "0", "0.0"])
    if field == "严重程度":
        mask &= detail["gold_严重程度"] != "不适用"
    return detail.loc[mask, required].rename(columns={"评论原文": "原始评论", "gold_" + field: "Gold", "pred_" + field: "Prediction", "match_" + field: "Match"})


def markdown_section(report: str, title: str) -> str:
    marker = "## " + title
    if marker not in report:
        return ""
    return report.split(marker, 1)[1].split("\n## ", 1)[0].strip()
