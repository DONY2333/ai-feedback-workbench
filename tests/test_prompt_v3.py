"""V3 的纯本地约束测试；不调用 API，不验证真实模型的分类效果。"""

import inspect
import json
import re
from pathlib import Path

import pytest

from src.model_client import FeedbackModelClient, JSON_SCHEMA, SYSTEM_PROMPT


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "evaluation/prompts"


def read_snapshot(version):
    return (PROMPTS_DIR / f"prompt_{version}.md").read_text(encoding="utf-8")


def fenced_block(snapshot, language):
    return re.search(rf"```{language}\n(.*?)\n```", snapshot, re.S).group(1)


def test_v3_snapshot_matches_live_prompt_and_schema():
    snapshot = read_snapshot("v3")
    assert "Prompt Version: v3" in snapshot
    assert "OPENAI_MODEL" in snapshot
    assert fenced_block(snapshot, "text") == SYSTEM_PROMPT
    assert json.loads(fenced_block(snapshot, "json")) == JSON_SCHEMA


def test_schema_and_user_template_are_unchanged_from_v2():
    v2, v3 = read_snapshot("v2"), read_snapshot("v3")
    assert fenced_block(v2, "json") == fenced_block(v3, "json")
    template = fenced_block(v2, "python")
    assert fenced_block(v3, "python") == template
    assert template in inspect.getsource(FeedbackModelClient._call_api)


@pytest.mark.parametrize("rule", [
    "功能完全失效、行为异常、明显违背正常预期，判为 Bug",
    "功能仍然存在并可使用，只是难找、难操作、效果较差、体验不佳，优先判为使用困难",
    "“完全收不到通知”→ Bug",
    "“通知声音太小”→ 使用困难",
    "“聊天记录搜索很难找到”→ 使用困难",
    "“点击后报错/无法使用”→ Bug",
    "以后文最新结论作为主要依据",
    "已明确排除的问题，不再作为 Bug 判断依据，也不参与下述多反馈优先级排序",
    "即使句子很短、语法不完整、语言混杂，也可以算有效产品反馈",
    "“上传 Base 太麻烦”“登录一直失败”“通知声音太小”均为有效",
    "只有完全无法定位具体产品信息的笼统表达",
    "“不错，但应该还有更好的”均为无效",
    "网络错误、连接失败、卡顿、崩溃、加载异常 → 性能/稳定性",
    "聊天归档、会话整理、消息管理 → 聊天/消息",
    "通知推送、后台提醒、消息提醒 → 通知",
    "登录、验证码、账号验证 → 账号/登录/验证",
    "不因“用户想要”就自动判中或高",
    "非核心增强型建议通常为低；核心工作流明显受阻可判中；核心能力基本不可用或持续失败才判高",
])
def test_v3_contains_targeted_rule_constraints(rule):
    assert rule in SYSTEM_PROMPT


def test_latest_conclusion_rule_precedes_multi_feedback_priority():
    assert SYSTEM_PROMPT.index("以后文最新结论作为主要依据") < SYSTEM_PROMPT.index(
        "主要反馈无法区分时"
    )


def test_v2_severity_definitions_are_preserved():
    old_prompt = fenced_block(read_snapshot("v2"), "text")
    severity_section = old_prompt.split("四、严重程度", 1)[1].split("五、其余字段", 1)[0]
    for line in severity_section.splitlines()[1:]:
        if line.strip():
            assert line in SYSTEM_PROMPT
