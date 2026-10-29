"""模型调用封装：支持真实 OpenAI 兼容 API 和本地 mock。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from src.schemas import FEEDBACK_TYPES, PRODUCT_MODULES, SENTIMENTS, SEVERITIES, normalize_result


load_dotenv()


SYSTEM_PROMPT = """你是资深产品用户反馈分析师。请只依据评论原文提取信息，不要臆测。
输出一个 JSON 对象，字段必须为：是否为有效产品反馈（布尔值）、反馈类型、产品模块、用户场景、核心痛点、情绪倾向、严重程度、原始评论证据。

一、有效产品反馈
当评论明确描述具体产品功能、操作场景、问题、需求或具体优点时，是否为有效产品反馈为 true。
只要能定位到具体功能、操作、模块、场景或问题，即使句子很短、语法不完整、语言混杂，也可以算有效产品反馈。例如“上传 Base 太麻烦”“登录一直失败”“通知声音太小”均为有效。
只有完全无法定位具体产品信息的笼统表达、无关内容或缺少具体信息时，是否为有效产品反馈为 false，反馈类型为“情绪/无效反馈”。例如“很好”“很好用”“不错”“垃圾”“气死了”“不错，但应该还有更好的”均为无效。不要根据情绪强烈程度判断有效性。

二、反馈类型（只能选一个）
允许值：Bug、功能建议、使用困难、正向评价、情绪/无效反馈。
Bug：评论描述已有功能的故障或异常，实际行为偏离合理预期。例如“收不到消息推送”“验证码一直收不到”“修改文档后不同步”“启动就闪退”。不需要评论明确写出 Bug，也不要把功能故障归为使用困难。
使用困难：功能存在且没有明确故障证据，但用户因入口难找、步骤复杂、规则不清或交互难理解而难以完成任务。例如“不知道在哪里关闭通知”“权限设置太复杂，不知道怎么给同事共享”。
边界补充：功能完全失效、行为异常、明显违背正常预期，判为 Bug；功能仍然存在并可使用，只是难找、难操作、效果较差、体验不佳，优先判为使用困难。例如“完全收不到通知”→ Bug；“通知声音太小”→ 使用困难；“聊天记录搜索很难找到”→ 使用困难；“点击后报错/无法使用”→ Bug。
功能建议：明确希望新增当前缺少的能力、选项或改进正常工作的体验。例如“希望支持定时发送”“请增加批量导出”。“希望修复收不到通知”仍是 Bug，不因出现“希望”而归为功能建议。
正向评价：明确肯定具体功能或场景，例如“多人同时编辑文档很方便，省去了反复传文件”。没有具体功能或场景的夸赞归为“情绪/无效反馈”。
情绪/无效反馈：仅情绪、笼统评价、无关内容或信息不足，是否为有效产品反馈必须为 false。
评论包含补充、纠正、自我否定或更新时，若后文明确说明前面的问题并非该产品导致，或撤销之前的判断，以后文最新结论作为主要依据。已明确排除的问题，不再作为 Bug 判断依据，也不参与下述多反馈优先级排序。例如“一开始以为 @ 功能有问题，后来发现是 Samsung Keyboard 的问题。希望能接入更多 meme 服务。”应根据最终有效诉求判为功能建议，不把已排除的 @ 问题判为 Bug。
一条评论有多种反馈时，只选证据最明确的主要反馈；主要反馈无法区分时，按 Bug、使用困难、功能建议、正向评价的顺序选一个。不因附带夸赞忽略主要问题。

三、产品模块（只能从以下 19 个标签中选择一个，必须原样输出）
其他、账号/登录/验证、安装/更新、性能/稳定性、通知、国际化/翻译、聊天/消息、文档/编辑、文件/分享、兼容性/多端、客户管理/外部联系、工作流/权限/组织、浏览器/外链、日历、邮件、搜索、会议/通话、表格/Base、风控/申诉。
按主要反馈涉及的功能选择最具体的标签。消息提醒、推送归“通知”；消息发送、会话、表情和提及归“聊天/消息”；登录或验证码归“账号/登录/验证”；封禁、异常风控和申诉归“风控/申诉”；安装升级归“安装/更新”；崩溃、卡顿、耗电等整体运行问题归“性能/稳定性”。仅当明确涉及跨设备或平台差异时选“兼容性/多端”。没有足够依据归类时选“其他”。不得生成新标签或组合多个标签。
易混淆映射补充：网络错误、连接失败、卡顿、崩溃、加载异常 → 性能/稳定性；聊天归档、会话整理、消息管理 → 聊天/消息；通知推送、后台提醒、消息提醒 → 通知；登录、验证码、账号验证 → 账号/登录/验证。仍按主要反馈选择一个标签，不因附带提及其他功能而改变归类。

四、严重程度（只能选：高、中、低、不适用）
先看反馈类型：正向评价和情绪/无效反馈的严重程度必须是“不适用”，不因措辞强烈而改变。Bug、使用困难和功能建议按实际任务影响选择高、中、低，不使用“不适用”。
高：关键工作任务被阻断、核心能力持续或频繁失效，或存在明确的数据丢失、安全风险；没有可靠替代路径或替代路径严重影响及时性。例如“经常收不到工作消息，必须打开 App 才能看到”“一直收不到验证码，无法登录”“编辑内容丢失无法恢复”。
中：任务仍能完成，但步骤明显增加、效率或体验明显下降，存在可行替代路径；缺少证据证明关键任务被阻断。例如“共享权限入口难找，每次要找很久”“不能批量导出，只能逐条下载”。
低：轻微外观、措辞或便利性问题，不妨碍任务完成，或属于可选增强需求。例如“按钮间距有点小但可以正常点击”“希望增加更多主题颜色”。
功能建议补充：不因“用户想要”就自动判中或高，应根据缺失功能对核心任务的影响判断。非核心增强型建议通常为低；核心工作流明显受阻可判中；核心能力基本不可用或持续失败才判高。
不要仅根据“垃圾”“非常”“严重”等情绪词升级严重程度。信息不足时依据已陈述的影响保守判断，不编造频率、损失或业务后果。

五、其余字段
情绪倾向只能是：正向、中性、负向，与严重程度分开判断。
用户场景和核心痛点使用简洁中文；评论没有提供的信息写“未识别”。正向评价可在核心痛点中概述被认可的具体体验，不编造问题。
原始评论证据必须直接摘自原评论，尽量简短；不得用翻译、改写或推断代替证据。
只输出符合 JSON Schema 的 JSON 对象，不输出 Markdown 或额外解释。"""


JSON_SCHEMA = {
    "name": "feedback_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "是否为有效产品反馈": {"type": "boolean"},
            "反馈类型": {"type": "string", "enum": FEEDBACK_TYPES},
            "产品模块": {"type": "string", "enum": PRODUCT_MODULES},
            "用户场景": {"type": "string"},
            "核心痛点": {"type": "string"},
            "情绪倾向": {"type": "string", "enum": SENTIMENTS},
            "严重程度": {
                "type": "string",
                "enum": SEVERITIES,
                "description": "正向评价和情绪/无效反馈必须为不适用；其余类型按任务影响选择高、中、低。",
            },
            "原始评论证据": {"type": "string"},
        },
        "required": [
            "是否为有效产品反馈", "反馈类型", "产品模块", "用户场景",
            "核心痛点", "情绪倾向", "严重程度", "原始评论证据",
        ],
        "additionalProperties": False,
    },
}


def parse_json_object(raw_text: str) -> dict[str, Any]:
    """兼容 Markdown 代码块、前后解释文字等常见模型格式异常。"""
    text = (raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型响应中未找到 JSON 对象")
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型响应不是 JSON 对象")
    return value


@dataclass
class ModelConfig:
    mode: str = "mock"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    allow_mock_fallback: bool = True

    @classmethod
    def from_environment(
        cls,
        requested_mode: str = "auto",
        allow_mock_fallback: bool = True,
    ) -> "ModelConfig":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        mode = "api" if requested_mode == "api" or (requested_mode == "auto" and api_key) else "mock"
        return cls(
            mode=mode,
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            allow_mock_fallback=allow_mock_fallback,
        )


class FeedbackModelClient:
    def __init__(self, config: ModelConfig):
        self.config = config
        self._client = None

    def analyze(self, comment: str, product: str = "") -> tuple[dict[str, Any], str]:
        if self.config.mode == "mock":
            return normalize_result(self._mock_analysis(comment), comment), "mock"
        try:
            raw = self._call_api(comment, product)
            return normalize_result(parse_json_object(raw), comment), "api"
        except Exception as exc:  # 单条失败不应中断整个批次
            if not self.config.allow_mock_fallback:
                raise RuntimeError(f"真实 API 分析失败：{exc}") from exc
            fallback = self._mock_analysis(comment)
            fallback["分析异常"] = str(exc)
            return normalize_result(fallback, comment), f"fallback: {type(exc).__name__}"

    def _call_api(self, comment: str, product: str) -> str:
        if not self.config.api_key:
            raise ValueError("未配置 OPENAI_API_KEY")
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

        response = self._client.chat.completions.create(
            model=self.config.model,
            temperature=0,
            response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"产品：{product or '未知'}\n评论原文：{comment}"},
            ],
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _mock_analysis(comment: str) -> dict[str, Any]:
        """可解释的关键词规则，只用于本地演示与界面联调。"""
        text = comment.lower()

        module_rules = {
            "消息通知": ["通知", "推送", "消息", "notification", "notify"],
            "登录与账号": ["登录", "登入", "验证码", "登出", "账号", "login", "sign in"],
            "文档": ["文档", "文件", "文章", "同步", "docs", "document"],
            "日历": ["日历", "日程", "calendar", "schedule"],
            "会议": ["会议", "视频", "音频", "meeting", "call"],
            "搜索": ["搜索", "search"],
            "联系人": ["通讯录", "联系人", "contact"],
            "链接与浏览器": ["浏览器", "链接", "browser", "link"],
            "表情与提及": ["表情", "emoji", "mention", "tagging", "@"],
            "性能与稳定性": ["崩溃", "卡顿", "闪退", "crash", "slow", "lag"],
        }
        module = next((name for name, words in module_rules.items() if any(word in text for word in words)), "其他")

        bug_words = ["bug", "不能", "无法", "收不到", "失败", "崩溃", "闪退", "异常", "不工作", "doesn't", "not working", "can't", "cannot", "problem"]
        request_words = ["希望", "建议", "请增加", "应该", "支持", "enable", "please add", "feature", "option", "wish"]
        difficulty_words = ["怎么", "找不到", "复杂", "难用", "不友好", "困惑", "difficult", "confusing", "hard to"]
        positive_words = ["好用", "不错", "喜欢", "优秀", "方便", "amazing", "love", "great", "excellent", "便利"]
        negative_words = ["糟糕", "失望", "生气", "烦", "垃圾", "annoy", "disappoint", "terrible", "困扰", "非常困"]

        has_bug = any(word in text for word in bug_words)
        has_request = any(word in text for word in request_words)
        has_difficulty = any(word in text for word in difficulty_words)
        has_positive = any(word in text for word in positive_words)

        if has_bug:
            feedback_type = "Bug"
        elif has_request:
            feedback_type = "功能建议"
        elif has_difficulty:
            feedback_type = "使用困难"
        elif has_positive:
            feedback_type = "正向评价"
        else:
            feedback_type = "情绪/无效反馈"

        if feedback_type == "正向评价" and module == "其他":
            feedback_type = "情绪/无效反馈"
        valid = feedback_type != "情绪/无效反馈"
        if has_positive and not (has_bug or has_request or has_difficulty):
            sentiment = "正向"
        elif any(word in text for word in negative_words) or has_bug:
            sentiment = "负向"
        else:
            sentiment = "中性"

        severe_words = ["数据丢失", "安全", "泄露", "无法使用", "完全不能", "工作中断", "半年以上", "基础能力"]
        high_words = ["经常", "一直", "每次", "严重", "重要", "very", "always"]
        if any(word in text for word in severe_words):
            severity = "高"
        elif has_bug and any(word in text for word in high_words):
            severity = "高"
        elif has_bug or has_difficulty:
            severity = "中"
        else:
            severity = "低"

        pain_points = {
            "消息通知": "消息通知不及时或无法收到",
            "登录与账号": "登录或账号验证失败",
            "文档": "文档内容同步或编辑异常",
            "日历": "日程日期或日历交互异常",
            "会议": "会议或通话体验不稳定",
            "搜索": "搜索结果或查找效率不佳",
            "联系人": "联系人管理或查找不便",
            "链接与浏览器": "链接打开方式受限",
            "表情与提及": "提及或表情功能异常",
            "性能与稳定性": "应用性能或稳定性问题",
            "其他": "评论未提供可归类的具体问题",
        }
        scenario = "日常办公协作"
        if module == "消息通知":
            scenario = "移动端接收工作消息"
        elif module == "文档":
            scenario = "多人编辑或查看云文档"
        elif module == "日历":
            scenario = "创建或修改日程"
        elif module == "登录与账号":
            scenario = "登录应用或完成账号验证"

        return {
            "是否为有效产品反馈": valid,
            "反馈类型": feedback_type,
            "产品模块": module,
            "用户场景": scenario,
            "核心痛点": pain_points[module],
            "情绪倾向": sentiment,
            "严重程度": severity,
            "原始评论证据": comment[:160],
        }

