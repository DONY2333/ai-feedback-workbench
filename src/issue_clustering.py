"""离线候选问题归并。只读预测，不调用模型；所有自动结果必须人工复核。"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

import pandas as pd

from src.schemas import FEEDBACK_TYPES, PRODUCT_MODULES, SEVERITIES

ISSUE_FIELDS = [
    "issue_id", "product", "issue_title", "product_module", "issue_summary",
    "evidence_count", "evidence_ids", "representative_comments",
    "feedback_type_distribution", "severity_distribution", "main_user_scenarios",
    "core_user_pain", "review_status", "review_note", "merge_basis",
]
PROBLEM_TYPES = {"Bug", "使用困难", "功能建议"}
MAPPING_FIELDS = [
    "feedback_id", "product", "comment", "valid", "feedback_type", "product_module",
    "core_user_pain", "severity", "user_scenario", "scenario_source", "source_file",
    "source_run", "original_excel_row", "eligible", "issue_id", "exclusion_reason",
]


def text(value):
    return "" if pd.isna(value) else str(value)


def as_bool(value):
    token = str(value).strip().lower()
    if token not in {"true", "false", "1", "0", "是", "否"}:
        raise ValueError(f"无效的有效性标签: {value!r}")
    return token in {"true", "1", "是"}


def load_feedback(paths):
    """保留原始证据和来源；有意不读取任何 gold_/match_ 字段。"""
    rows = []
    for path in paths:
        path = Path(path)
        frame = pd.read_excel(path)
        required = ["ID", "产品", "评论原文", "分析来源", "prompt_version"] + [
            "pred_" + field for field in ["是否为有效产品反馈", "反馈类型", "产品模块", "核心痛点", "严重程度"]
        ]
        missing = set(required) - set(frame.columns)
        if missing:
            raise ValueError(f"{path.name} 缺少列: {sorted(missing)}")
        for _, row in frame.iterrows():
            if text(row["分析来源"]) != "api" or text(row["prompt_version"]) != "v3":
                raise ValueError("只接受已保存的正式 V3 API 预测")
            if text(row.get("错误信息", "")):
                raise ValueError("输入包含失败预测")
            result = {
                "feedback_id": text(row["ID"]).strip(), "product": text(row["产品"]),
                "comment": text(row["评论原文"]),
                "valid": as_bool(row["pred_是否为有效产品反馈"]),
                "feedback_type": text(row["pred_反馈类型"]),
                "product_module": text(row["pred_产品模块"]),
                "core_user_pain": text(row["pred_核心痛点"]),
                "severity": text(row["pred_严重程度"]),
                "user_scenario": text(row.get("pred_用户场景", "")),
                "scenario_source": "saved_prediction" if "pred_用户场景" in frame else "not_saved",
                "source_file": str(path.resolve()), "source_run": text(row.get("run_name", path.parent.name)),
                "original_excel_row": text(row.get("原始Excel行号", "")),
            }
            rows.append(result)
    validate_feedback(rows)
    return rows


def validate_feedback(rows):
    ids = [row["feedback_id"] for row in rows]
    if len(ids) != len(set(ids)) or any(not i.strip() for i in ids):
        raise ValueError("反馈 ID 缺失或重复")
    for row in rows:
        if not row["product"].strip() or not row["comment"].strip():
            raise ValueError("产品/原始评论缺失")
        if row["feedback_type"] not in FEEDBACK_TYPES or row["product_module"] not in PRODUCT_MODULES or row["severity"] not in SEVERITIES:
            raise ValueError("预测标签不属于正式 taxonomy")
        if not isinstance(row["valid"], bool):
            raise ValueError("valid 必须为 bool")


def eligible(row):
    return row["valid"] and row["feedback_type"] in PROBLEM_TYPES


# 小范围语义锚点：模块与对象、症状同时约束；不是新增单条分类 taxonomy。
# 标题只描述文本已有症状，不推断原因、平台或解决方案。
ANCHORS = [
    ("通知", r"通知|推送|提醒|消息", r"收不到|不送达|不推送|接收异常|不显示|未显示|不提醒", "消息通知送达或显示异常"),
    ("通知", r"声音|铃声", r"小|低|听不", "消息提醒音量不足"),
    ("账号/登录/验证", r"验证码", r"收不到|不送达|不发|未收到|无法收到", "账号验证码获取失败"),
    ("安装/更新", r"附加文件|额外文件", r"失败|循环|0%|无法|反复", "安装更新附加文件下载异常"),
    ("国际化/翻译", r"语言|中文|英语", r"切换|切回|仍|变成", "界面语言切换异常"),
]


def merge_key(row):
    pain = row["core_user_pain"].strip()
    # 有痛点时只看痛点，避免从整条原文重新引入已被撤销的问题。
    candidate = pain or row["comment"]
    lane = "suggestion" if row["feedback_type"] == "功能建议" else "problem"
    if lane == "problem":
        for module, obj, symptom, title in ANCHORS:
            if row["product_module"] == module and re.search(obj, candidate) and re.search(symptom, candidate):
                return lane, "anchor:" + title, title
    compact = re.sub(r"[\W_]+", "", pain).lower()
    # 保守处理：没有明确锚点时只合并完全一致且不太短的痛点。
    key = "exact:" + compact if len(compact) >= 8 else "single:" + row["feedback_id"]
    return lane, key, ""


def build_clusters(rows):
    validate_feedback(rows)
    groups = {}
    for row in sorted(rows, key=lambda r: r["feedback_id"]):
        if eligible(row):
            lane, key, title = merge_key(row)
            group = (row["product"], row["product_module"], lane, key)
            groups.setdefault(group, {"rows": [], "title": title})["rows"].append(row)
    issues = []
    for group, data in sorted(groups.items()):
        members = data["rows"]
        fingerprint = json.dumps([group[:2], [r["feedback_id"] for r in members]], ensure_ascii=False)
        issue_id = "ISS-" + sha256(fingerprint.encode()).hexdigest()[:12]
        title = data["title"] or f'{members[0]["product_module"]}：{members[0]["core_user_pain"] or "具体问题待复核（见原文）"}'
        issues.append(assemble_issue(issue_id, title, members, "needs_review", "", group[-1].split(":")[0]))
    return issues


def assemble_issue(issue_id, title, members, status, note, basis):
    pains = list(dict.fromkeys(r["core_user_pain"] for r in members if r["core_user_pain"]))
    return dict(zip(ISSUE_FIELDS, [
        issue_id, members[0]["product"], title, members[0]["product_module"],
        "；".join(pains), len(members), [r["feedback_id"] for r in members],
        [{"feedback_id": r["feedback_id"], "comment": r["comment"]} for r in members[:3]],
        dict(Counter(r["feedback_type"] for r in members)),
        dict(Counter(r["severity"] for r in members)),
        list(dict.fromkeys(r.get("user_scenario", "") for r in members if r.get("user_scenario"))),
        pains, status, note, basis,
    ]))


def review_template(issues):
    return {"review_version": 1, "issues": [
        {key: issue[key] for key in ["issue_id", "issue_title", "evidence_ids", "review_status", "review_note"]}
        for issue in issues
    ]}


def apply_review(rows, review):
    """通过成员列表支持改标题、合并、拆分、移动；所有统计从原始证据重建。"""
    validate_feedback(rows)
    if review.get("review_version") != 1:
        raise ValueError("不支持的 review_version")
    lookup = {r["feedback_id"]: r for r in rows if eligible(r)}
    seen, issue_ids, result = set(), set(), []
    for issue in review["issues"]:
        ids = issue["evidence_ids"]
        issue_id = issue["issue_id"]
        if not issue_id.strip() or issue_id in issue_ids or not ids or not issue["issue_title"].strip():
            raise ValueError("Issue ID/标题/成员不合法")
        issue_ids.add(issue_id)
        if len(ids) != len(set(ids)) or seen.intersection(ids) or set(ids) - lookup.keys():
            raise ValueError("证据 ID 重复或未知/不符合准入条件")
        members = [lookup[i] for i in sorted(ids)]
        if len({(r["product"], r["product_module"]) for r in members}) != 1:
            raise ValueError("禁止跨产品/模块归并")
        if len({r["feedback_type"] == "功能建议" for r in members}) != 1:
            raise ValueError("建议和现有故障/困难需分开复核")
        status = issue.get("review_status", "needs_review")
        if status not in {"needs_review", "reviewed"}:
            raise ValueError("不合法的复核状态")
        seen.update(ids)
        result.append(assemble_issue(issue_id, issue["issue_title"], members, status, issue.get("review_note", ""), "human_review"))
    if seen != lookup.keys():
        raise ValueError("复核文件遗漏符合准入条件的反馈")
    return result


def package_results(rows, issues):
    # 即使调用方自行构造 issues，也必须通过证据分区校验。
    apply_review(rows, review_template(issues))
    assignments = {i: issue["issue_id"] for issue in issues for i in issue["evidence_ids"]}
    mapping = []
    for row in rows:
        reason = "" if eligible(row) else ("positive_separate" if row["feedback_type"] == "正向评价" else "invalid_or_not_problem")
        mapping.append({**row, "eligible": eligible(row), "issue_id": assignments.get(row["feedback_id"], ""), "exclusion_reason": reason})
    selected = [r for r in rows if eligible(r)]
    summary = {
        "input_count": len(rows), "eligible_count": len(selected), "excluded_count": len(rows) - len(selected),
        "coarse_group_count": len({(r["product"], r["product_module"]) for r in selected}),
        "issue_count": len(issues), "needs_review_count": sum(i["review_status"] == "needs_review" for i in issues),
        "eligible_by_product": dict(Counter(r["product"] for r in selected)),
        "missing_scenario_count": sum(not r.get("user_scenario") for r in rows),
        "model_api_calls": 0,
    }
    return {"schema_version": "e1-offline-v1", "method": "conservative_anchors_and_exact_pain", "issues": issues, "feedback_mapping": mapping, "summary": summary}


def export_package(package, directory):
    """CSV + JSON 是可复现离线交付格式；不覆盖已有运行目录。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    for name, value in [("issue_clusters.json", package), ("review.json", review_template(package["issues"]))]:
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tables = {
        "issue_clusters.csv": pd.DataFrame(package["issues"], columns=ISSUE_FIELDS),
        "feedback_mapping.csv": pd.DataFrame(package["feedback_mapping"], columns=MAPPING_FIELDS),
        "summary.csv": pd.DataFrame([{"metric": k, "value": v} for k, v in package["summary"].items()]),
    }
    def safe_cell(value):
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        # 防止 Excel 将原始评论等文本当作公式；JSON 保留完全原文。
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    for name, frame in tables.items():
        frame.map(safe_cell).to_csv(directory / name, index=False, encoding="utf-8-sig")
