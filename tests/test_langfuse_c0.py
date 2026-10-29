"""C0 离线回归；所有模型及 Langfuse 网络调用均为假对象。"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from evaluation.langfuse_c0 import (
    HistoricalClient, load_snapshot, run_c0, select_l001, validate_raw, verify_runs,
)
from evaluation.langfuse_dataset import build_items
from evaluation.langfuse_one import calculate_scores
from evaluation.run_evaluation import load_gold_set
from src.model_client import JSON_SCHEMA, SYSTEM_PROMPT, ModelConfig


def prediction(version):
    return {
        "是否为有效产品反馈": False,
        "反馈类型": "情绪或无效反馈" if version == "v1" else "情绪/无效反馈",
        "产品模块": "其他", "用户场景": "未识别", "核心痛点": "笼统评价",
        "情绪倾向": "正向", "严重程度": "低" if version == "v1" else "不适用",
        "原始评论证据": "使用记录来看",
    }


@pytest.fixture
def config():
    return ModelConfig(mode="api", model="gpt-6.1-sol", api_key="test-placeholder", allow_mock_fallback=False)


@pytest.fixture
def fake_api():
    api = Mock(max_retries=0)
    responses = [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps(prediction(v), ensure_ascii=False)))]) for v in ("v1", "v2", "v3")]
    api.chat.completions.create.side_effect = responses
    return api


@pytest.fixture
def lf():
    dev, _ = load_gold_set(split="dev")
    items = [SimpleNamespace(**x, status="ACTIVE", dataset_id="dataset-test") for x in build_items(dev)]
    client = MagicMock()
    client.get_dataset.return_value = SimpleNamespace(items=items)
    client.api.experiments.list.return_value = SimpleNamespace(data=[])
    generations = []
    for i in range(3):
        generation = Mock(trace_id=f"trace-{i}", id=f"generation-{i}")
        context = MagicMock()
        context.__enter__.return_value = generation
        generations.append(context)
    client.start_as_current_observation.side_effect = generations
    def run_experiment(**kwargs):
        assert len(kwargs["data"]) == 1
        assert kwargs["max_concurrency"] == 1
        output = kwargs["task"](item=kwargs["data"][0])
        return SimpleNamespace(experiment_id=kwargs["name"], item_results=[SimpleNamespace(output=output)])
    client.run_experiment.side_effect = run_experiment
    return client


def test_snapshots_are_historical_and_distinct():
    v1, v2, v3 = [load_snapshot(v) for v in ("v1", "v2", "v3")]
    assert len({v1.sha256, v2.sha256, v3.sha256}) == 3
    assert len({v1.system, v2.system, v3.system}) == 3
    assert "enum" not in v1.schema["schema"]["properties"]["产品模块"]
    assert v1.schema["schema"]["properties"]["严重程度"]["enum"] == ["低", "中", "高", "严重"]
    assert len(v2.schema["schema"]["properties"]["产品模块"]["enum"]) == 19
    assert v3.schema == JSON_SCHEMA
    assert v3.system == SYSTEM_PROMPT
    assert v1.user_template == v2.user_template == v3.user_template


def test_exactly_three_l001_calls_and_official_scores(lf, config, fake_api, tmp_path):
    receipt = run_c0(lf, config, fake_api, tmp_path / "receipt.json")
    assert receipt["model_api_calls"] == 3
    assert fake_api.chat.completions.create.call_count == 3
    assert len(receipt["runs"]) == 3
    assert lf.run_experiment.call_count == 3
    for version, call, record in zip(("v1", "v2", "v3"), fake_api.chat.completions.create.call_args_list, receipt["runs"]):
        snapshot = load_snapshot(version)
        assert call.kwargs["messages"][0]["content"] == snapshot.system
        assert call.kwargs["response_format"]["json_schema"] == snapshot.schema
        assert call.kwargs["model"] == "gpt-6.1-sol"
        assert record["gold_id"] == "L001"
        assert record["run_name"] == f"feedback-dev-{version}"
        assert record["scores"] == {"valid_correct": 1, "feedback_type_correct": 1,
                                    "product_module_correct": 1, "all_fields_correct": 1}
        assert "severity_correct" not in record["scores"]
    assert receipt["runs"][0]["raw_output"]["反馈类型"] == "情绪或无效反馈"
    assert receipt["runs"][0]["normalized_output"]["反馈类型"] == "情绪或无效反馈"
    assert receipt["runs"][0]["normalized_output"]["严重程度"] == "低"
    assert receipt["runs"][0]["normalization_changes"] == {}
    for record in receipt["runs"]:
        assert record["score_input"] == record["normalized_output"]
        assert record["normalization_version"].startswith(record["prompt_version"])
        assert record["normalization_reproduction"] == "partial"
        assert record["scoring_version"] == "historical-exact-match-v1"
    assert json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8")) == receipt
    with pytest.raises(ValueError, match="回执"):
        run_c0(lf, config, fake_api, tmp_path / "receipt.json")
    assert fake_api.chat.completions.create.call_count == 3


@pytest.mark.parametrize("mode,fallback,retries", [("mock", False, 0), ("api", True, 0), ("api", False, 2)])
def test_invalid_client_config_rejected(config, mode, fallback, retries):
    config.mode, config.allow_mock_fallback = mode, fallback
    with pytest.raises(ValueError):
        HistoricalClient(config, Mock(max_retries=retries), load_snapshot("v1"))


def test_one_request_per_version(config, fake_api):
    client = HistoricalClient(config, fake_api, load_snapshot("v1"))
    client.analyze("使用记录来看", "飞书/Lark")
    with pytest.raises(RuntimeError):
        client.analyze("使用记录来看", "飞书/Lark")
    assert fake_api.chat.completions.create.call_count == 1


def test_api_error_stops_without_retry(lf, config, fake_api, tmp_path):
    fake_api.chat.completions.create.side_effect = RuntimeError("offline")
    path = tmp_path / "receipt.json"
    with pytest.raises(RuntimeError):
        run_c0(lf, config, fake_api, path)
    assert fake_api.chat.completions.create.call_count == 1
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["model_api_calls"] == 1
    assert saved["runs"][0]["status"] == "failed"
    assert "offline" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("change", ["missing", "enum", "type"])
def test_invalid_response_is_not_silently_normalized(change):
    raw = prediction("v2")
    if change == "missing":
        raw.pop("核心痛点")
    elif change == "enum":
        raw["反馈类型"] = "unknown"
    else:
        raw["是否为有效产品反馈"] = "False"
    with pytest.raises(ValueError):
        validate_raw(raw, load_snapshot("v2"))


def test_dataset_mismatch_fails_before_model(lf, config, fake_api, tmp_path):
    lf.get_dataset.return_value.items[0].input = {}
    with pytest.raises(ValueError):
        run_c0(lf, config, fake_api, tmp_path / "receipt.json")
    fake_api.chat.completions.create.assert_not_called()


def test_existing_run_aborts_before_model(lf, config, fake_api, tmp_path):
    lf.api.experiments.list.return_value = SimpleNamespace(data=[SimpleNamespace(name="feedback-dev-v1")])
    with pytest.raises(ValueError, match="已存在"):
        run_c0(lf, config, fake_api, tmp_path / "receipt.json")
    fake_api.chat.completions.create.assert_not_called()


def test_all_fields_uses_existing_phase_a_rules(lf):
    _, gold = select_l001(lf)
    gold["ID"] = "L001"
    raw = prediction("v3")
    raw["产品模块"] = "通知"
    raw["严重程度"] = "高"
    scores = calculate_scores(gold, raw)
    assert scores["product_module_correct"] == 0
    assert scores["all_fields_correct"] == 0
    assert "severity_correct" not in scores


def test_verify_is_read_only(lf, config, fake_api, tmp_path):
    receipt = run_c0(lf, config, fake_api, tmp_path / "receipt.json")
    experiments, items, observations, scores = [], [], [], []
    for record in receipt["runs"]:
        experiments.append(SimpleNamespace(data=[SimpleNamespace(
            name=record["run_name"], id=record["experiment_id"], item_count=1, dataset_id="dataset-test")]))
        items.append(SimpleNamespace(data=[SimpleNamespace(
            experiment_item_id=record["dataset_item_id"], trace_id=record["trace_id"],
            output=json.dumps(record["raw_output"])) ]))
        observations.append(SimpleNamespace(data=[SimpleNamespace(
            id=record["generation_id"], output=json.dumps(record["raw_output"]), model=record["model"],
            input=json.dumps({"messages": [{"content": load_snapshot(record["prompt_version"]).system}],
                              "json_schema": load_snapshot(record["prompt_version"]).schema}),
            metadata={"prompt_sha256": record["prompt_sha256"], "prompt_version": record["prompt_version"]})]))
        scores.append(SimpleNamespace(data=[SimpleNamespace(name=k, value=v) for k, v in record["scores"].items()]))
    lf.api.experiments.list.side_effect = experiments
    lf.api.experiments.list_items.side_effect = items
    lf.api.observations.get_many.side_effect = observations
    lf.api.scores_v3.get_many_v3.side_effect = scores
    assert len(verify_runs(lf, receipt)) == 3
    assert fake_api.chat.completions.create.call_count == 3


def test_c0_v1_free_module_not_modernized(lf, config, fake_api, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("历史客户端不得调用当前生产 normalizer")
    monkeypatch.setattr("src.model_client.normalize_result", forbidden)
    raw = {**prediction("v1"), "是否为有效产品反馈": True,
           "反馈类型": "正向评价", "产品模块": "国外版本整体体验"}
    values = [raw, prediction("v2"), prediction("v3")]
    fake_api.chat.completions.create.side_effect = [SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(value)))]) for value in values]
    result = run_c0(lf, config, fake_api, tmp_path / "receipt.json")["runs"][0]
    assert result["raw_output"] == result["normalized_output"] == result["score_input"]
    assert result["score_input"]["产品模块"] == "国外版本整体体验"
    assert result["score_input"]["严重程度"] == "低"
    assert result["scores"] == {"valid_correct": 0, "feedback_type_correct": 0,
                                "product_module_correct": 0, "all_fields_correct": 0}
