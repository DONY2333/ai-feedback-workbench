"""只读取已保存的预测，离线计算分类指标；不导入模型客户端、不发送请求。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.schemas import FEEDBACK_TYPES, PRODUCT_MODULES  # noqa: E402


RESULTS_DIR = PROJECT_ROOT / "evaluation/results"
VALIDITY = "是否为有效产品反馈"
SEVERITY = "严重程度"
SEVERITY_LABELS = ["高", "中", "低"]
FIELD_SPECS = {
    VALIDITY: ("validity", [False, True]),
    "反馈类型": ("feedback_type", FEEDBACK_TYPES),
    "产品模块": ("product_module", PRODUCT_MODULES),
    SEVERITY: ("severity", SEVERITY_LABELS),
}


def parse_boolean(value: object) -> bool:
    """Excel 布尔值可为 bool、0/1 或明确的文本；未知值报错，不猜测。"""
    text = str(value).strip().lower()
    if text in {"true", "1", "1.0", "是", "有效"}:
        return True
    if text in {"false", "0", "0.0", "否", "无效"}:
        return False
    raise ValueError(f"无法识别有效性标签：{value!r}")


def prepare_detail(detail: pd.DataFrame, *, historical_version: str | None = None) -> pd.DataFrame:
    """拒绝失败/Mock/缺失标签，避免把部分结果或空值计成好成绩。"""
    if historical_version not in {None, "v1", "v2", "v3"}:
        raise ValueError("未知历史版本")
    required = ["ID", "分析来源", *[
        f"{prefix}_{field}" for field in FIELD_SPECS for prefix in ("gold", "pred")
    ]]
    missing = set(required) - set(detail.columns)
    if missing:
        raise ValueError(f"明细缺少列：{sorted(missing)}")
    if detail.empty:
        raise ValueError("明细没有样本")
    result = detail.copy()
    if result["ID"].isna().any() or result["ID"].astype(str).str.strip().eq("").any():
        raise ValueError("ID 缺失")
    if result["ID"].duplicated().any():
        raise ValueError("ID 重复")
    if not result["分析来源"].eq("api").all():
        raise ValueError("只允许分析来源为 api 的已有结果，不得计入 Mock")
    if "错误信息" in result and result["错误信息"].fillna("").astype(str).str.strip().ne("").any():
        raise ValueError("明细存在错误信息，不分析不完整运行")
    for field, (_, labels) in FIELD_SPECS.items():
        for prefix in ("gold", "pred"):
            column = f"{prefix}_{field}"
            if result[column].isna().any():
                raise ValueError(f"{column} 有缺失值")
            if field == VALIDITY:
                result[column] = result[column].map(parse_boolean)
            else:
                if not result[column].map(lambda v: isinstance(v, str) and bool(v.strip())).all():
                    raise ValueError(f"{column} 必须是非空标签")
                # 只有显式历史回放允许范围外预测；保留原值计错，不映射成其他。
                if historical_version is not None and prefix == "pred":
                    continue
                # 不重写已有模型标签；未知 taxonomy 必须暴露，而不是自动改成其他。
                allowed = labels + (["不适用"] if field == SEVERITY else [])
                if historical_version == "v1" and field == "反馈类型":
                    allowed = ["情绪或无效反馈" if x == "情绪/无效反馈" else x for x in allowed]
                if not result[column].isin(allowed).all():
                    raise ValueError(f"{column} 有 taxonomy 之外的标签")
    return result


def classification_metrics(
    gold: pd.Series, prediction: pd.Series, labels: list,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Macro-F1 使用全部固定标签；Weighted-F1 按 Gold support 加权。"""
    from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
    if len(gold) == 0:
        # 没有适用样本时，sklearn 不接受空输入；指标应为缺失，而不是 0% 或 100%。
        overall = {"n_samples": 0, "correct": 0, **{
            name: float("nan") for name in ["accuracy", "macro_f1", "weighted_f1"]
        }}
        classes = pd.DataFrame({
            "label": labels, "precision": float("nan"), "recall": float("nan"),
            "f1": float("nan"), "support": 0, "predicted_support": 0,
            "status": "未覆盖（Support=0，不评价该类表现）",
        })
        confusion = pd.DataFrame(0, index=labels, columns=labels)
        confusion.index.name = "Gold / Pred"
        return overall, classes, confusion
    precision, recall, f1, support = precision_recall_fscore_support(
        gold.tolist(), prediction.tolist(), labels=labels, zero_division=0,
    )
    total = len(gold)
    overall = {
        "n_samples": total,
        "correct": int(gold.eq(prediction).sum()),
        "accuracy": float(accuracy_score(gold, prediction)) if total else float("nan"),
        "macro_f1": float(f1.mean()) if total else float("nan"),
        "weighted_f1": float((f1 * support).sum() / total) if total else float("nan"),
    }
    classes = pd.DataFrame({
        "label": labels, "precision": precision, "recall": recall, "f1": f1,
        "support": support,
        "predicted_support": [int(prediction.eq(label).sum()) for label in labels],
        "status": ["已覆盖" if n else "未覆盖（Support=0，不评价该类表现）" for n in support],
    })
    # 无 Gold 样本的类别不展示虚假的单类表现；宏平均的零值约定另行明确说明。
    classes.loc[classes["support"].eq(0), ["precision", "recall", "f1"]] = float("nan")

    # 若 Gold 严重程度适用而 Pred=不适用，仍是错误，不能丢失该样本。
    extra_predictions = [value for value in prediction.unique() if value not in labels]
    matrix_labels = labels + sorted(extra_predictions)
    if total:
        matrix = confusion_matrix(gold.tolist(), prediction.tolist(), labels=matrix_labels)
        confusion = pd.DataFrame(matrix, index=matrix_labels, columns=matrix_labels).reindex(index=labels)
    else:
        confusion = pd.DataFrame(0, index=labels, columns=labels)
    confusion.index.name = "Gold / Pred"
    assert int(confusion.to_numpy().sum()) == total
    return overall, classes, confusion


def compare_fields(detail: pd.DataFrame, *, historical_version: str | None = None) -> pd.DataFrame:
    """共享的 Exact Match/适用性规则；不需要分类指标库或模型。"""
    data = prepare_detail(detail, historical_version=historical_version)
    comparisons = data[["ID"]].copy()
    all_correct = pd.Series(True, index=data.index)
    for field in FIELD_SPECS:
        applicable = data[f"gold_{field}"].ne("不适用") if field == SEVERITY else pd.Series(True, index=data.index)
        matches = data[f"gold_{field}"].eq(data[f"pred_{field}"])
        comparisons[f"applicable_{field}"] = applicable
        comparisons[f"correct_{field}"] = matches.where(applicable, pd.NA)
        all_correct &= matches | ~applicable
    comparisons["all_fields_correct"] = all_correct
    return comparisons


def analyze_detail(detail: pd.DataFrame, *, historical_version: str | None = None) -> dict[str, pd.DataFrame]:
    """所有比较从 Gold/Pred 重算，不使用已有 match_* 或 summary 中的正确数。"""
    data = prepare_detail(detail, historical_version=historical_version)
    tables: dict[str, pd.DataFrame] = {}
    overall_rows = []
    comparisons = compare_fields(data, historical_version=historical_version)
    all_correct = comparisons["all_fields_correct"]
    errors = []

    for field, (slug, labels) in FIELD_SPECS.items():
        if historical_version == "v1" and field == "反馈类型":
            labels = ["情绪或无效反馈" if x == "情绪/无效反馈" else x for x in labels]
        applicable = data[f"gold_{field}"].ne("不适用") if field == SEVERITY else pd.Series(True, index=data.index)
        gold = data.loc[applicable, f"gold_{field}"]
        prediction = data.loc[applicable, f"pred_{field}"]
        metrics, classes, confusion = classification_metrics(gold, prediction, labels)
        metrics["field"] = field
        if field == VALIDITY:
            positive = classes.loc[classes["label"].eq(True)].iloc[0]
            metrics.update({name: positive[name] for name in ["precision", "recall", "f1", "support"]})
        overall_rows.append(metrics)
        tables[f"metrics_{slug}"] = classes
        tables[f"confusion_{slug}"] = confusion

        matches = data[f"gold_{field}"].eq(data[f"pred_{field}"])
        if field in {"反馈类型", SEVERITY, "产品模块"}:
            wrong = data.loc[applicable & ~matches]
            for (gold_label, pred_label), group in wrong.groupby([f"gold_{field}", f"pred_{field}"], sort=True):
                errors.append({
                    "field": field, "gold": gold_label, "pred": pred_label,
                    "count": len(group), "sample_ids": ",".join(group["ID"].astype(str)),
                })

    comparisons["all_fields_correct"] = all_correct
    overall_rows.append({
        "field": "all_fields_correct", "n_samples": len(data),
        "correct": int(all_correct.sum()), "accuracy": float(all_correct.mean()),
    })
    tables["metrics_overall"] = pd.DataFrame(overall_rows)
    tables["all_fields_correct"] = comparisons
    tables["error_patterns"] = pd.DataFrame(
        errors, columns=["field", "gold", "pred", "count", "sample_ids"],
    ).sort_values(["count", "field", "gold", "pred"], ascending=[False, True, True, True]).reset_index(drop=True)
    return tables


def validate_run_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise argparse.ArgumentTypeError("run-name 只能使用简单目录名")
    if value.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise argparse.ArgumentTypeError("run-name 不能使用 Windows 保留目录名")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线分析已生成的 Evaluation 明细；不调用模型")
    parser.add_argument("--run-name", required=True, type=validate_run_name)
    args = parser.parse_args(argv)
    run_dir = RESULTS_DIR / args.run_name
    detail_path = run_dir / "evaluation_detail.xlsx"
    tables = analyze_detail(pd.read_excel(detail_path))
    output_dir = run_dir / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(output_dir / f"{name}.csv", index=name.startswith("confusion_"), encoding="utf-8-sig")
    print(tables["metrics_overall"].to_string(index=False))
    print(f"离线分析已保存：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
