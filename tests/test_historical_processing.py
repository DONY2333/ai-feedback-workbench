"""C1 全离线回放与版本隔离回归。"""

import hashlib
from unittest.mock import Mock

import pandas as pd
import pytest

from evaluation.analyze_results import compare_fields, prepare_detail
from evaluation.historical_processing import process_output, score_output, version_metadata
from evaluation.replay_history import RESULTS, replay_all, replay_version
from src.schemas import normalize_result


def raw_v1():
    return {"是否为有效产品反馈": True, "反馈类型": "正向评价",
            "产品模块": "国外版本整体体验", "用户场景": "未识别", "核心痛点": "未识别",
            "情绪倾向": "正向", "严重程度": "低", "原始评论证据": "测试"}


def gold_v1():
    return pd.Series({"ID": "L001", "gold_是否为有效产品反馈": False,
                      "gold_反馈类型": "情绪或无效反馈", "gold_产品模块": "其他",
                      "gold_严重程度": "不适用"})


def test_v1_never_maps_free_module_or_changes_severity():
    for module in ("国外版本整体体验", "消息通知", "完全未知模块"):
        raw = {**raw_v1(), "产品模块": module}
        layers = process_output(raw, "测试", "v1")
        assert layers["raw_output"] == raw
        assert layers["normalized_output"] == raw
        assert layers["score_input"]["产品模块"] == module
        assert layers["score_input"]["严重程度"] == "低"
    assert score_output(gold_v1(), layers["score_input"], "v1")["product_module_correct"] == 0


def test_three_layers_are_independent_copies():
    raw = raw_v1()
    layers = process_output(raw, "测试", "v1")
    layers["score_input"]["产品模块"] = "mutated"
    assert raw["产品模块"] == layers["raw_output"]["产品模块"] == layers["normalized_output"]["产品模块"]


def test_v1_l001_and_severity_omission():
    scores = score_output(gold_v1(), process_output(raw_v1(), "测试", "v1")["score_input"], "v1")
    assert scores == {"valid_correct": 0, "feedback_type_correct": 0,
                      "product_module_correct": 0, "all_fields_correct": 0}


def test_v1_canonical_dataset_gold_name_is_projected_without_mutation():
    gold = gold_v1()
    gold["gold_反馈类型"] = "情绪/无效反馈"
    raw = {**raw_v1(), "是否为有效产品反馈": False, "反馈类型": "情绪或无效反馈", "产品模块": "其他"}
    assert score_output(gold, raw, "v1")["all_fields_correct"] == 1
    assert gold["gold_反馈类型"] == "情绪/无效反馈"
    assert raw["反馈类型"] == "情绪或无效反馈"


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("change", [{}, {"产品模块": "消息通知"}, {"产品模块": "unknown"},
                                      {"反馈类型": "情绪或无效反馈", "是否为有效产品反馈": True},
                                      {"严重程度": "严重"}, {"反馈类型": "Bug", "严重程度": "高"},
                                      {"情绪倾向": "未知", "原始评论证据": "不存在"}])
def test_formal_versions_keep_confirmed_current_behavior(version, change):
    raw = {**raw_v1(), **change}
    assert process_output(raw, "测试", version)["normalized_output"] == normalize_result(raw, "测试")


def test_production_normalizer_not_called_by_history(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("不得调用现代 normalizer"))
    monkeypatch.setattr("src.schemas.normalize_result", forbidden)
    monkeypatch.setattr("src.model_client.normalize_result", forbidden)
    for version in ("v1", "v2", "v3"):
        assert process_output(raw_v1(), "测试", version)["normalization_reproduction"] == "partial"
    forbidden.assert_not_called()


def test_explicit_historical_validation_allows_unknown_prediction_only():
    row = gold_v1().to_dict()
    row.update({f"pred_{key}": value for key, value in raw_v1().items()})
    row["分析来源"] = "api"
    frame = pd.DataFrame([row])
    with pytest.raises(ValueError):
        prepare_detail(frame)
    prepared = prepare_detail(frame, historical_version="v1")
    assert prepared.iloc[0]["pred_产品模块"] == "国外版本整体体验"
    compared = compare_fields(frame, historical_version="v1")
    assert not compared.iloc[0]["correct_产品模块"]
    frame.loc[0, "gold_产品模块"] = "非法Gold"
    with pytest.raises(ValueError):
        prepare_detail(frame, historical_version="v1")


def test_applicable_severity_still_counts_unknown_prediction_as_error():
    gold = gold_v1()
    gold["gold_严重程度"] = "高"
    raw = raw_v1()
    raw["严重程度"] = "严重"
    assert score_output(gold, raw, "v1")["severity_correct"] == 0


@pytest.mark.parametrize("version,run", [("v1", "v1_baseline"), ("v2", "v2"), ("v3", "v3")])
def test_every_historical_match_and_summary_reproduces(version, run):
    report = replay_version(version, RESULTS / run)
    assert report["all_matches_equal"] and report["summary_equal"]
    if version == "v1":
        module = next(x for x in report["summary"] if x["field"] == "产品模块")
        assert module == {"field": "产品模块", "total": 20, "correct": 1, "accuracy": .05}


def test_replay_and_c0_corrections_do_not_touch_network_or_sources(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("离线回放不允许网络/模型")
    monkeypatch.setattr("src.model_client.FeedbackModelClient.analyze", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)
    files = [p for run in ("v1_baseline", "v2", "v3") for p in (RESULTS / run).glob("evaluation_*")]
    files += [RESULTS / "langfuse_phase_c0/receipt.json", RESULTS.parent / "gold_set_82.xlsx"]
    files += list((RESULTS.parent / "prompts").glob("*.md"))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    report = replay_all()
    assert report["model_api_calls"] == report["langfuse_writes"] == 0
    for record in report["c0_local_corrections"]:
        wanted = 0 if record["prompt_version"] == "v1" else 1
        assert record["corrected_scores"] == {
            "valid_correct": wanted, "feedback_type_correct": wanted,
            "product_module_correct": wanted, "all_fields_correct": wanted,
        }
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def test_unknown_version_rejected():
    with pytest.raises(ValueError):
        version_metadata("v4")
