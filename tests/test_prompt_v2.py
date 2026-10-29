"""V2 taxonomy 与输出约束的纯本地回归测试。"""

import json
import re
from pathlib import Path

import pytest

from evaluation.run_evaluation import _normalize_gold_feedback_type
from src.model_client import JSON_SCHEMA, SYSTEM_PROMPT, FeedbackModelClient, ModelConfig
from src.schemas import FEEDBACK_TYPES, PRODUCT_MODULES, SEVERITIES, normalize_result


def test_v2_schema_has_fixed_taxonomy_and_unchanged_fields():
    properties = JSON_SCHEMA["schema"]["properties"]
    assert FEEDBACK_TYPES == ["Bug", "功能建议", "使用困难", "正向评价", "情绪/无效反馈"]
    assert SEVERITIES == ["高", "中", "低", "不适用"]
    assert len(set(PRODUCT_MODULES)) == 19
    assert properties["产品模块"]["enum"] == PRODUCT_MODULES
    assert set(properties) == {
        "是否为有效产品反馈", "反馈类型", "产品模块", "用户场景",
        "核心痛点", "情绪倾向", "严重程度", "原始评论证据",
    }


@pytest.mark.parametrize("feedback_type", ["正向评价", "情绪/无效反馈"])
def test_non_problem_feedback_forces_not_applicable(feedback_type):
    result = normalize_result(
        {"反馈类型": feedback_type, "严重程度": "高", "产品模块": "通知"}, "测试评论"
    )
    assert result["严重程度"] == "不适用"


def test_unknown_module_cannot_escape_taxonomy():
    result = normalize_result({"产品模块": "自定义模块"}, "测试评论")
    assert result["产品模块"] == "其他"


def test_vague_praise_is_not_valid_feedback_in_mock():
    result, source = FeedbackModelClient(ModelConfig(mode="mock")).analyze("很好用")
    assert source == "mock"
    assert result["是否为有效产品反馈"] is False
    assert result["反馈类型"] == "情绪/无效反馈"
    assert result["严重程度"] == "不适用"


def test_gold_feedback_normalization_matches_v2():
    assert _normalize_gold_feedback_type("情绪/无效反馈") == "情绪/无效反馈"
    assert _normalize_gold_feedback_type("情绪或无效反馈") == "情绪/无效反馈"


def test_v2_historical_snapshot_preserves_schema():
    path = Path(__file__).resolve().parents[1] / "evaluation/prompts/prompt_v2.md"
    snapshot = path.read_text(encoding="utf-8")
    prompt = re.search(r"```text\n(.*?)\n```", snapshot, re.S).group(1)
    schema = json.loads(re.search(r"```json\n(.*?)\n```", snapshot, re.S).group(1))
    assert "Prompt Version：`v2`" in snapshot
    assert prompt != SYSTEM_PROMPT  # V2 是历史快照；当前 Prompt 已升级为 V3。
    assert schema == JSON_SCHEMA
