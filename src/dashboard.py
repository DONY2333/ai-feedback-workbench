"""Streamlit 看板需要的轻量数据汇总。"""

from __future__ import annotations

import pandas as pd


def count_by(data: pd.DataFrame, column: str) -> pd.DataFrame:
    if data.empty or column not in data.columns:
        return pd.DataFrame(columns=[column, "数量"])
    return data[column].fillna("未识别").value_counts().rename_axis(column).reset_index(name="数量")


def frequent_issues(data: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    if data.empty or "核心痛点" not in data.columns:
        return pd.DataFrame(columns=["核心痛点", "评论数", "涉及产品", "最高严重程度"])
    valid = data[
        data["核心痛点"].notna()
        & ~data["核心痛点"].isin(["", "未识别", "评论未提供可归类的具体问题"])
    ].copy()
    if valid.empty:
        return pd.DataFrame(columns=["核心痛点", "评论数", "涉及产品", "最高严重程度"])

    severity_order = {"低": 1, "中": 2, "高": 3, "严重": 4}
    valid["_严重度值"] = valid["严重程度"].map(severity_order).fillna(0)
    summary = (
        valid.groupby("核心痛点", as_index=False)
        .agg(
            评论数=("评论原文", "size"),
            涉及产品=("产品", lambda values: "、".join(sorted(set(map(str, values))))),
            _最高严重度=("_严重度值", "max"),
        )
        .sort_values(["评论数", "_最高严重度"], ascending=False)
        .head(limit)
    )
    reverse_severity = {value: key for key, value in severity_order.items()}
    summary["最高严重程度"] = summary["_最高严重度"].map(reverse_severity).fillna("低")
    return summary.drop(columns="_最高严重度").reset_index(drop=True)


def product_comparison(data: pd.DataFrame) -> pd.DataFrame:
    """按产品对比反馈类型；不限于固定产品名，也兼容“飞书/Lark”。"""
    if data.empty or not {"产品", "反馈类型"}.issubset(data.columns):
        return pd.DataFrame()
    comparison = pd.crosstab(data["产品"], data["反馈类型"])
    comparison["总计"] = comparison.sum(axis=1)
    return comparison.sort_values("总计", ascending=False)


def comments_for_issue(data: pd.DataFrame, issue: str) -> pd.DataFrame:
    columns = [
        column for column in ["产品", "平台", "评论原文", "反馈类型", "产品模块", "情绪倾向", "严重程度", "原始评论证据", "来源URL"]
        if column in data.columns
    ]
    if not issue or data.empty:
        return pd.DataFrame(columns=columns)
    return data.loc[data["核心痛点"].eq(issue), columns].reset_index(drop=True)

