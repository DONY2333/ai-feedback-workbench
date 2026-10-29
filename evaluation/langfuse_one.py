"""Phase A：只调用一条正式 Gold；复用 V3 客户端和本地指标口径。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.analyze_results import analyze_detail  # noqa: E402
from evaluation.analyze_results import compare_fields  # noqa: E402
from evaluation.run_evaluation import load_gold_set  # noqa: E402
from src.model_client import FeedbackModelClient, ModelConfig  # noqa: E402


GOLD_ID = "L001"
PROMPT_VERSION = "v3"
TIMEOUT_SECONDS = 20.0  # 与已跑通的 langfuse_test/llm_trace.py 保持一致。
SCORE_FIELDS = {
    "valid_correct": "是否为有效产品反馈",
    "feedback_type_correct": "反馈类型",
    "product_module_correct": "产品模块",
    "severity_correct": "严重程度",
}


def load_environment(langfuse_env: Path | None = None) -> None:
    """系统环境优先；外部 demo 文件只提供 LANGFUSE_*，不覆盖正式模型配置。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    if langfuse_env is not None:
        if not langfuse_env.is_file():
            raise ValueError("指定的 Langfuse 配置文件不存在")
        for key, value in dotenv_values(langfuse_env).items():
            if key.startswith("LANGFUSE_") and value:
                os.environ.setdefault(key, value)


def load_one_gold() -> pd.Series:
    # 仅复用 Excel 读取/标签标准化；没有调用批量 run_evaluation()。
    dev, _ = load_gold_set(split="dev")
    selected = dev.loc[dev["ID"].eq(GOLD_ID)]
    if len(selected) != 1:
        raise ValueError(f"必须存在且仅存在一条 {GOLD_ID}")
    return selected.iloc[0]


def calculate_scores(
    gold: pd.Series, prediction: dict, *, historical_version: str | None = None,
) -> dict[str, int]:
    """直接取 analyze_results 的逐字段/整条比较结果，不另写一套判定规则。"""
    record = gold.to_dict()
    record.update({f"pred_{field}": prediction[field] for field in SCORE_FIELDS.values()})
    record.update({"分析来源": "api", "错误信息": ""})
    compared = compare_fields(
        pd.DataFrame([record]), historical_version=historical_version,
    ).iloc[0]
    scores = {
        name: int(compared[f"correct_{field}"])
        for name, field in SCORE_FIELDS.items()
        if compared[f"applicable_{field}"]
    }
    scores["all_fields_correct"] = int(compared["all_fields_correct"])
    return scores


def run_one(gold: pd.Series, client: FeedbackModelClient, langfuse) -> dict:
    """一个 Generation、一次 analyze；失败直接退出，不循环或重试模型。"""
    if client.config.mode != "api" or client.config.allow_mock_fallback:
        raise ValueError("必须启用 api 且禁用 Mock fallback")
    if not client.config.api_key:
        raise ValueError("未配置 OPENAI_API_KEY")
    if client._client is None or client._client.max_retries != 0:
        raise ValueError("必须先注入 max_retries=0 的正式 API 客户端")
    if gold["ID"] != GOLD_ID:
        raise ValueError(f"Phase A 仅允许 {GOLD_ID}")

    metadata = {
        "gold_id": str(gold["ID"]), "product": str(gold["产品"]),
        "prompt_version": PROMPT_VERSION, "evaluation_source": "gold_set_82",
        "model": client.config.model, "max_retries": 0, "timeout_seconds": TIMEOUT_SECONDS,
    }
    with langfuse.start_as_current_observation(
        as_type="generation", name="comment-classification",
        model=client.config.model, metadata=metadata,
        input={"产品": str(gold["产品"]), "评论原文": str(gold["评论原文"])},
    ) as generation:
        prediction, source = client.analyze(str(gold["评论原文"]), str(gold["产品"]))
        if source != "api":
            raise RuntimeError("结果来源不是 api，不写入正确率 Score")
        generation.update(output=prediction)
        scores = calculate_scores(gold, prediction)
        for name, value in scores.items():
            generation.score(
                name=name, value=value, data_type="BOOLEAN",
                comment="Compared with human Gold using evaluation.analyze_results",
                metadata=metadata,
            )
        receipt = {
            **metadata, "trace_id": generation.trace_id, "generation_id": generation.id,
            "prediction": prediction, "scores": scores, "analysis_source": source,
            "model_calls": 1, "severity_applicable": "severity_correct" in scores,
            "local_evaluation_consistent": True,
        }
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只对 L001 发送一次真实模型请求，写入 V3 Generation 和多个 Score")
    parser.add_argument("--langfuse-env", type=Path, help="可选：已有 demo 的 .env；仅读取 LANGFUSE_*，不复制密钥")
    args = parser.parse_args(argv)
    load_environment(args.langfuse_env)
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        if not os.getenv(name):
            raise ValueError(f"缺少 {name}")
    config = ModelConfig.from_environment("api", allow_mock_fallback=False)
    if not config.api_key:
        raise ValueError("未配置 OPENAI_API_KEY")
    if not os.getenv("OPENAI_MODEL"):
        raise ValueError("必须通过 OPENAI_MODEL 指定正式模型")
    gold = load_one_gold()

    from langfuse import get_client
    from openai import OpenAI

    langfuse = get_client()  # 复用已经跑通的 SDK 初始化和 observation/score API。
    if not langfuse.auth_check():
        raise RuntimeError("Langfuse 认证失败；未调用模型")
    try:
        with OpenAI(
            api_key=config.api_key, base_url=config.base_url,
            max_retries=0, timeout=TIMEOUT_SECONDS,
        ) as api_client:
            client = FeedbackModelClient(config)
            client._client = api_client  # 与现有 test_api_connection.py 的注入方式相同。
            receipt = run_one(gold, client, langfuse)
    finally:
        langfuse.flush()

    # 保存本次结果，后续检查 Langfuse 可用该 trace_id；不要因入库延迟再调用模型。
    receipt["trace_url"] = langfuse.get_trace_url(trace_id=receipt["trace_id"])
    receipt["langfuse_status"] = "flushed; server visibility must be verified separately"
    output_dir = PROJECT_ROOT / "evaluation/results/langfuse_phase_a"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{GOLD_ID}_{receipt['trace_id']}.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(f"本次单条结果：{path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # 不输出异常正文：代理/认证错误可能含请求信息或凭据。
        print(f"Phase A 失败：{type(exc).__name__}；已停止，不重试模型。", file=sys.stderr)
        raise SystemExit(1) from None
