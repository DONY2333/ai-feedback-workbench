"""Phase D 固定证据与文档核对：只读文件，无客户端、模型或 Evaluation 执行。"""

import csv
import hashlib
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evaluation/results"
REPORT = RESULTS / "experiment_summary.md"
FIELDS = ["是否为有效产品反馈", "反馈类型", "产品模块", "严重程度"]


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def rate(correct, total):
    return f"{int(correct)}/{int(total)} = {float(correct) / float(total):.2%}"


def test_frozen_evidence_hashes_unchanged():
    manifest = json.loads((RESULTS / "experiment_evidence_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 21
    assert len({x["path"] for x in manifest["files"]}) == 21
    for item in manifest["files"]:
        path = (ROOT / item["path"]).resolve()
        assert path.is_relative_to(ROOT) and path.is_file(), item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], item["path"]


@pytest.mark.parametrize("version,folder", [("V1", "v1_baseline"), ("V2", "v2"), ("V3", "v3")])
def test_historical_table_matches_saved_summary(version, folder):
    summary = {r["字段名"]: r for r in rows(RESULTS / folder / "evaluation_summary.csv")}
    cells = [rate(summary[f]["正确数"], summary[f]["总样本数"]) for f in FIELDS]
    assert f"| {version} | " + " | ".join(cells) + " |" in REPORT.read_text(encoding="utf-8")
    for field in FIELDS:
        record = summary[field]
        assert float(record["一致率"]) == pytest.approx(float(record["正确数"]) / float(record["总样本数"]))


def test_holdout_table_and_error_patterns_match_saved_files():
    text = REPORT.read_text(encoding="utf-8")
    overall = {r["field"]: r for r in rows(RESULTS / "v3_holdout/analysis/metrics_overall.csv")}
    for field, label in zip(FIELDS, ["Valid", "Feedback Type", "Product Module", "Severity"]):
        r = overall[field]
        cells = [f"{r['correct']}/{r['n_samples']}"] + [f"{float(r[k]):.2%}" for k in ("accuracy", "macro_f1", "weighted_f1")]
        assert f"| {label} | " + " | ".join(cells) + " |" in text
    all_fields = overall["all_fields_correct"]
    assert "All-fields correct：" + rate(all_fields["correct"], all_fields["n_samples"]) in text
    patterns = {(r["field"], r["gold"], r["pred"]): int(r["count"]) for r in rows(RESULTS / "v3_holdout/analysis/error_patterns.csv")}
    for field, gold, pred in [("严重程度", "高", "中"), ("反馈类型", "使用困难", "Bug"), ("产品模块", "安装/更新", "性能/稳定性")]:
        assert f"{gold}→{pred} {patterns[field, gold, pred]}" in text


def test_support_zero_and_denominators_are_explicit():
    severity = rows(RESULTS / "v3_holdout/analysis/metrics_severity.csv")
    assert sum(int(r["support"]) for r in severity) == 44
    low = next(r for r in severity if r["label"] == "低")
    assert int(low["support"]) == 0 and all(low[k] == "" for k in ("precision", "recall", "f1"))
    modules = rows(RESULTS / "v3_holdout/analysis/metrics_product_module.csv")
    assert len(modules) == 19 and sum(int(r["support"]) > 0 for r in modules) == 14
    text = REPORT.read_text(encoding="utf-8")
    assert "support=0" in text and "固定分母" in text and "31/44=70.45%" in text


def test_r3_table_latency_usage_and_existing_verification_receipt():
    text = REPORT.read_text(encoding="utf-8")
    j = json.loads((RESULTS / "langfuse_phase_c2/r3/receipt.json").read_text(encoding="utf-8"))
    assert j["status"] == "server_verified" and j["model_api_calls"] == 60
    assert len(j["runs"]) == len(j["summary"]) == 3
    total_tokens, elapsed, count = 0, 0.0, 0
    for run, summary in zip(j["runs"], j["summary"]):
        records = run["records"]
        assert len(records) == 20 and {r["gold_id"] for r in records} == {f"L{i:03d}" for i in range(1, 21)}
        assert all(r["status"] == "completed" and r["server_verified"] and r["attempt_count"] == 1 for r in records)
        assert run["normalization_reproduction"] == "partial"
        assert sum(len(r["scores"]) for r in records) == 98
        assert run["experiment"] in text and run["server_experiment_id"] in text
        metrics = summary["metrics"]
        keys = ["valid_correct", "feedback_type_correct", "product_module_correct", "severity_correct", "all_fields_correct"]
        cells = []
        for key in keys:
            values = [r["scores"][key] for r in records if key in r["scores"]]
            assert metrics[key]["correct"] == sum(values) and metrics[key]["total"] == len(values)
            cells.append(rate(sum(values), len(values)))
        version = run["prompt_version"].upper()
        assert f"| {version} | " + " | ".join(cells) + " |" in text
        tokens = {k: sum(r["usage_details"][k] for r in records) for k in ("input", "output", "total")}
        assert tokens == summary["tokens"] and tokens["input"] + tokens["output"] == tokens["total"]
        mean = sum(r["api_latency_seconds"] for r in records) / 20
        assert mean == pytest.approx(summary["mean_api_latency_seconds"])
        latency_cells = [f"{mean:.3f}s"] + [f"{tokens[k]:,}" for k in ("input", "output", "total")]
        assert f"| {version} | " + " | ".join(latency_cells) + " |" in text
        total_tokens += tokens["total"]
        elapsed += sum(r["api_latency_seconds"] for r in records)
        count += 20
    assert f"总 token：{total_tokens:,}；总体平均 API latency：{elapsed/count:.3f}s。" in text


def test_report_local_links_and_readme_entry():
    for target in re.findall(r"\]\(([^)]+)\)", REPORT.read_text(encoding="utf-8")):
        assert (REPORT.parent / target).resolve().is_file(), target
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "(evaluation/results/experiment_summary.md)" in readme


def test_report_preserves_key_limitations_and_separate_runs():
    text = REPORT.read_text(encoding="utf-8")
    for required in ("partial reproduction", "pristine unseen test set", "不是上传历史预测", "未用于创建 V4",
                     "不计算货币成本", "核心痛点尚未建立正式自由文本 Evaluation", ".gitignore", "Phase D 新进行的在线核验"):
        assert required in text
