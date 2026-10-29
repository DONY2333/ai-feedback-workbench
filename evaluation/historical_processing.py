"""历史 Experiment 专用策略；与生产 normalize_result 隔离，不宣称完整源码复原。"""

from copy import deepcopy

from evaluation.analyze_results import parse_boolean
from evaluation.langfuse_one import calculate_scores

# 锁定已确认的 V2/V3 规则，不动态引用生产 taxonomy/normalizer。
TYPES = ["Bug", "功能建议", "使用困难", "正向评价", "情绪/无效反馈"]
MODULES = [
    "其他", "账号/登录/验证", "安装/更新", "性能/稳定性", "通知", "国际化/翻译",
    "聊天/消息", "文档/编辑", "文件/分享", "兼容性/多端", "客户管理/外部联系",
    "工作流/权限/组织", "浏览器/外链", "日历", "邮件", "搜索", "会议/通话", "表格/Base", "风控/申诉",
]
ALIASES = {
    "消息通知": "通知", "登录与账号": "账号/登录/验证", "文档": "文档/编辑",
    "会议": "会议/通话", "联系人": "客户管理/外部联系", "链接与浏览器": "浏览器/外链",
    "表情与提及": "聊天/消息", "性能与稳定性": "性能/稳定性",
}
DEFAULTS = {
    "是否为有效产品反馈": False, "反馈类型": "情绪/无效反馈", "产品模块": "其他",
    "用户场景": "未识别", "核心痛点": "未识别", "情绪倾向": "中性",
    "严重程度": "不适用", "原始评论证据": "",
}
FIELDS = ["是否为有效产品反馈", "反馈类型", "产品模块", "严重程度"]
SCORING_VERSION = "historical-exact-match-v1"


def version_metadata(version: str) -> dict:
    if version not in {"v1", "v2", "v3"}:
        raise ValueError("必须显式指定 v1/v2/v3")
    limitation = (
        "无历史 normalization 源码与完整原始响应；V1 仅保守保留历史自由文本、原严重程度和标签，不推断未知修复。"
        if version == "v1" else
        "锁定当前可确认的正式标准化规则；历史运行时源码和完整原始响应未存档，不能证明全部异常分支的历史行为。"
    )
    return {
        "prompt_version": version, "normalization_version": f"{version}-known-rules-1",
        "scoring_version": SCORING_VERSION, "normalization_reproduction": "partial",
        "normalization_limitation": limitation, "score_input_source": "normalized_output",
    }


def normalize_v1(raw: dict, comment: str = "") -> dict:
    """无语义修补：自由模块、旧类型、原严重程度均原样保留。"""
    result = deepcopy(raw)
    result["是否为有效产品反馈"] = parse_boolean(result["是否为有效产品反馈"])
    return result


def _normalize_confirmed_formal(raw: dict, comment: str) -> dict:
    """V2/V3 已确认相同的规则快照；不调用当前生产 normalizer。"""
    result = deepcopy(DEFAULTS)
    result.update({key: raw[key] for key in DEFAULTS if raw.get(key) is not None})
    result["是否为有效产品反馈"] = str(result["是否为有效产品反馈"]).strip().lower() in {
        "true", "1", "yes", "是", "有效",
    }
    if result["反馈类型"] == "情绪或无效反馈":
        result["反馈类型"] = "情绪/无效反馈"
    if result["反馈类型"] not in TYPES:
        result["反馈类型"] = "情绪/无效反馈"
    if result["情绪倾向"] not in {"正向", "中性", "负向"}:
        result["情绪倾向"] = "中性"
    if result["严重程度"] not in {"高", "中", "低", "不适用"}:
        result["严重程度"] = "低"
    if result["反馈类型"] in {"正向评价", "情绪/无效反馈"}:
        result["严重程度"] = "不适用"
    if result["反馈类型"] == "情绪/无效反馈":
        result["是否为有效产品反馈"] = False
    for key in ("产品模块", "用户场景", "核心痛点", "原始评论证据"):
        result[key] = str(result[key] or "").strip()
    module = ALIASES.get(result["产品模块"], result["产品模块"])
    result["产品模块"] = module if module in MODULES else "其他"
    for key in ("用户场景", "核心痛点"):
        result[key] = result[key] or "未识别"
    if not result["原始评论证据"] or result["原始评论证据"] not in comment:
        result["原始评论证据"] = comment[:160]
    return result


def normalize_v2(raw: dict, comment: str) -> dict:
    return _normalize_confirmed_formal(raw, comment)


def normalize_v3(raw: dict, comment: str) -> dict:
    return _normalize_confirmed_formal(raw, comment)


def process_output(raw: dict, comment: str, version: str) -> dict:
    metadata = version_metadata(version)
    normalizers = {"v1": normalize_v1, "v2": normalize_v2, "v3": normalize_v3}
    if any(key not in raw or raw[key] is None for key in FIELDS):
        raise ValueError("原始结果缺少评分字段")
    normalized = normalizers[version](deepcopy(raw), comment)
    return {**metadata, "raw_output": deepcopy(raw), "normalized_output": normalized,
            "score_input": deepcopy(normalized)}


def score_output(gold, score_input: dict, version: str) -> dict[str, int]:
    version_metadata(version)
    projected_gold = gold.copy()
    # Dataset 使用现行名称；V1 恢复存档中的 Gold 拼写，不改模型输出或 Gold 文件。
    if version == "v1" and projected_gold["gold_反馈类型"] == "情绪/无效反馈":
        projected_gold["gold_反馈类型"] = "情绪或无效反馈"
    return calculate_scores(projected_gold, score_input, historical_version=version)
