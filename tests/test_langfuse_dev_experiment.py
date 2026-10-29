"""C2 安全离线测试：所有模型、Langfuse 请求均为替身。"""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from evaluation.langfuse_dataset import build_items
from evaluation.langfuse_dev_experiment import (
    EXPERIMENTS, MeteredAPI, prepare_dev, run_final, run_settings, summarize, verify_final,
)
from evaluation.run_evaluation import load_gold_set
from src.model_client import ModelConfig


@pytest.fixture
def setup_clients():
    gold, _ = load_gold_set(split="dev")
    items = [SimpleNamespace(**x, dataset_id="dataset-test", status="ACTIVE") for x in build_items(gold)]
    lf = Mock()
    lf.get_dataset.return_value = SimpleNamespace(id="dataset-test", items=items)
    lf.api.experiments.list.return_value = SimpleNamespace(data=[])
    api = Mock(max_retries=0)
    def create(**kwargs):
        n = api.chat.completions.create.call_count
        v1 = "enum" not in kwargs["response_format"]["json_schema"]["schema"]["properties"]["产品模块"]
        raw = {"是否为有效产品反馈": True, "反馈类型": "正向评价", "产品模块": "国外版本整体体验" if v1 else "其他",
               "用户场景": "未识别", "核心痛点": "未识别", "情绪倾向": "正向",
               "严重程度": "低" if v1 else "不适用", "原始评论证据": "测试"}
        return SimpleNamespace(id=f"response-{n}", usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20),
                               choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))])
    api.chat.completions.create.side_effect = create
    generations = []
    def start(**kwargs):
        from contextlib import contextmanager
        @contextmanager
        def context():
            i = len(generations)
            g = Mock(trace_id=f"trace-{i}", id=f"generation-{i}")
            generations.append((g, kwargs))
            yield g
        return context()
    lf.start_as_current_observation.side_effect = start
    def experiment(**kwargs):
        assert kwargs["max_concurrency"] == 1
        assert len(kwargs["data"]) == 20
        successful = []
        for item in kwargs["data"]:
            try:
                successful.append(kwargs["task"](item=item))
            except Exception:
                pass  # 模拟 SDK 吞掉单项错误后继续调度；不能继续请求模型。
        return SimpleNamespace(experiment_id=kwargs["name"] + "-id", item_results=successful)
    lf.run_experiment.side_effect = experiment
    config = ModelConfig(mode="api", model="gpt-6.1-sol", api_key="test-placeholder", allow_mock_fallback=False)
    return lf, api, config, generations


def test_full_60_calls_version_isolation_scores_and_summary(setup_clients, tmp_path, monkeypatch):
    lf, api, config, generations = setup_clients
    monkeypatch.setattr("src.model_client.normalize_result", Mock(side_effect=AssertionError("禁止现代标准化")))
    journal = run_final(lf, config, api, tmp_path / "receipt.json")
    assert journal["model_api_calls"] == api.chat.completions.create.call_count == 60
    assert lf.run_experiment.call_count == 3
    assert len(generations) == 60
    for run in journal["runs"]:
        assert run["experiment"] == EXPERIMENTS[run["prompt_version"]]
        assert len(run["records"]) == 20
        assert {r["gold_id"] for r in run["records"]} == {f"L{i:03d}" for i in range(1, 21)}
        for record in run["records"]:
            assert record["attempted"] and record["status"] == "completed"
            assert all(k in record for k in ("raw_output", "normalized_output", "score_input", "normalization_version", "scoring_version"))
            assert record["usage_details"] == {"input": 12, "output": 8, "total": 20}
            assert ("severity_correct" in record["scores"]) == (record["expected_output"]["严重程度"] != "不适用")
            if record["prompt_version"] == "v1":
                assert record["score_input"]["产品模块"] == "国外版本整体体验"
                assert record["scores"]["product_module_correct"] == 0
    requests = api.chat.completions.create.call_args_list
    assert len({requests[i].kwargs["messages"][0]["content"] for i in (0, 20, 40)}) == 3
    summary = summarize(journal)
    assert all(s["samples"] == 20 and s["metrics"]["severity_correct"]["total"] == 18 for s in summary)
    assert all(s["tokens"]["total"] == 400 and s["token_coverage"] == "20/20" for s in summary)
    with pytest.raises(ValueError, match="回执"):
        run_final(lf, config, api, tmp_path / "receipt.json")
    assert api.chat.completions.create.call_count == 60


@pytest.mark.parametrize("corrupt", ["duplicate", "missing", "extra", "gold"])
def test_dataset_preflight_fail_before_model(setup_clients, tmp_path, corrupt):
    lf, api, config, _ = setup_clients
    items = lf.get_dataset.return_value.items
    if corrupt == "duplicate":
        items[-1] = items[0]
    elif corrupt == "missing":
        items.pop()
    elif corrupt == "extra":
        items.append(items[0])
    else:
        items[0].expected_output["产品模块"] = "通知"
    with pytest.raises(ValueError):
        run_final(lf, config, api, tmp_path / "receipt.json")
    api.chat.completions.create.assert_not_called()
    lf.run_experiment.assert_not_called()


@pytest.mark.parametrize("failure", ["http", "auth", "parse", "schema"])
def test_failure_latches_and_no_retry_or_later_versions(setup_clients, tmp_path, failure):
    lf, api, config, _ = setup_clients
    if failure == "http":
        api.chat.completions.create.side_effect = ConnectionError("do not log request details")
    elif failure == "auth":
        class AuthenticationError(Exception):
            status_code = 401
        api.chat.completions.create.side_effect = AuthenticationError("do not log request details")
    else:
        content = "not json" if failure == "parse" else '{}'
        api.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        api.chat.completions.create.side_effect = None
    with pytest.raises(RuntimeError):
        run_final(lf, config, api, tmp_path / "receipt.json")
    assert api.chat.completions.create.call_count == 1
    assert lf.run_experiment.call_count == 1
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["model_api_calls"] == 1 and receipt["status"] == "failed"
    assert receipt["runs"][0]["status"] == "failed"
    if failure == "auth":
        assert receipt["runs"][0]["records"][0]["http_status"] == 401
    assert "do not log request details" not in (tmp_path / "receipt.json").read_text(encoding="utf-8")


def test_existing_final_name_is_not_reused(setup_clients, tmp_path):
    lf, api, config, _ = setup_clients
    lf.api.experiments.list.return_value = SimpleNamespace(data=[Mock()])
    with pytest.raises(ValueError, match="已存在"):
        run_final(lf, config, api, tmp_path / "receipt.json")
    api.chat.completions.create.assert_not_called()


def test_budget_120_and_single_dispatch_enforced(tmp_path):
    path = tmp_path / "receipt.json"
    api = Mock(max_retries=0)
    journal = {"model_api_calls": 120}
    client = MeteredAPI(api, journal, {}, path)
    with pytest.raises(RuntimeError):
        client.create()
    api.chat.completions.create.assert_not_called()
    client = MeteredAPI(api, {"model_api_calls": 1}, {"attempted": True}, path)
    with pytest.raises(RuntimeError):
        client.create()
    api.chat.completions.create.assert_not_called()


@pytest.mark.parametrize("mode,fallback,retries", [("mock", False, 0), ("api", True, 0), ("api", False, 1)])
def test_config_safety(setup_clients, tmp_path, mode, fallback, retries):
    lf, api, config, _ = setup_clients
    config.mode, config.allow_mock_fallback, api.max_retries = mode, fallback, retries
    with pytest.raises(ValueError):
        run_final(lf, config, api, tmp_path / "receipt.json")
    api.chat.completions.create.assert_not_called()


def setup_verification(lf, journal):
    runs = {r["experiment"] + "-server-id": r for r in journal["runs"]}
    by_name = {r["experiment"]: r for r in journal["runs"]}
    records = {r["trace_id"]: r for run in journal["runs"] for r in run["records"]}
    lf.api.experiments.list.side_effect = lambda **k: SimpleNamespace(data=[SimpleNamespace(
        id=k["name"] + "-server-id", name=by_name[k["name"]]["experiment"], item_count=20, dataset_id="dataset-test")])
    lf.api.experiments.list_items.side_effect = lambda **k: SimpleNamespace(data=[SimpleNamespace(
        experiment_item_id=r["dataset_item_id"], trace_id=r["trace_id"], input=json.dumps(r["input"]),
        output=json.dumps(r["result_output"]), expected_output=json.dumps(r["expected_output"])) for r in runs[k["experiment_id"]]["records"]])
    def observations(**kwargs):
        r = records[kwargs["trace_id"]]
        from evaluation.langfuse_c0 import load_snapshot
        s = load_snapshot(r["prompt_version"])
        return SimpleNamespace(data=[SimpleNamespace(id=r["generation_id"], model=r["model"], metadata=r,
            input=json.dumps({"messages": [{"content": s.system}], "json_schema": s.schema}), output=json.dumps(r["result_output"]))])
    lf.api.observations.get_many.side_effect = observations
    lf.api.scores_v3.get_many_v3.side_effect = lambda **k: SimpleNamespace(data=[SimpleNamespace(name=n, value=v) for n, v in records[k["trace_id"]]["scores"].items()])


def test_server_verification_is_read_only_and_checks_all_60(setup_clients, tmp_path):
    lf, api, config, _ = setup_clients
    path = tmp_path / "receipt.json"
    journal = run_final(lf, config, api, path)
    setup_verification(lf, journal)
    verified = verify_final(lf, journal, path)
    assert verified["status"] == "server_verified"
    assert len(verified["server_verification"]) == 3
    assert all(r["server_experiment_id"] != r["experiment_id"] for r in verified["runs"])
    assert all(r["server_verified"] for run in verified["runs"] for r in run["records"])
    assert lf.api.observations.get_many.call_count == lf.api.scores_v3.get_many_v3.call_count == 60
    assert api.chat.completions.create.call_count == 60


def test_duplicate_remote_item_fails_readback(setup_clients, tmp_path):
    lf, api, config, _ = setup_clients
    path = tmp_path / "receipt.json"
    journal = run_final(lf, config, api, path)
    setup_verification(lf, journal)
    links = lf.api.experiments.list_items.side_effect(experiment_id=journal["runs"][0]["experiment"] + "-server-id")
    links.data[-1] = links.data[0]
    lf.api.experiments.list_items.side_effect = None
    lf.api.experiments.list_items.return_value = links
    with pytest.raises(ValueError, match="缺失/重复"):
        verify_final(lf, journal, path)
    assert api.chat.completions.create.call_count == 60


def test_unreliable_usage_not_fabricated(tmp_path):
    api = Mock(max_retries=0)
    api.chat.completions.create.return_value = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=99))
    record = {}
    MeteredAPI(api, {"model_api_calls": 0}, record, tmp_path / "receipt.json").create()
    assert record["usage_details"] is None
    assert record["usage_source"] == "unavailable_or_inconsistent"


def test_r2_isolated_names_receipt_and_full_60(setup_clients, tmp_path):
    lf, api, config, _ = setup_clients
    names, path = run_settings("r2")
    assert path.parent.name == "r2"
    assert names == {v: f"feedback-dev-{v}-final-r2" for v in ("v1", "v2", "v3")}
    old = tmp_path / "receipt.json"
    old.write_text("old failed receipt", encoding="utf-8")
    journal = run_final(lf, config, api, tmp_path / "r2" / "receipt.json", experiments=names)
    assert api.chat.completions.create.call_count == journal["model_api_calls"] == 60
    assert [r["experiment"] for r in journal["runs"]] == list(names.values())
    assert old.read_text(encoding="utf-8") == "old failed receipt"


@pytest.mark.parametrize("revision", ["../r2", "", "v2", "r0"])
def test_revision_rejects_invalid_path(revision):
    with pytest.raises(ValueError):
        run_settings(revision)


@pytest.mark.parametrize("status", [None, 429, 500, 503])
@pytest.mark.parametrize("failures", [1, 2])
def test_transient_retry_and_continue_without_third_attempt(setup_clients, tmp_path, status, failures):
    lf, api, config, _ = setup_clients
    original = api.chat.completions.create.side_effect
    class TemporaryHTTPError(Exception):
        status_code = status
    def create(**kwargs):
        if api.chat.completions.create.call_count <= failures:
            raise TimeoutError("secret detail not logged") if status is None else TemporaryHTTPError("secret detail not logged")
        return original(**kwargs)
    api.chat.completions.create.side_effect = create
    path = tmp_path / "receipt.json"
    journal = run_final(lf, config, api, path)
    first = journal["runs"][0]["records"][0]
    assert api.chat.completions.create.call_count == journal["model_api_calls"] == 61
    assert first["attempt_count"] == len(first["attempts"]) == 2
    assert first["retry_reason"]["error_category"] == "transient"
    assert first["attempts"][0]["status"] == "failed"
    assert first["final_status"] == ("completed" if failures == 1 else "failed")
    assert journal["runs"][0]["records"][1]["attempt_count"] == 1
    assert all(len(r["records"]) == 20 for r in journal["runs"])
    assert "secret detail not logged" not in path.read_text(encoding="utf-8")
    if failures == 2:
        assert first["raw_output"] is first["normalized_output"] is first["score_input"] is None
        assert first["scores"] == {}
    setup_verification(lf, journal)
    verified = verify_final(lf, journal, path)
    assert verified["server_verification"][0]["failed"] == (failures == 2)


@pytest.mark.parametrize("status", [401, 403, 400])
def test_systemic_http_never_retried(setup_clients, tmp_path, status):
    lf, api, config, _ = setup_clients
    class HTTPFailure(Exception):
        status_code = status
    api.chat.completions.create.side_effect = HTTPFailure()
    with pytest.raises(RuntimeError):
        run_final(lf, config, api, tmp_path / "receipt.json")
    assert api.chat.completions.create.call_count == 1
    assert lf.run_experiment.call_count == 1
