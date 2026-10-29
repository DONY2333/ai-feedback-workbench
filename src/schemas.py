"""分析结果的数据规范与基础校验。"""

from __future__ import annotations

from typing import Any


FEEDBACK_TYPES = ["Bug", "功能建议", "使用困难", "正向评价", "情绪/无效反馈"]
SENTIMENTS = ["正向", "中性", "负向"]
SEVERITIES = ["高", "中", "低", "不适用"]
PRODUCT_MODULES = [
    "其他", "账号/登录/验证", "安装/更新", "性能/稳定性", "通知",
    "国际化/翻译", "聊天/消息", "文档/编辑", "文件/分享", "兼容性/多端",
    "客户管理/外部联系", "工作流/权限/组织", "浏览器/外链", "日历", "邮件",
    "搜索", "会议/通话", "表格/Base", "风控/申诉",
]

# 仅用于兼容现有本地 Mock 的模块名称；真实模型由 JSON Schema 限定标签。
MODULE_ALIASES = {
    "消息通知": "通知",
    "登录与账号": "账号/登录/验证",
    "文档": "文档/编辑",
    "会议": "会议/通话",
    "联系人": "客户管理/外部联系",
    "链接与浏览器": "浏览器/外链",
    "表情与提及": "聊天/消息",
    "性能与稳定性": "性能/稳定性",
}

RESULT_FIELDS = [
    "是否为有效产品反馈",
    "反馈类型",
    "产品模块",
    "用户场景",
    "核心痛点",
    "情绪倾向",
    "严重程度",
    "原始评论证据",
]

DEFAULT_RESULT = {
    "是否为有效产品反馈": False,
    "反馈类型": "情绪/无效反馈",
    "产品模块": "其他",
    "用户场景": "未识别",
    "核心痛点": "未识别",
    "情绪倾向": "中性",
    "严重程度": "不适用",
    "原始评论证据": "",
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "是", "有效"}


def normalize_result(data: dict[str, Any], original_comment: str) -> dict[str, Any]:
    """把模型结果收敛到固定字段和枚举，避免脏 JSON 进入数据表。"""
    result = DEFAULT_RESULT.copy()
    result.update({key: data.get(key) for key in RESULT_FIELDS if data.get(key) is not None})

    result["是否为有效产品反馈"] = _as_bool(result["是否为有效产品反馈"])
    if result["反馈类型"] == "情绪或无效反馈":
        result["反馈类型"] = "情绪/无效反馈"
    if result["反馈类型"] not in FEEDBACK_TYPES:
        result["反馈类型"] = "情绪/无效反馈"
    if result["情绪倾向"] not in SENTIMENTS:
        result["情绪倾向"] = "中性"
    if result["严重程度"] not in SEVERITIES:
        result["严重程度"] = "低"
    if result["反馈类型"] in {"正向评价", "情绪/无效反馈"}:
        result["严重程度"] = "不适用"
    if result["反馈类型"] == "情绪/无效反馈":
        result["是否为有效产品反馈"] = False

    for key in ["产品模块", "用户场景", "核心痛点", "原始评论证据"]:
        result[key] = str(result[key] or "").strip()
    result["产品模块"] = result["产品模块"] or "其他"
    result["产品模块"] = MODULE_ALIASES.get(result["产品模块"], result["产品模块"])
    if result["产品模块"] not in PRODUCT_MODULES:
        result["产品模块"] = "其他"
    result["用户场景"] = result["用户场景"] or "未识别"
    result["核心痛点"] = result["核心痛点"] or "未识别"

    evidence = result["原始评论证据"]
    if not evidence or evidence not in original_comment:
        result["原始评论证据"] = original_comment[:160]
    return result

