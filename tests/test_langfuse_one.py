"""Phase A 单条接入的纯本地测试：所有网络客户端均为替身。"""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from evaluation.analyze_results import analyze_detail
from evaluation.langfuse_one import calculate_scores, load_environment, run_one
from evaluation.run_evaluation import _is_evaluable_gold
from src.model_client import FeedbackModelClient, JSON_SCHEMA, ModelConfig, SYSTEM_PROMPT


def example():
    prediction = {
        "是否为有效产品反馈": True, "反馈类型": "Bug", "产品模块": "通知",
        "用户场景": "接收消息", "核心痛点": "没有提醒", "情绪倾向": "负向",
        "严重程度": "高", "原始评论证据": "完全收不到通知",
    }
    gold = pd.Series({
        "ID": "L001", "产品": "飞书", "评论原文": "完全收不到通知",
        **{f"gold_{key}": value for key, value in prediction.items()},
    })
    return gold, prediction


class FakeLangfuse:
    def __init__(self):
        self.generation = SimpleNamespace(
            trace_id="test-trace", id="test-generation", update=Mock(), score=Mock(),
        )
        self.observation_args = []
        self.flush = Mock()
        self.auth_check = Mock(return_value=True)
        self.get_trace_url = Mock(return_value="https://example.test/trace/test-trace")

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.observation_args.append(kwargs)
        yield self.generation


def formal_client(prediction):
    config = ModelConfig(mode="api", api_key="unit-test", model="unit-model", allow_mock_fallback=False)
    client = FeedbackModelClient(config)
    create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(prediction, ensure_ascii=False)),
    )]))
    client._client = SimpleNamespace(
        max_retries=0, chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    return client, create


@pytest.mark.parametrize("mismatch", [None, "是否为有效产品反馈", "反馈类型", "产品模块", "严重程度"])
def test_scores_reuse_formal_comparisons(mismatch):
    gold, prediction = example()
    wrong_values = {"是否为有效产品反馈": False, "反馈类型": "使用困难", "产品模块": "其他", "严重程度": "中"}
    if mismatch:
        prediction[mismatch] = wrong_values[mismatch]
    scores = calculate_scores(gold, prediction)
    detail = gold.to_dict()
    detail.update({f"pred_{key}": value for key, value in prediction.items()})
    detail["分析来源"] = "api"
    local = analyze_detail(pd.DataFrame([detail]))["all_fields_correct"].iloc[0]
    for score, field in {
        "valid_correct": "是否为有效产品反馈", "feedback_type_correct": "反馈类型",
        "product_module_correct": "产品模块", "severity_correct": "严重程度",
    }.items():
        assert scores[score] == int(gold[f"gold_{field}"] == prediction[field])
        assert scores[score] == int(local[f"correct_{field}"])
    assert scores["all_fields_correct"] == int(mismatch is None)
    assert scores["all_fields_correct"] == int(local["all_fields_correct"])


def test_not_applicable_severity_is_omitted_and_does_not_fail_all_fields():
    gold, prediction = example()
    gold["gold_严重程度"] = "不适用"
    assert not _is_evaluable_gold("严重程度", gold["gold_严重程度"])
    scores = calculate_scores(gold, prediction)
    assert "severity_correct" not in scores
    assert scores["all_fields_correct"] == 1


def test_one_generation_uses_official_prompt_schema_and_attached_scores():
    gold, prediction = example()
    client, create = formal_client(prediction)
    langfuse = FakeLangfuse()
    receipt = run_one(gold, client, langfuse)
    create.assert_called_once()
    request = create.call_args.kwargs
    assert request["messages"][0]["content"] == SYSTEM_PROMPT
    assert request["messages"][1]["content"] == "产品：飞书\n评论原文：完全收不到通知"
    assert request["response_format"]["json_schema"] == JSON_SCHEMA
    assert request["model"] == "unit-model"
    assert len(langfuse.observation_args) == 1
    observation = langfuse.observation_args[0]
    assert observation["as_type"] == "generation"
    assert observation["name"] == "comment-classification"
    assert observation["metadata"]["gold_id"] == "L001"
    assert observation["metadata"]["prompt_version"] == "v3"
    assert observation["metadata"]["evaluation_source"] == "gold_set_82"
    langfuse.generation.update.assert_called_once_with(output=prediction)
    assert receipt["model_calls"] == 1
    assert receipt["trace_id"] == "test-trace"
    assert receipt["generation_id"] == "test-generation"
    assert langfuse.generation.score.call_count == 5
    assert {call.kwargs["name"] for call in langfuse.generation.score.call_args_list} == set(receipt["scores"])
    assert all(call.kwargs["data_type"] == "BOOLEAN" and call.kwargs["value"] == 1
               for call in langfuse.generation.score.call_args_list)


def test_api_failure_stops_after_one_attempt_without_mock_or_scores(monkeypatch):
    gold, prediction = example()
    client, create = formal_client(prediction)
    create.side_effect = ConnectionError("test failure")
    mock = Mock(side_effect=AssertionError("Mock must not be called"))
    monkeypatch.setattr(client, "_mock_analysis", mock)
    langfuse = FakeLangfuse()
    with pytest.raises(RuntimeError, match="真实 API 分析失败"):
        run_one(gold, client, langfuse)
    create.assert_called_once()
    mock.assert_not_called()
    langfuse.generation.score.assert_not_called()


def test_parse_failure_does_not_write_scores():
    gold, prediction = example()
    client, create = formal_client(prediction)
    create.return_value.choices[0].message.content = "not json"
    langfuse = FakeLangfuse()
    with pytest.raises(RuntimeError):
        run_one(gold, client, langfuse)
    create.assert_called_once()
    langfuse.generation.score.assert_not_called()


def test_not_applicable_severity_is_not_written_to_generation():
    gold, prediction = example()
    gold["gold_严重程度"] = "不适用"
    client, _ = formal_client(prediction)
    telemetry = FakeLangfuse()
    receipt = run_one(gold, client, telemetry)
    assert telemetry.generation.score.call_count == 4
    assert "severity_correct" not in receipt["scores"]
    assert receipt["scores"]["all_fields_correct"] == 1


def test_non_api_source_is_rejected_without_scores(monkeypatch):
    gold, prediction = example()
    client, create = formal_client(prediction)
    monkeypatch.setattr(client, "analyze", lambda *_: (prediction, "mock"))
    telemetry = FakeLangfuse()
    with pytest.raises(RuntimeError, match="不是 api"):
        run_one(gold, client, telemetry)
    telemetry.generation.score.assert_not_called()
    create.assert_not_called()


@pytest.mark.parametrize("condition", ["mock", "fallback", "retry", "wrong_id"])
def test_unsafe_configuration_fails_before_request(condition):
    gold, prediction = example()
    client, create = formal_client(prediction)
    if condition == "mock":
        client.config.mode = "mock"
    elif condition == "fallback":
        client.config.allow_mock_fallback = True
    elif condition == "retry":
        client._client.max_retries = 2
    else:
        gold["ID"] = "L002"
    with pytest.raises(ValueError):
        run_one(gold, client, FakeLangfuse())
    create.assert_not_called()


def test_external_env_only_supplies_langfuse_and_preserves_system(monkeypatch, tmp_path):
    import evaluation.langfuse_one as module

    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("OPENAI_MODEL", "system-model")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "system-secret")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    path = tmp_path / "demo.env"
    path.write_text("OPENAI_MODEL=wrong-model\nLANGFUSE_PUBLIC_KEY=unit-public\nLANGFUSE_SECRET_KEY=wrong-secret\n", encoding="utf-8")
    load_environment(path)
    import os

    assert os.environ["OPENAI_MODEL"] == "system-model"
    assert os.environ["LANGFUSE_SECRET_KEY"] == "system-secret"
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "unit-public"


def test_main_injects_zero_retry_twenty_second_client_and_flushes(monkeypatch, tmp_path):
    import evaluation.langfuse_one as module
    import langfuse
    import openai

    gold, prediction = example()
    _, create = formal_client(prediction)
    api = SimpleNamespace(max_retries=0, chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    @contextmanager
    def fake_openai(**kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"] == 20.0
        yield api

    telemetry = FakeLangfuse()
    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    monkeypatch.setattr(langfuse, "get_client", lambda: telemetry)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "load_environment", lambda _: None)
    monkeypatch.setattr(module, "load_one_gold", lambda: gold)
    for key in ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "OPENAI_API_KEY", "OPENAI_MODEL"]:
        monkeypatch.setenv(key, "unit-test")
    assert module.main([]) == 0
    create.assert_called_once()
    telemetry.flush.assert_called_once()
    saved = json.loads((tmp_path / "evaluation/results/langfuse_phase_a/L001_test-trace.json").read_text(encoding="utf-8"))
    assert saved["scores"]["all_fields_correct"] == 1
