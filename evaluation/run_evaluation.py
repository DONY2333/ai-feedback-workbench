"""按明确的 ID split 使用真实 LLM 评价 Gold Set。"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.model_client import FeedbackModelClient, ModelConfig  # noqa: E402


DEFAULT_GOLD_PATH = PROJECT_ROOT / "evaluation" / "gold_set_82.xlsx"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
DEFAULT_SPLIT = "dev"
SPLIT_RANGES = {
    "dev": [("L", 1, 20)],
    "holdout": [("L", 21, 43), ("W", 1, 39)],
    "all": [("L", 1, 43), ("W", 1, 39)],
}
DEFAULT_RUN_NAME = "v2"
PROMPT_VERSION = "v3"

GOLD_FIELD_ALIASES = {
    "gold_是否为有效产品反馈": [
        "人工-是否有效", "人工_是否有效", "人工是否有效", "是否有效", "是否为有效产品反馈",
    ],
    "gold_反馈类型": ["人工-反馈类型", "人工_反馈类型", "人工反馈类型", "反馈类型"],
    "gold_产品模块": ["人工-产品模块", "人工_产品模块", "人工产品模块", "产品模块"],
    "gold_核心痛点": ["人工-核心痛点", "人工_核心痛点", "人工核心痛点", "核心痛点"],
    "gold_严重程度": ["人工-严重程度", "人工_严重程度", "人工严重程度", "严重程度"],
}

INPUT_FIELD_ALIASES = {
    "ID": ["ID", "id", "编号", "评论ID"],
    "产品": ["产品", "产品名称", "应用", "product", "app"],
    "评论原文": ["评论原文", "评论", "用户评论", "反馈内容", "review", "comment"],
}

PREDICTION_FIELDS = [
    "是否为有效产品反馈",
    "反馈类型",
    "产品模块",
    "核心痛点",
    "严重程度",
]

BASE_EVALUATION_FIELDS = ["是否为有效产品反馈", "反馈类型", "严重程度"]
NOT_APPLICABLE_LABELS = {"", "不适用", "n/a", "na", "none", "null"}


class EvaluationError(RuntimeError):
    """Evaluation 不能继续时抛出；可携带已经生成的逐条结果。"""

    def __init__(self, message: str, detail: pd.DataFrame | None = None):
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class GoldSetInfo:
    sheet_name: str
    column_mapping: dict[str, str]
    evaluation_fields: list[str]


def _normalized_column_name(value: object) -> str:
    return str(value).strip().lower().replace(" ", "")


def _find_column(columns: list[object], aliases: list[str]) -> str | None:
    normalized = {_normalized_column_name(column): str(column) for column in columns}
    for alias in aliases:
        match = normalized.get(_normalized_column_name(alias))
        if match is not None:
            return match
    return None


def detect_column_mapping(columns: list[object]) -> dict[str, str]:
    """根据 Excel 实际列名识别输入字段和 Gold 标签字段。"""
    mapping: dict[str, str] = {}
    missing: list[str] = []

    for target, aliases in {**INPUT_FIELD_ALIASES, **GOLD_FIELD_ALIASES}.items():
        source = _find_column(columns, aliases)
        if source is None:
            if target == "产品":
                continue
            missing.append(target)
        else:
            mapping[target] = source

    if missing:
        raise ValueError(f"Gold Set 缺少必要字段：{', '.join(missing)}")
    return mapping


def _normalize_gold_validity(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"有效", "是", "true", "1", "yes"}:
        return True
    if text in {"无效", "否", "false", "0", "no"}:
        return False
    raise ValueError(f"无法识别人工有效性标签：{value}")


def _normalize_gold_feedback_type(value: Any) -> str:
    text = str(value).strip()
    aliases = {
        "情绪或无效反馈": "情绪/无效反馈",
        "情绪／无效反馈": "情绪/无效反馈",
    }
    return aliases.get(text, text)


def split_ids(split: str) -> list[str]:
    """split 仅基于固定 ID 范围，与 Excel 行号及顺序无关。"""
    if split not in SPLIT_RANGES:
        raise EvaluationError(f"未知 split：{split}")
    return [
        f"{prefix}{number:03d}"
        for prefix, start, end in SPLIT_RANGES[split]
        for number in range(start, end + 1)
    ]


def validate_ids(data: pd.DataFrame, expected_ids: list[str]) -> None:
    """严格检查 ID；不自动补齐、去重或忽略范围外的数据。"""
    if "ID" not in data.columns or data["ID"].isna().any():
        raise EvaluationError("Gold Set ID 缺失")
    ids = data["ID"].tolist()
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        raise EvaluationError("Gold Set ID 缺失或格式不符")
    if data["ID"].duplicated().any():
        raise EvaluationError("Gold Set ID 重复")
    missing = sorted(set(expected_ids) - set(ids))
    unexpected = sorted(set(ids) - set(expected_ids))
    if missing or unexpected or len(ids) != len(expected_ids):
        raise EvaluationError(
            f"Gold Set ID 范围不符：应有 {len(expected_ids)} 条，实际 {len(ids)} 条；"
            f"缺失={missing}；范围外或格式不符={unexpected}"
        )


def load_gold_set(
    path: str | Path = DEFAULT_GOLD_PATH,
    split: str = DEFAULT_SPLIT,
) -> tuple[pd.DataFrame, GoldSetInfo]:
    """检查完整的 82 个 ID，再按 split 返回完整标签数据。"""
    selected_ids = split_ids(split)
    gold_path = Path(path)
    if not gold_path.exists():
        raise FileNotFoundError(f"Gold Set 不存在：{gold_path}")

    workbook = pd.ExcelFile(gold_path)
    selected_frame: pd.DataFrame | None = None
    selected_sheet = ""
    selected_mapping: dict[str, str] | None = None
    errors: list[str] = []

    for sheet_name in workbook.sheet_names:
        frame = pd.read_excel(gold_path, sheet_name=sheet_name)
        try:
            mapping = detect_column_mapping(frame.columns.tolist())
        except ValueError as exc:
            errors.append(f"{sheet_name}: {exc}")
            continue
        selected_frame = frame
        selected_sheet = sheet_name
        selected_mapping = mapping
        break

    if selected_frame is None or selected_mapping is None:
        raise ValueError("未找到包含完整人工标签的工作表。" + "；".join(errors))

    rename_mapping = {source: target for target, source in selected_mapping.items()}
    prepared = selected_frame.rename(columns=rename_mapping).copy()
    validate_ids(prepared, split_ids("all"))
    prepared["原始Excel行号"] = prepared.index + 2
    prepared = prepared.set_index("ID").loc[selected_ids].reset_index()
    if "产品" not in prepared.columns:
        prepared["产品"] = "未知产品"

    required = ["评论原文", *GOLD_FIELD_ALIASES.keys()]
    for column in required:
        prepared[column] = prepared[column].map(lambda value: "" if pd.isna(value) else str(value).strip())
    if not prepared[required].ne("").all(axis=1).all():
        raise EvaluationError(f"{split} 中存在评论或人工标签缺失；未发送 API 请求")
    validate_ids(prepared, selected_ids)

    prepared["gold_是否为有效产品反馈"] = prepared["gold_是否为有效产品反馈"].map(
        _normalize_gold_validity
    )
    prepared["gold_反馈类型"] = prepared["gold_反馈类型"].map(_normalize_gold_feedback_type)

    evaluation_fields = [*BASE_EVALUATION_FIELDS, "产品模块"]
    info = GoldSetInfo(
        sheet_name=selected_sheet,
        column_mapping=selected_mapping,
        evaluation_fields=evaluation_fields,
    )
    return prepared, info


def calculate_accuracy(detail: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    """计算每个字段的简单一致率。"""
    rows: list[dict[str, object]] = []
    for field in fields:
        match_column = f"match_{field}"
        if match_column not in detail.columns:
            raise ValueError(f"缺少一致性字段：{match_column}")
        total = int(detail[match_column].notna().sum())
        correct = int(detail[match_column].fillna(False).astype(bool).sum())
        rows.append(
            {
                "字段名": field,
                "总样本数": total,
                "正确数": correct,
                "一致率": correct / total if total else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _is_evaluable_gold(field: str, value: object) -> bool:
    if field != "严重程度":
        return True
    return str(value).strip().lower() not in NOT_APPLICABLE_LABELS


def run_evaluation(
    gold_data: pd.DataFrame,
    info: GoldSetInfo,
    client: FeedbackModelClient,
    run_name: str = DEFAULT_RUN_NAME,
    prompt_version: str = PROMPT_VERSION,
    split: str = DEFAULT_SPLIT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """逐条调用真实 API；任何异常或非 API 来源都会终止 Evaluation。"""
    validate_run_name(run_name)
    if client.config.mode != "api":
        raise EvaluationError("Evaluation 必须使用 mode='api'")
    if client.config.allow_mock_fallback:
        raise EvaluationError("Evaluation 必须禁用 Mock fallback")
    if not client.config.api_key:
        raise EvaluationError("未配置 OPENAI_API_KEY")
    validate_ids(gold_data, split_ids(split))

    records: list[dict[str, object]] = []
    for _, row in gold_data.iterrows():
        record: dict[str, object] = {
            "ID": row.get("ID", ""),
            "原始Excel行号": row["原始Excel行号"],
            "产品": row.get("产品", "未知产品"),
            "评论原文": row["评论原文"],
            "分析来源": "",
            "模型名称": client.config.model,
            "run_name": run_name,
            "prompt_version": prompt_version,
            "model": client.config.model,
            "split": split,
            "错误信息": "",
        }
        for field in PREDICTION_FIELDS:
            record[f"gold_{field}"] = row[f"gold_{field}"]

        try:
            prediction, source = client.analyze(str(row["评论原文"]), str(row.get("产品", "")))
            record["分析来源"] = source
            if source != "api":
                raise RuntimeError(f"分析来源不是 api：{source}")
            for field in PREDICTION_FIELDS:
                record[f"pred_{field}"] = prediction[field]
            for field in info.evaluation_fields:
                if _is_evaluable_gold(field, record[f"gold_{field}"]):
                    record[f"match_{field}"] = record[f"gold_{field}"] == record[f"pred_{field}"]
                else:
                    record[f"match_{field}"] = None
            records.append(record)
        except Exception as exc:
            record["错误信息"] = str(exc)
            records.append(record)
            detail = pd.DataFrame(records)
            raise EvaluationError(
                f"ID={record['ID'] or '未知'}，Excel 行={record['原始Excel行号']}：{exc}",
                detail=detail,
            ) from exc

    detail = pd.DataFrame(records)
    summary = calculate_accuracy(detail, info.evaluation_fields)
    return detail, summary


def save_results(
    detail: pd.DataFrame,
    summary: pd.DataFrame | None,
    results_dir: str | Path = DEFAULT_RESULTS_DIR,
    run_name: str = DEFAULT_RUN_NAME,
) -> tuple[Path, Path | None]:
    directory = Path(results_dir) / validate_run_name(run_name)
    directory.mkdir(parents=True, exist_ok=True)
    detail_path = directory / "evaluation_detail.xlsx"
    summary_path = directory / "evaluation_summary.csv" if summary is not None else None

    detail.to_excel(detail_path, index=False, sheet_name="Evaluation Detail")
    if summary_path is not None:
        summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    return detail_path, summary_path


def validate_run_name(value: str) -> str:
    """版本名只允许简单目录名，避免结果写到 results 目录以外。"""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("run-name 必须为 1–64 位字母、数字、下划线或短横线，且以字母或数字开头")
    if value.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise ValueError("run-name 不能使用 Windows 保留目录名")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按 ID split 评价 Gold Set，并按运行版本保存结果")
    parser.add_argument(
        "--split", choices=list(SPLIT_RANGES), default=DEFAULT_SPLIT,
        help="dev=L001-L020（默认）；holdout=L021-L043 + W001-W039；all=L001-L043 + W001-W039",
    )
    parser.add_argument(
        "--run-name", type=validate_run_name, default=DEFAULT_RUN_NAME,
        help="结果子目录名称，默认 v2；只控制结果目录，不切换 Prompt",
    )
    parser.add_argument(
        "--prompt-version", default=PROMPT_VERSION,
        help="仅记录 Prompt 版本元数据，默认 v3；不加载或切换 Prompt",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    gold_data, info = load_gold_set(DEFAULT_GOLD_PATH, split=args.split)
    sample_range = " + ".join(
        f"{prefix}{start:03d}-{prefix}{end:03d}"
        for prefix, start, end in SPLIT_RANGES[args.split]
    )
    print(f"Split: {args.split}", flush=True)
    print(f"样本范围: {sample_range}", flush=True)
    print(f"样本数: {len(gold_data)}", flush=True)
    config = ModelConfig.from_environment("api", allow_mock_fallback=False)
    if not config.api_key:
        raise EvaluationError("未配置 OPENAI_API_KEY；Evaluation 未发送任何请求")

    client = FeedbackModelClient(config)
    try:
        detail, summary = run_evaluation(
            gold_data, info, client,
            run_name=args.run_name, prompt_version=args.prompt_version,
            split=args.split,
        )
    except EvaluationError as exc:
        if exc.detail is not None:
            detail_path, _ = save_results(exc.detail, None, DEFAULT_RESULTS_DIR, run_name=args.run_name)
            print(f"Evaluation 失败，错误明细已保存：{detail_path}", file=sys.stderr)
        raise

    detail_path, summary_path = save_results(detail, summary, DEFAULT_RESULTS_DIR, run_name=args.run_name)
    print(f"Gold Set 工作表：{info.sheet_name}")
    print(f"列映射：{info.column_mapping}")
    print(f"Evaluation 字段：{info.evaluation_fields}")
    print(f"逐条结果：{detail_path}")
    print(f"汇总结果：{summary_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvaluationError as exc:
        print(f"Evaluation 失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

