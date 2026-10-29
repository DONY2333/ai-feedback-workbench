"""新上传批次的本地边界：读取、清洗、分析检查点与手动 E1/E2 更新。"""

from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
from uuid import uuid4

import pandas as pd

from src.analyzer import analyze_comments
from src.data_loader import COMMENT_ALIASES, PRODUCT_ALIASES, _find_column
from src.issue_clustering import as_bool
from src.schemas import RESULT_FIELDS

RUNS_DIR = Path(__file__).resolve().parents[1] / "analysis/results/user_runs"
BATCH_PATTERN = r"batch-\d{8}T\d{12}-[a-f0-9]{8}"


def now():
    return datetime.now(timezone.utc).isoformat()


def batch_id():
    return "batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid4().hex[:8]


def run_path(identifier):
    if not re.fullmatch(BATCH_PATTERN, identifier):
        raise ValueError("无效 batch_id")
    root = RUNS_DIR.resolve()
    path = (root / identifier).resolve()
    if path.parent != root:
        raise ValueError("批次路径越界")
    return path


def read_upload(data, filename, sheet=0):
    """只解析字节，不创建模型；Excel 由页面明确选择工作表。"""
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("文件超过20MB，请拆分后上传")
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".csv":
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    frame = pd.read_csv(BytesIO(data), encoding=encoding, dtype=str, keep_default_na=False)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError("CSV 编码无法识别，请另存 UTF-8")
        elif suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(BytesIO(data), sheet_name=sheet, dtype=str, keep_default_na=False)
        else:
            raise ValueError("只支持 CSV / XLSX / XLS")
        frame.columns = frame.columns.map(str)
        if frame.empty or len(set(frame.columns)) != len(frame.columns):
            raise ValueError("文件为空或列名重复")
        return frame
    except Exception as error:
        raise ValueError(f"文件无法读取（{type(error).__name__}），请检查文件格式、编码或工作表。") from None


def detect_columns(frame):
    cols = frame.columns.tolist()
    return {"comment": _find_column(cols, COMMENT_ALIASES),
            "product": _find_column(cols, PRODUCT_ALIASES),
            "id": next((c for c in cols if c.strip().lower() in {"id", "feedback_id", "反馈id", "评论id"}), None)}


def prepare_feedback(frame, identifier, comment_column, product_column=None, id_column=None, default_product=""):
    """保留原文；空评论移除，同产品规范化重复移除，缺产品/重ID明确拒绝。"""
    if comment_column not in frame.columns:
        raise ValueError("缺少评论列，请选择评论原文列")
    selected = [c for c in (comment_column, product_column, id_column) if c is not None]
    if any(c not in frame.columns for c in selected) or len(set(selected)) != len(selected):
        raise ValueError("产品、评论和 ID 必须选择不同且存在的列")
    def value(v):
        return "" if pd.isna(v) else str(v).strip()
    records = []
    empty = missing = 0
    for position, (_, row) in enumerate(frame.iterrows(), 2):
        comment = value(row[comment_column])
        if not comment:
            empty += 1
            continue
        product = value(row[product_column]) if product_column else ""
        product = product or default_product.strip()
        missing += int(not product)
        original_id = value(row[id_column]) if id_column else ""
        records.append({"ID": original_id or f"{identifier}-R{position:06d}", "产品": product,
                        "评论原文": comment, "来源行号": position})
    result = pd.DataFrame(records, columns=["ID", "产品", "评论原文", "来源行号"])
    if missing:
        raise ValueError(f"{missing} 条评论缺失产品，请选择产品列或填写缺失产品默认值")
    if result.empty:
        raise ValueError("移除空评论后没有有效行")
    # 和现有清洗相同的保守规范化键，不改写原评论文本。
    key = result["产品"].str.lower().str.replace(r"\s+", "", regex=True) + "|" + result["评论原文"].str.lower().str.replace(
        r"[\s\.,!?，。！？、；;：:\"“”'‘’（）()\[\]{}<>《》…—_-]+", "", regex=True)
    duplicate = int(key.duplicated().sum())
    result = result.loc[~key.duplicated()].copy().reset_index(drop=True)
    if result["ID"].duplicated().any():
        raise ValueError("清洗后仍有重复 ID，请修正 ID 列或选择生成临时 ID")
    return result, {"读取行数": len(frame), "空评论数": empty, "重复评论数": duplicate, "清洗后行数": len(result)}


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def save_frame(path, frame):
    # JSON 保留原始字符；CSV 防公式注入，仅供展示/导出，不读回参与计算。
    safe = frame.map(lambda v: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
    safe.to_csv(path.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    save_json(path.with_suffix(".json"), json.loads(frame.to_json(orient="records", force_ascii=False)))


def analyze_batch(comments, identifier, filename, client):
    """只由 Analyze 按钮调用；逐条复用现有分析器并落盘，失败不丢已成功行。"""
    if client.config.mode != "mock":
        raise ValueError("当前演示导入仅支持 Demo / Mock，不允许真实 API")
    path = run_path(identifier)
    path.mkdir(parents=True, exist_ok=False)
    meta = {"batch_id": identifier, "source_filename": Path(filename).name, "created_at": now(),
            "feedback_count": len(comments), "analysis_mode": "mock", "model": "mock-rules",
            "analysis_status": "running", "issue_analysis_status": "not_started", "issue_count": 0}
    save_json(path / "metadata.json", meta)
    save_frame(path / "input_feedback", comments)
    records = []
    for _, row in comments.iterrows():
        try:
            record = analyze_comments(pd.DataFrame([row]), client).iloc[0].to_dict()
            if record.get("分析来源") != "mock":
                raise ValueError("结果来源与 Mock 模式不一致")
            record["错误信息"] = ""
        except Exception as error:
            # 不保存外部异常原文，避免潜在凭据；错误类型+行ID可定位。
            record = {**row.to_dict(), **{f: None for f in RESULT_FIELDS}, "分析来源": "",
                      "错误信息": f"{type(error).__name__}：此条结构化分析失败，请检查输入或本地配置。"}
        records.append(record)
        save_frame(path / "structured_feedback", pd.DataFrame(records))
    meta.update(analysis_status="failed" if any(r["错误信息"] for r in records) else "completed",
                success_count=sum(not r["错误信息"] for r in records), updated_at=now())
    save_json(path / "metadata.json", meta)
    return pd.DataFrame(records), meta


def load_batch(identifier):
    path = run_path(identifier)
    return json.loads((path / "metadata.json").read_text(encoding="utf-8")), pd.DataFrame(
        json.loads((path / "structured_feedback.json").read_text(encoding="utf-8")))


def list_batches():
    batches = []
    for file in sorted(RUNS_DIR.glob("batch-*/metadata.json"), reverse=True):
        try:
            meta = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or not {"batch_id", "issue_analysis_status", "analysis_status"}.issubset(meta):
                continue
            if run_path(meta["batch_id"]) != file.parent.resolve():
                continue
            batches.append(meta)
        except (OSError, ValueError, KeyError):
            continue
    return batches


def update_insights(identifier, excluded_ids=(), confirmed=False):
    """只有显式确认且无失败预测才运行 E1/E2；与历史目录无连接。"""
    if not confirmed:
        raise ValueError("请先确认结构化结果")
    meta, frame = load_batch(identifier)
    if meta["analysis_status"] != "completed" or frame["错误信息"].fillna("").ne("").any():
        raise ValueError("结构化分析包含失败，不允许更新 Issue")
    if set(excluded_ids) - set(frame["ID"]):
        raise ValueError("排除 ID 不存在")
    if meta["issue_analysis_status"] == "completed":
        raise ValueError("该批次洞察已保存，不允许覆盖；请新建批次")
    path = run_path(identifier)
    # 更新失败可手动再试：每次输出新目录，失败文件也保留。
    update_dir = path / ("insights-" + uuid4().hex[:8])
    meta.update(issue_analysis_status="running", excluded_ids=list(excluded_ids), review_confirmed_at=now())
    save_json(path / "metadata.json", meta)
    try:
        from analysis.run_issue_clustering import run_from_records
        from analysis.build_product_insights import build_for_batch
        rows = []
        for row in frame.to_dict("records"):
            if row["ID"] in excluded_ids:
                continue
            rows.append({"feedback_id": row["ID"], "product": row["产品"], "comment": row["评论原文"],
                         "valid": as_bool(row["是否为有效产品反馈"]), "feedback_type": row["反馈类型"],
                         "product_module": row["产品模块"], "core_user_pain": row["核心痛点"],
                         "severity": row["严重程度"], "user_scenario": row["用户场景"], "scenario_source": "batch_prediction",
                         "source_file": meta["source_filename"], "source_run": identifier,
                         "original_excel_row": str(row["来源行号"])})
        package = run_from_records(rows, update_dir / "e1")
        build_for_batch(package, update_dir / "e2")
        meta.update(issue_analysis_status="completed", issue_count=len(package["issues"]),
                    insights_directory=update_dir.name, confirmed_feedback_count=len(rows), updated_at=now())
    except Exception as error:
        meta.update(issue_analysis_status="failed", insight_error=f"{type(error).__name__}：洞察更新失败，可手动重试。", updated_at=now())
        save_json(path / "metadata.json", meta)
        raise ValueError("Structured analysis completed, but product insight update failed.") from None
    save_json(path / "metadata.json", meta)
    return meta


def read_insights(identifier):
    meta, _ = load_batch(identifier)
    name = meta.get("insights_directory", "")
    if meta["issue_analysis_status"] != "completed" or not re.fullmatch(r"insights-[a-f0-9]{8}", name):
        raise ValueError("该批次尚无成功保存的 Product Insights")
    directory = run_path(identifier) / name
    return (meta, json.loads((directory / "e1/issue_clusters.json").read_text(encoding="utf-8")),
            pd.DataFrame(json.loads((directory / "e2/product_comparison.json").read_text(encoding="utf-8")),
                         columns=["product", "product_module", "issue_feedback_count", "percentage", "high_severity_count",
                                  "bug_count", "usage_difficulty_count", "feature_request_count"]),
            json.loads((directory / "e2/review_audit.json").read_text(encoding="utf-8")))
