"""Phase B：只上传人工 Dev Gold，不调用模型、不运行 Experiment。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.langfuse_one import load_environment
from evaluation.run_evaluation import load_gold_set, split_ids, validate_ids
from src.schemas import FEEDBACK_TYPES, PRODUCT_MODULES, SEVERITIES

DATASET_NAME = "feedback-dev-20"
GOLD_FIELDS = ["是否为有效产品反馈", "反馈类型", "产品模块", "核心痛点", "严重程度"]


def build_items(dev: pd.DataFrame) -> list[dict]:
    """独立验证全部 20 条后映射；任何缺失都不能进入上传阶段。"""
    validate_ids(dev, split_ids("dev"))
    required = ["产品", "评论原文", *[f"gold_{field}" for field in GOLD_FIELDS]]
    for column in required:
        if column not in dev.columns:
            raise ValueError(f"缺少字段：{column}")
        if dev[column].isna().any() or dev[column].map(lambda x: not str(x).strip()).any():
            raise ValueError(f"字段存在空值：{column}")

    items = []
    for row in dev.set_index("ID").loc[split_ids("dev")].reset_index().to_dict("records"):
        gold = {field: row[f"gold_{field}"] for field in GOLD_FIELDS}
        if not isinstance(gold["是否为有效产品反馈"], bool):
            raise ValueError("Gold 有效性必须由正式读取逻辑标准化为 bool")
        for field, labels in [("反馈类型", FEEDBACK_TYPES), ("产品模块", PRODUCT_MODULES), ("严重程度", SEVERITIES)]:
            if gold[field] not in labels:
                raise ValueError(f"{row['ID']} 的 {field} 不属于正式 taxonomy")
        metadata = {"gold_id": row["ID"], "split": "dev", "evaluation_source": "gold_set_82"}
        if "原始Excel行号" in row:
            metadata["excel_row"] = int(row["原始Excel行号"])
        items.append({
            "id": str(uuid5(NAMESPACE_URL, f"ai-feedback-workbench/{DATASET_NAME}/{row['ID']}")),
            "input": {"product": row["产品"], "comment": row["评论原文"]},
            "expected_output": gold,
            "metadata": metadata,
        })
    return items


def check_existing(dataset, expected: list[dict]) -> list[dict]:
    """返回缺少的条目；已有内容不一致时拒绝覆盖。"""
    by_gold = {item["metadata"]["gold_id"]: item for item in expected}
    seen = set()
    for remote in dataset.items:
        metadata = remote.metadata or {}
        gold_id = metadata.get("gold_id") if isinstance(metadata, dict) else None
        if gold_id not in by_gold or gold_id in seen:
            raise ValueError("服务端存在范围外或重复 Gold ID，未执行写入")
        seen.add(gold_id)
        target = by_gold[gold_id]
        status = getattr(remote.status, "value", remote.status)
        if (status != "ACTIVE" or remote.input != target["input"]
                or remote.expected_output != target["expected_output"]
                or any(metadata.get(k) != v for k, v in target["metadata"].items())):
            raise ValueError(f"服务端 {gold_id} 内容或状态冲突，拒绝覆盖")
    return [item for item in expected if item["metadata"]["gold_id"] not in seen]


def upload_dev(client, dev: pd.DataFrame) -> dict:
    """先完成本地/远端预检，再写缺失项，最后从服务端完整回读。"""
    from langfuse.api import NotFoundError

    expected = build_items(dev)  # 在任何 Langfuse 请求之前校验。
    try:
        dataset = client.get_dataset(DATASET_NAME)
    except NotFoundError:
        dataset = None
    missing = expected if dataset is None else check_existing(dataset, expected)
    # SDK 的 Item ID 在整个项目中唯一；不允许 upsert 意外覆盖其他 Dataset。
    for item in missing:
        try:
            client.api.dataset_items.get(item["id"])
        except NotFoundError:
            continue
        raise ValueError("待创建 Item ID 已存在，拒绝 upsert 覆盖；请重新核验服务端")
    if dataset is None:
        client.create_dataset(
            name=DATASET_NAME,
            description="Official human Gold Dev set: L001-L020. No model execution.",
            metadata={"split": "dev", "evaluation_source": "gold_set_82"},
        )
    for item in missing:
        client.create_dataset_item(dataset_name=DATASET_NAME, **item)
    verified = client.get_dataset(DATASET_NAME)
    if check_existing(verified, expected):
        raise RuntimeError("服务端回读缺少条目，未通过完整性核验")
    return {
        "dataset_name": DATASET_NAME, "dataset_id": verified.id,
        "written": len(missing), "skipped": len(expected) - len(missing),
        "verified_count": len(verified.items), "model_api_calls": 0,
        "example": {k: v for k, v in expected[0].items() if k != "id"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--langfuse-env", type=Path, help="复用 Phase A 外部 LANGFUSE_* 配置")
    args = parser.parse_args()
    dev, _ = load_gold_set(split="dev")
    build_items(dev)  # 本地失败时甚至不初始化 Langfuse。
    load_environment(args.langfuse_env)
    if not all(os.getenv(key) for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")):
        raise ValueError("缺少 Langfuse 配置；未打印任何密钥")
    from langfuse import get_client

    client = get_client()
    try:
        receipt = upload_dev(client, dev)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
