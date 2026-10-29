"""C1 离线回放：只读历史预测与 C0 JSON，不初始化任何网络客户端。"""

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.historical_processing import FIELDS, process_output, score_output, version_metadata

RESULTS = PROJECT_ROOT / "evaluation/results"
RUNS = {"v1": "v1_baseline", "v2": "v2", "v3": "v3"}
SCORE_NAMES = dict(zip(FIELDS, ["valid_correct", "feedback_type_correct", "product_module_correct", "severity_correct"]))


def replay_version(version: str, directory: Path) -> dict:
    detail = pd.read_excel(directory / "evaluation_detail.xlsx")
    archived_summary = pd.read_csv(directory / "evaluation_summary.csv")
    if len(detail) != 20 or set(detail.ID) != {f"L{i:03d}" for i in range(1, 21)}:
        raise ValueError("历史结果必须恰好包含 L001-L020，无重复")
    totals = {field: {"total": 0, "correct": 0} for field in FIELDS}
    for _, row in detail.iterrows():
        if row["分析来源"] != "api" or pd.notna(row.get("错误信息")) and str(row["错误信息"]).strip():
            raise ValueError("历史文件包含失败或非 API 结果")
        # 存档只有已保存预测，不把它冒充模型原始响应；只回放可观察到的部分。
        prediction = {str(key)[5:]: value for key, value in row.items() if str(key).startswith("pred_")}
        layers = process_output(prediction, row["评论原文"], version)
        scores = score_output(row, layers["score_input"], version)
        for field, name in SCORE_NAMES.items():
            saved = row[f"match_{field}"]
            if name not in scores:
                if pd.notna(saved):
                    raise AssertionError(f"{version}/{row.ID}/{field}: 历史 match 应为不适用")
                continue
            if pd.isna(saved) or scores[name] != int(saved):
                raise AssertionError(f"{version}/{row.ID}/{field}: match 不一致")
            totals[field]["total"] += 1
            totals[field]["correct"] += scores[name]
    summary = []
    for field, numbers in totals.items():
        total, correct = numbers["total"], numbers["correct"]
        accuracy = correct / total if total else 0.0
        saved = archived_summary.loc[archived_summary["字段名"].eq(field)]
        if len(saved) != 1:
            raise AssertionError(f"{version}/{field}: summary 字段缺失或重复")
        saved = saved.iloc[0]
        if (int(saved["总样本数"]) != total or int(saved["正确数"]) != correct
                or not math.isclose(float(saved["一致率"]), accuracy, rel_tol=0, abs_tol=1e-12)):
            raise AssertionError(f"{version}/{field}: summary 不一致")
        summary.append({"field": field, "total": total, "correct": correct, "accuracy": accuracy})
    return {**version_metadata(version), "input_stage": "archived_prediction_not_raw_response",
            "all_matches_equal": True, "summary_equal": True, "summary": summary}


def replay_all(results: Path = RESULTS) -> dict:
    versions = [replay_version(v, results / run) for v, run in RUNS.items()]
    old_receipt = json.loads((results / "langfuse_phase_c0/receipt.json").read_text(encoding="utf-8"))
    corrected = []
    for record in old_receipt["runs"]:
        version = record["prompt_version"]
        if record["gold_id"] != "L001":
            raise ValueError("旧 C0 必须为 L001")
        detail = pd.read_excel(results / RUNS[version] / "evaluation_detail.xlsx")
        gold = detail.loc[detail.ID.eq("L001")].iloc[0]
        raw = record.get("raw_output", record.get("raw_prediction"))
        layers = process_output(raw, gold["评论原文"], version)
        corrected.append({**layers, "gold_id": "L001", "source": "existing_C0_raw_output",
                          "old_scores": record["scores"],
                          "corrected_scores": score_output(gold, layers["score_input"], version)})
    return {"model_api_calls": 0, "langfuse_writes": 0, "historical_replay": versions,
            "c0_local_corrections": corrected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="可选：另存新报告；不覆盖任何已有文件")
    args = parser.parse_args()
    report = replay_all()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as handle:
            handle.write(text)
    print(text)


if __name__ == "__main__":
    main()
