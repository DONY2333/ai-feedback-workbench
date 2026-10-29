"""批量分析评论，并把模型结果拼回原始数据。"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from src.model_client import FeedbackModelClient


ProgressCallback = Callable[[int, int], None]


def analyze_comments(
    comments: pd.DataFrame,
    client: FeedbackModelClient,
    progress_callback: ProgressCallback | None = None,
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    total = len(comments)

    for position, (_, row) in enumerate(comments.iterrows(), start=1):
        analysis, source = client.analyze(str(row["评论原文"]), str(row.get("产品", "")))
        record = row.to_dict()
        record.update(analysis)
        record["分析来源"] = source
        records.append(record)
        if progress_callback:
            progress_callback(position, total)

    return pd.DataFrame(records)

