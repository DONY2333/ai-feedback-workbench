from __future__ import annotations

import json

import pandas as pd
import pytest

from evaluation.run_evaluation import (
    EvaluationError,
    GoldSetInfo,
    calculate_accuracy,
    detect_column_mapping,
    load_gold_set,
    parse_args,
    run_evaluation,
    save_results,
)
from src.model_client import FeedbackModelClient, ModelConfig


DEV_IDS = [f"L{i:03d}" for i in range(1, 21)]
HOLDOUT_IDS = [f"L{i:03d}" for i in range(21, 44)] + [f"W{i:03d}" for i in range(1, 40)]
ALL_IDS = DEV_IDS + HOLDOUT_IDS


def test_gold_label_column_mapping():
    columns = [
        "ID", "产品", "评论原文", "人工-是否有效", "人工-反馈类型",
        "人工-产品模块", "人工-核心痛点", "人工-严重程度",
    ]
    mapping = detect_column_mapping(columns)

    assert mapping["gold_是否为有效产品反馈"] == "人工-是否有效"
    assert mapping["gold_反馈类型"] == "人工-反馈类型"
    assert mapping["gold_产品模块"] == "人工-产品模块"
    assert mapping["gold_核心痛点"] == "人工-核心痛点"
    assert mapping["gold_严重程度"] == "人工-严重程度"


def test_accuracy_calculation():
    detail = pd.DataFrame(
        {
            "match_反馈类型": [True, False, True],
            "match_严重程度": [True, True, None],
        }
    )

    summary = calculate_accuracy(detail, ["反馈类型", "严重程度"]).set_index("字段名")

    assert summary.loc["反馈类型", "总样本数"] == 3
    assert summary.loc["反馈类型", "正确数"] == 2
    assert summary.loc["反馈类型", "一致率"] == pytest.approx(2 / 3)
    assert summary.loc["严重程度", "总样本数"] == 2
    assert summary.loc["严重程度", "一致率"] == 1.0


def test_evaluation_disallows_api_fallback(monkeypatch):
    client = FeedbackModelClient(
        ModelConfig(mode="api", api_key="test-key", allow_mock_fallback=False)
    )
    monkeypatch.setattr(client, "_call_api", lambda *_: (_ for _ in ()).throw(ConnectionError("boom")))

    with pytest.raises(RuntimeError, match="真实 API 分析失败"):
        client.analyze("测试评论", "飞书")


def test_run_evaluation_rejects_client_with_fallback_enabled():
    client = FeedbackModelClient(ModelConfig(mode="api", api_key="test-key", allow_mock_fallback=True))
    info = GoldSetInfo("评论数据", {}, ["反馈类型"])

    with pytest.raises(EvaluationError, match="禁用 Mock fallback"):
        run_evaluation(pd.DataFrame(), info, client)


def make_gold_source():
    rows = 82
    return pd.DataFrame(
        {
            "ID": ALL_IDS,
            "产品": ["飞书"] * rows,
            "评论原文": [f"评论 {i}" for i in range(rows)],
            "人工-是否有效": ["有效"] * rows,
            "人工-反馈类型": ["Bug"] * rows,
            "人工-产品模块": ["通知"] * rows,
            "人工-核心痛点": ["收不到通知"] * rows,
            "人工-严重程度": ["高"] * rows,
        }
    )


@pytest.mark.parametrize("split,expected_ids,count", [("dev", DEV_IDS, 20), ("holdout", HOLDOUT_IDS, 62), ("all", ALL_IDS, 82)])
def test_gold_set_split_by_id_not_row_order(tmp_path, split, expected_ids, count):
    source = make_gold_source().iloc[::-1].reset_index(drop=True)
    path = tmp_path / "gold.xlsx"
    source.to_excel(path, index=False, sheet_name="评论数据")

    result, info = load_gold_set(path, split=split)

    assert result["ID"].tolist() == expected_ids
    assert len(result) == count
    for _, row in result.iterrows():
        assert source.iloc[row["原始Excel行号"] - 2]["ID"] == row["ID"]
    assert info.evaluation_fields == ["是否为有效产品反馈", "反馈类型", "严重程度", "产品模块"]


@pytest.mark.parametrize("invalid", ["blank", "duplicate", "missing", "outside", "extra", "format", "column", "L044", "W040", "X001"])
def test_invalid_gold_ids_fail_before_api(tmp_path, monkeypatch, invalid):
    import importlib

    module = importlib.import_module("evaluation.run_evaluation")
    source = make_gold_source()
    if invalid == "blank":
        source.loc[81, "ID"] = None
    elif invalid == "duplicate":
        source.loc[81, "ID"] = "L001"
    elif invalid == "missing":
        source = source.iloc[:-1]
    elif invalid == "outside":
        source.loc[81, "ID"] = "L083"
    elif invalid == "extra":
        extra = source.iloc[[0]].copy()
        extra["ID"] = "W040"
        source = pd.concat([source, extra], ignore_index=True)
    elif invalid == "format":
        source.loc[81, "ID"] = "L82"
    elif invalid == "column":
        source = source.drop(columns="ID")
    else:
        source.loc[81, "ID"] = invalid
    path = tmp_path / "gold.xlsx"
    source.to_excel(path, index=False)
    monkeypatch.setattr(module, "DEFAULT_GOLD_PATH", path)

    def forbidden(*args, **kwargs):
        pytest.fail("ID 验证失败时不应创建 API 配置或调用 API")

    monkeypatch.setattr(ModelConfig, "from_environment", forbidden)
    monkeypatch.setattr(FeedbackModelClient, "analyze", forbidden)
    with pytest.raises((EvaluationError, ValueError), match="ID|必要字段"):
        module.main(["--split", "dev"])


def test_default_split_is_dev():
    assert parse_args([]).split == "dev"
    with pytest.raises(SystemExit):
        parse_args(["--split", "unknown"])


def test_holdout_printed_before_api_setup(tmp_path, monkeypatch, capsys):
    import importlib

    module = importlib.import_module("evaluation.run_evaluation")
    path = tmp_path / "gold.xlsx"
    make_gold_source().to_excel(path, index=False)
    monkeypatch.setattr(module, "DEFAULT_GOLD_PATH", path)

    def stop_before_api(*args, **kwargs):
        output = capsys.readouterr().out
        assert "Split: holdout" in output
        assert "样本范围: L021-L043 + W001-W039" in output
        assert "样本数: 62" in output
        raise EvaluationError("本地测试：停止，不调用 API")

    monkeypatch.setattr(ModelConfig, "from_environment", stop_before_api)
    with pytest.raises(EvaluationError, match="本地测试"):
        module.main(["--split", "holdout", "--run-name", "v3_holdout", "--prompt-version", "v3"])


def test_run_name_argument_and_default():
    assert parse_args([]).run_name == "v2"
    assert parse_args(["--run-name", "v2_trial"]).run_name == "v2_trial"


def test_prompt_version_argument_only_records_metadata():
    from src.model_client import JSON_SCHEMA, SYSTEM_PROMPT

    original_prompt = SYSTEM_PROMPT
    original_schema = json.dumps(JSON_SCHEMA, ensure_ascii=False)
    assert parse_args([]).prompt_version == "v3"
    args = parse_args(["--run-name", "v3", "--prompt-version", "custom-label"])
    assert args.run_name == "v3"
    assert args.prompt_version == "custom-label"
    from src import model_client

    assert model_client.SYSTEM_PROMPT == original_prompt
    assert json.dumps(model_client.JSON_SCHEMA, ensure_ascii=False) == original_schema


@pytest.mark.parametrize("run_name", ["../v2", "..\\v2", "C:\\temp", "CON"])
def test_run_name_rejects_invalid_directory(run_name):
    with pytest.raises(SystemExit):
        parse_args(["--run-name", run_name])


@pytest.mark.parametrize("request_fails", [False, True])
@pytest.mark.parametrize("prompt_version", [None, "custom-label"])
@pytest.mark.parametrize("split,expected_ids", [("dev", DEV_IDS), ("holdout", HOLDOUT_IDS), ("all", ALL_IDS)])
def test_versioned_results_include_metadata(tmp_path, monkeypatch, request_fails, prompt_version, split, expected_ids):
    prediction = {
        "是否为有效产品反馈": True, "反馈类型": "Bug", "产品模块": "通知",
        "用户场景": "接收工作消息", "核心痛点": "收不到通知", "情绪倾向": "负向",
        "严重程度": "高", "原始评论证据": "收不到通知",
    }
    gold = pd.DataFrame([{
        "ID": sample_id, "原始Excel行号": i + 2, "产品": "飞书", "评论原文": "收不到通知",
        **{f"gold_{field}": prediction[field] for field in
           ["是否为有效产品反馈", "反馈类型", "产品模块", "核心痛点", "严重程度"]},
    } for i, sample_id in enumerate(expected_ids)])
    info = GoldSetInfo("评论数据", {}, ["是否为有效产品反馈", "反馈类型", "严重程度", "产品模块"])
    client = FeedbackModelClient(ModelConfig(
        mode="api", model="test-model", api_key="test-key", allow_mock_fallback=False,
    ))

    def fake_request(*_):
        if request_fails:
            raise ConnectionError("test request failure")
        return json.dumps(prediction, ensure_ascii=False)

    monkeypatch.setattr(client, "_call_api", fake_request)
    version_args = {} if prompt_version is None else {"prompt_version": prompt_version}
    if request_fails:
        with pytest.raises(EvaluationError) as error:
            run_evaluation(gold, info, client, run_name="v2_trial", split=split, **version_args)
        detail, summary = error.value.detail, None
    else:
        detail, summary = run_evaluation(gold, info, client, run_name="v2_trial", split=split, **version_args)
        assert summary["一致率"].eq(1.0).all()

    # 旧版本文件不会被新版本写入覆盖。
    baseline_dir = tmp_path / "v1_baseline"
    baseline_dir.mkdir()
    baseline_file = baseline_dir / "evaluation_summary.csv"
    baseline_file.write_bytes(b"existing baseline")
    detail_path, summary_path = save_results(detail, summary, tmp_path, run_name="v2_trial")
    assert detail_path == tmp_path / "v2_trial" / "evaluation_detail.xlsx"
    saved = pd.read_excel(detail_path)
    assert saved["run_name"].eq("v2_trial").all()
    assert saved["prompt_version"].eq(prompt_version or "v3").all()
    assert saved["model"].eq("test-model").all()
    assert saved["split"].eq(split).all()
    assert len(saved) == (1 if request_fails else len(expected_ids))
    assert saved["ID"].tolist() == (expected_ids[:1] if request_fails else expected_ids)
    assert baseline_file.read_bytes() == b"existing baseline"
    if request_fails:
        assert summary_path is None
        assert saved["错误信息"].notna().all()
    else:
        assert summary_path == tmp_path / "v2_trial" / "evaluation_summary.csv"
        pd.testing.assert_frame_equal(pd.read_csv(summary_path), summary, check_dtype=False)

