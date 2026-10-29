"""只读已保存的 V3 预测，生成待人工复核的问题候选。无模型依赖。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.issue_clustering import load_feedback, build_clusters, apply_review, package_results, export_package


def run_from_records(rows, output):
    """网页新批次复用同一 E1 归并流程；调用方提供规范记录。"""
    package = package_results(rows, build_clusters(rows))
    export_package(package, output)
    return package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", type=Path, default=[
        ROOT / "evaluation/results/v3/evaluation_detail.xlsx",
        ROOT / "evaluation/results/v3_holdout/evaluation_detail.xlsx",
    ])
    parser.add_argument("--output", type=Path, default=ROOT / "analysis/results/e1_offline")
    parser.add_argument("--review", type=Path, help="编辑过的 review.json；输出必须使用新目录")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请用 --output 指定新目录（禁止覆盖）")
    rows = load_feedback(args.input)
    issues = apply_review(rows, json.loads(args.review.read_text(encoding="utf-8"))) if args.review else build_clusters(rows)
    package = package_results(rows, issues)
    export_package(package, args.output)
    print(json.dumps(package["summary"], ensure_ascii=False, indent=2))
    print(f"输出目录: {args.output.resolve()}")
    print("候选问题需人工复核；模型 API 调用次数: 0")


if __name__ == "__main__":
    main()
