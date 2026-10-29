"""离线指标的可手算回归测试；不需要 API 配置或模型请求。"""

import importlib

import pandas as pd
import pytest

from evaluation.analyze_results import analyze_detail, classification_metrics, prepare_detail


def sample_detail():
    rows = []
    # 行 1 的严重程度不适用，即使预测高也不影响整条正确性。
    # 行 2 严重程度错；行 3 类型和模块错；行 4 严重程度错。
    for i, (severity, pred_severity) in enumerate([
        ("不适用", "高"), ("高", "中"), ("中", "中"), ("低", "中"),
    ]):
        rows.append({
            "ID": f"T{i}", "分析来源": "api", "错误信息": "",
            "gold_是否为有效产品反馈": True, "pred_是否为有效产品反馈": True,
            "gold_反馈类型": "Bug", "pred_反馈类型": "使用困难" if i == 2 else "Bug",
            "gold_产品模块": "通知", "pred_产品模块": "聊天/消息" if i == 2 else "通知",
            "gold_严重程度": severity, "pred_严重程度": pred_severity,
            "match_严重程度": True,  # 故意放过时的 match，分析必须重算。
        })
    return pd.DataFrame(rows)


def test_severity_excludes_only_gold_not_applicable():
    data = sample_detail()
    data.loc[1, "pred_严重程度"] = "不适用"
    tables = analyze_detail(data)
    severity = tables["metrics_overall"].set_index("field").loc["严重程度"]
    assert severity["n_samples"] == 3
    assert severity["correct"] == 1
    assert severity["accuracy"] == pytest.approx(1 / 3)
    matrix = tables["confusion_severity"]
    assert matrix.index.tolist() == ["高", "中", "低"]
    assert matrix.loc["高", "不适用"] == 1
    assert matrix.to_numpy().sum() == 3  # 不丢掉预测为不适用的错误。


def test_macro_weighted_f1_with_fixed_absent_class():
    gold = pd.Series(["A", "A", "A", "B"])
    pred = pd.Series(["A", "B", "B", "B"])
    # 实际 A: P=1,R=1/3,F1=1/2,Support=3; B: P=1/3,R=1,F1=1/2,Support=1。
    metrics, classes, matrix = classification_metrics(gold, pred, ["A", "B", "C"])
    assert metrics["accuracy"] == 0.5
    assert metrics["macro_f1"] == pytest.approx(1 / 3)  # (0.5 + 0.5 + 0)/3
    assert metrics["weighted_f1"] == 0.5
    absent = classes.set_index("label").loc["C"]
    assert absent["support"] == 0
    assert pd.isna(absent["f1"])
    assert "Support=0" in absent["status"]
    assert matrix.to_numpy().sum() == 4


def test_weighted_f1_uses_gold_support_not_prediction_support():
    metrics, _, _ = classification_metrics(
        pd.Series(["A", "A", "B"]), pd.Series(["A", "B", "B"]), ["A", "B"],
    )
    assert metrics["macro_f1"] == pytest.approx(2 / 3)
    assert metrics["weighted_f1"] == pytest.approx(2 / 3)
    # 不同单类 F1 和 support，确保加权并非简单平均。
    metrics, _, _ = classification_metrics(
        pd.Series(["A", "A", "A", "B"]), pd.Series(["A", "A", "B", "B"]), ["A", "B"],
    )
    assert metrics["macro_f1"] == pytest.approx((0.8 + 2 / 3) / 2)
    assert metrics["weighted_f1"] == pytest.approx((0.8 * 3 + 2 / 3) / 4)


def test_all_fields_correct_checks_all_applicable_fields():
    tables = analyze_detail(sample_detail())
    assert tables["all_fields_correct"]["all_fields_correct"].tolist() == [True, False, False, False]
    overall = tables["metrics_overall"].set_index("field").loc["all_fields_correct"]
    assert overall["correct"] == 1
    assert overall["n_samples"] == 4
    assert overall["accuracy"] == 0.25
    data = sample_detail()
    data.loc[0, "pred_是否为有效产品反馈"] = False
    assert not analyze_detail(data)["all_fields_correct"]["all_fields_correct"].any()


def test_error_patterns_count_and_sort_automatically():
    data = sample_detail()
    data.loc[3, "gold_严重程度"] = "高"
    errors = analyze_detail(data)["error_patterns"]
    assert errors.iloc[0][["field", "gold", "pred", "count"]].tolist() == ["严重程度", "高", "中", 2]
    assert errors.iloc[0]["sample_ids"] == "T1,T3"
    assert set(errors["field"]) == {"严重程度", "反馈类型", "产品模块"}
    assert errors["count"].tolist() == sorted(errors["count"], reverse=True)


def test_validity_positive_class_metrics_and_confusion():
    data = sample_detail()
    data["gold_是否为有效产品反馈"] = [True, True, True, False]
    data["pred_是否为有效产品反馈"] = [True, False, True, True]
    tables = analyze_detail(data)
    metrics = tables["metrics_overall"].set_index("field").loc["是否为有效产品反馈"]
    assert metrics["accuracy"] == 0.5
    assert metrics["precision"] == pytest.approx(2 / 3)
    assert metrics["recall"] == pytest.approx(2 / 3)
    assert metrics["f1"] == pytest.approx(2 / 3)
    assert metrics["support"] == 3
    assert tables["confusion_validity"].loc[False, True] == 1


@pytest.mark.parametrize("column,value", [
    ("分析来源", "mock"), ("错误信息", "request failed"),
    ("pred_产品模块", "新模块"), ("gold_严重程度", None),
    ("pred_是否为有效产品反馈", "不确定"),
])
def test_rejects_invalid_or_failed_results(column, value):
    data = sample_detail().astype(object)
    data.loc[0, column] = value
    with pytest.raises(ValueError):
        prepare_detail(data)


def test_no_applicable_severity_and_no_errors():
    data = sample_detail()
    for field in ["是否为有效产品反馈", "反馈类型", "产品模块"]:
        data[f"pred_{field}"] = data[f"gold_{field}"]
    data["gold_严重程度"] = "不适用"
    tables = analyze_detail(data)
    assert tables["error_patterns"].empty
    assert tables["all_fields_correct"]["all_fields_correct"].all()
    metrics = tables["metrics_overall"].set_index("field").loc["严重程度"]
    assert metrics["n_samples"] == 0
    assert pd.isna(metrics["accuracy"])


def test_cli_writes_only_offline_analysis(tmp_path, monkeypatch):
    module = importlib.import_module("evaluation.analyze_results")
    monkeypatch.setattr(module, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(module.pd, "read_excel", lambda _: sample_detail())
    assert module.main(["--run-name", "test_run"]) == 0
    output = tmp_path / "test_run/analysis"
    assert (output / "metrics_overall.csv").exists()
    assert (output / "confusion_validity.csv").exists()
    assert len(list(output.glob("*.csv"))) == 11
    # 模块自身不依赖模型调用或 Evaluation 执行入口。
    assert "FeedbackModelClient" not in module.__dict__
    with pytest.raises(SystemExit):
        module.main(["--run-name", "../bad"])
