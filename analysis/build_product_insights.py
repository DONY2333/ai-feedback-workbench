"""E2 固定 E1 快照的离线产品解读；不重新归并、不读取 Gold、不调用模型。"""

from collections import Counter
from hashlib import sha256
import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.issue_clustering import eligible


# 以下为本轮阅读原始证据后的编辑决策，不是自动优先级模型。
DECISIONS = [
    dict(issue_id="ISS-3955d4f6ed9d", title="消息通知未送达或通知栏显示异常", priority="高",
         summary="5 条评论反映消息通知未到达、很快消失或未显示在通知栏；其中部分用户明确提到安卓设备、后台设置或手动进入应用才更新。",
         problem="用户在等待消息、后台使用或查看通知栏时，无法可靠获知新消息。L002 明确描述需要进入主界面触发更新，L010 明确提到工作使用受影响。不同送达与展示症状按通知任务归并，不认定具有同一根因，也不把全部成员都限定为后台故障。",
         reason="多成员中重复最多；高严重程度 4/5，涉及消息及时获取这一核心场景。另一产品也有通知送达反馈，不构成独有问题。",
         competitor="在当前样本中，企业微信 W008、W021 同样报告通知收不到（2 条）；飞书该 Issue 为 5 条。飞书通知模块另有音量反馈，共 6/31；企业微信通知模块为 2/32。不能将样本差异解释为实际送达率差异。",
         competitor_ids=["W008", "W021"],
         solutions=["产品方案建议：按消息生成、推送、设备接收、通知展示拆分排查送达链路，先复现已有设备与省电场景。", "产品方案建议：提供可理解的通知诊断与状态提示，避免仅让用户重复修改系统设置。"],
         metrics=["分设备/后台状态的消息通知送达率", "通知端到端延迟分位数", "通知相关负反馈占问题反馈比例"],
         review_note="逐条核对 L002/L003/L010/L016/L038；排除 L015 音量问题；保留送达与展示子症状，不推断根因。"),
    dict(issue_id="ISS-138b4f4a6e20", title="登录或注册验证码未送达", priority="高",
         summary="L004 被登出后收不到验证码；L028 注册时收不到验证码，明确提及邮箱和手机号渠道。",
         problem="用户在恢复登录或注册时无法取得验证码，账号进入流程受阻。两条证据都指向验证码获取这一任务；不据此判断短信或邮件服务的具体故障原因。",
         reason="重复 2 条且高严重程度 2/2，直接阻断账号进入。虽然证据少于两个 3 条簇，但核心场景与影响更明确，因此列第 2。",
         competitor="在当前样本中，企业微信账号模块有 7 条、飞书有 5 条；企业微信 W001/W035 主要体现登录途径或验证选项障碍，并非相同的验证码送达失败。当前样本不足以形成明确竞品差异判断。",
         competitor_ids=["W001", "W035"],
         solutions=["产品方案建议：为验证码发送与到达失败提供可追踪状态、清晰错误提示及受限重发入口。", "产品方案建议：在符合账号安全要求的前提下提供可用的备用验证渠道。"],
         metrics=["验证码请求成功率与到达率（分渠道）", "验证码验证完成率", "登录/注册完成率", "验证码相关投诉数量"],
         review_note="核对 L004/L028 原文，标题同时覆盖登录和注册；L017 为相似独立 Issue，仅作背景，不加入本簇计数。"),
    dict(issue_id="ISS-6b4345525155", title="安装更新附加文件下载失败或循环提示", priority="高",
         summary="W003 反复弹出额外文件下载提示；W006 下载失败后无法打开，重装后恢复；W007 下载显示 0% 后回到提示且重装无效。",
         problem="用户在安装后首次启动或更新后，需要下载附加文件，但流程失败或反复回到下载提示。W006、W007 明确说明应用无法打开或使用。保留各自恢复情况，不把所有成员统一解释为网络故障。",
         reason="重复 3 条，高严重程度 1/3；附加文件流程直接关系应用能否进入。企业微信安装模块集中出现，飞书同模块当前为 0 条，但不代表没有缺陷。",
         competitor="在当前样本中，企业微信安装/更新为 6/32，飞书为 0/31；该簇 3 条。W005/W017/W023 是同模块其他独立 Issue；W009 虽有类似下载描述，但预测模块为性能/稳定性，不移入此簇。",
         competitor_ids=[],
         solutions=["产品方案建议：明确附加资源下载状态与失败原因，提供可恢复的重试入口而非循环提示。", "产品方案建议：检查安装包、资源版本与更新路径的一致性，覆盖已有失败安装场景回归。"],
         metrics=["附加资源下载完成率", "安装/更新后首次启动成功率", "循环下载提示发生率", "相关投诉数量"],
         review_note="核对 W003/W006/W007 原文；按附加资源下载任务归并，区分循环、失败和重装恢复差异；不扩大到其他更新问题。"),
    dict(issue_id="ISS-ab72710c8df7", title="界面语言无法切换或选择后不生效", priority="中",
         summary="W024 无法切换语言以配置和工作；W027 界面变成中文后无法切回；W030 选择英语仍显示中文，并描述注册未完成。",
         problem="需要非中文界面的用户在设置语言或恢复界面语言时遇到障碍，影响理解界面和完成操作。证据支持语言切换任务异常，而不是所有翻译内容质量差。",
         reason="重复 3 条，高严重程度 1/3；部分评论影响配置或注册，但其余影响范围不充分。暂列中，保留 W030 的高影响个案，不因多语言措辞自动升级。",
         competitor="在当前样本中，企业微信国际化/翻译模块 4/32，其中该簇 3 条；飞书预测主模块为国际化/翻译的有 0 条。但 L037 原文也提及翻译不佳，未作为主模块计数，因此不能声称飞书不存在语言问题。",
         competitor_ids=["L037"],
         solutions=["产品方案建议：统一语言选择、保存与重新加载逻辑，核验语言切换确实生效。", "产品方案建议：为无法理解当前语言的用户提供容易识别的语言恢复入口，并回归注册路径。"],
         metrics=["语言切换成功率", "切换后语言一致性错误率", "相关语言用户注册完成率", "语言设置相关投诉数量"],
         review_note="核对 W024/W027/W030 原文，标题涵盖切换及生效问题；不与 W022 翻译覆盖不足混为同一问题。"),
    dict(issue_id="ISS-568ec73ccc90", title="换机登录陷入旧设备验证循环", priority="高",
         summary="W015 描述更换手机并重置旧手机后，新手机要求旧手机验证，旧手机重装登录也要求在自身验证，无法完成登录。",
         problem="用户更换设备并重置旧设备后，登录验证依赖已经不可用的旧会话；尝试恢复旧设备登录仍陷入验证要求。仅有一条评论，不推断此流程对所有用户均不可恢复。",
         reason="单条高严重程度 1/1，直接影响登录与换机恢复，作为只有 4 个多成员 Issue 时的第 5 个补充。没有重复性证据，排序低于所有多成员候选；需先复现确认。",
         competitor="在当前样本中，飞书 L004/L012/L021 也涉及账号进入失败，但没有相同的换机旧设备验证循环证据。当前样本不足以形成明确竞品差异判断。",
         competitor_ids=["L004", "L012", "L021"],
         solutions=["产品方案建议：在符合账号安全要求下，提供不依赖旧设备已登录会话的账号恢复路径。", "产品方案建议：对换机、旧设备重置与重新安装组合做验证流程回归，并提示可用恢复入口。"],
         metrics=["换机账号恢复完成率", "重复验证循环发生率", "账号恢复完成耗时", "换机验证相关投诉数量"],
         review_note="逐句核对 W015 越南语原文与已保存痛点；仅 1 条原始评论，不能提供或虚构 2–3 条独立证据。"),
]


def load_e1(directory):
    """JSON 为权威证据；其余四份输入做一致性校验并记录 hash。"""
    directory = Path(directory)
    names = ["issue_clusters.json", "issue_clusters.csv", "feedback_mapping.csv", "summary.csv", "review.json"]
    hashes = {name: sha256((directory / name).read_bytes()).hexdigest() for name in names}
    package = json.loads((directory / names[0]).read_text(encoding="utf-8"))
    review = json.loads((directory / "review.json").read_text(encoding="utf-8"))
    issues = package["issues"]
    issue_map = {i["issue_id"]: i for i in issues}
    rows = package["feedback_mapping"]
    lookup = {r["feedback_id"]: r for r in rows}
    if len(issue_map) != len(issues) or len(lookup) != len(rows):
        raise ValueError("E1 存在重复 ID")
    seen = set()
    for issue in issues:
        ids = issue["evidence_ids"]
        if len(ids) != len(set(ids)) or seen.intersection(ids) or len(ids) != issue["evidence_count"]:
            raise ValueError("Issue 证据重复或计数不符")
        members = [lookup[i] for i in ids]
        if any(not eligible(r) or not r["eligible"] or r["issue_id"] != issue["issue_id"] for r in members):
            raise ValueError("Issue 成员不符合准入条件或映射不一致")
        if {(r["product"], r["product_module"]) for r in members} != {(issue["product"], issue["product_module"])}:
            raise ValueError("跨产品/模块")
        for field, key in [("feedback_type_distribution", "feedback_type"), ("severity_distribution", "severity")]:
            if issue[field] != dict(Counter(r[key] for r in members)):
                raise ValueError("Issue 分布不符")
        for rep in issue["representative_comments"]:
            if rep["feedback_id"] not in ids or rep["comment"] != lookup[rep["feedback_id"]]["comment"]:
                raise ValueError("代表评论与原文不一致")
        seen.update(ids)
    selected = [r for r in rows if eligible(r)]
    if seen != {r["feedback_id"] for r in selected} or any(r["eligible"] != eligible(r) for r in rows):
        raise ValueError("E1 证据覆盖异常")
    if len(rows) != 82 or len(selected) != 63 or len(issues) != 54:
        raise ValueError("本次 E2 固定输入范围已变化，请重新复核编辑决策")
    if Counter(r["product"] for r in selected) != {"飞书/Lark": 31, "企业微信/WeCom": 32}:
        raise ValueError("产品样本范围变化")
    for name, records, id_field in [("issue_clusters.csv", issues, "issue_id"), ("feedback_mapping.csv", rows, "feedback_id")]:
        frame = pd.read_csv(directory / name, keep_default_na=False)
        if len(frame) != len(records) or set(frame[id_field]) != {r[id_field] for r in records}:
            raise ValueError(f"{name} 与 JSON 不一致")
        expected = {r[id_field]: r for r in records}
        for record in frame.to_dict("records"):
            for key, value in record.items():
                original = expected[record[id_field]][key]
                if isinstance(original, (list, dict)):
                    equal = json.loads(value) == original
                else:
                    escaped = "'" + original if isinstance(original, str) and original.lstrip().startswith(("=", "+", "-", "@")) else original
                    equal = str(value) == str(escaped)
                if not equal:
                    raise ValueError(f"{name} 的 {key} 与 JSON 不一致")
    for record in pd.read_csv(directory / "summary.csv", keep_default_na=False).to_dict("records"):
        original = package["summary"][record["metric"]]
        if (json.loads(record["value"]) if isinstance(original, dict) else str(record["value"])) != (original if isinstance(original, dict) else str(original)):
            raise ValueError("Summary 不一致")
    if len(review["issues"]) != len(issues) or len({i["issue_id"] for i in review["issues"]}) != len(issues):
        raise ValueError("Review ID 不一致")
    for item in review["issues"]:
        if any(item[k] != issue_map[item["issue_id"]][k] for k in item):
            raise ValueError("Review 已变化，需重新核对 E2 决策")
    return package, hashes


def product_comparison(rows):
    selected = [r for r in rows if eligible(r)]
    totals = Counter(r["product"] for r in selected)
    modules = sorted({r["product_module"] for r in selected})
    result = []
    for product in sorted(totals):
        for module in modules:
            members = [r for r in selected if (r["product"], r["product_module"]) == (product, module)]
            result.append(dict(product=product, product_module=module, issue_feedback_count=len(members),
                               percentage=100 * len(members) / totals[product],
                               high_severity_count=sum(r["severity"] == "高" for r in members),
                               bug_count=sum(r["feedback_type"] == "Bug" for r in members),
                               usage_difficulty_count=sum(r["feedback_type"] == "使用困难" for r in members),
                               feature_request_count=sum(r["feedback_type"] == "功能建议" for r in members)))
    return pd.DataFrame(result, columns=["product", "product_module", "issue_feedback_count", "percentage",
                                        "high_severity_count", "bug_count", "usage_difficulty_count", "feature_request_count"])


def top_cards(package):
    lookup = {r["feedback_id"]: r for r in package["feedback_mapping"]}
    issues = {i["issue_id"]: i for i in package["issues"]}
    cards = []
    for rank, decision in enumerate(DECISIONS, 1):
        issue = issues[decision["issue_id"]]
        members = [lookup[i] for i in issue["evidence_ids"]]
        card = {**issue, "rank": rank, "issue_title": decision["title"], "issue_summary": decision["summary"],
                "problem": decision["problem"], "priority": decision["priority"], "priority_reason": decision["reason"],
                "competitor_summary": decision["competitor"], "competitor_evidence_ids": decision["competitor_ids"],
                "proposed_solution": decision["solutions"], "validation_metrics": decision["metrics"],
                "review_status": "evidence_checked_by_assistant", "review_note": decision["review_note"],
                "evidence": members}
        # 不改变原 E1 成员；只在 E2 编辑标题、摘要与建议。
        if len(members) != len(set(issue["evidence_ids"])) or not all(eligible(r) for r in members):
            raise ValueError("Top Issue 证据异常")
        if {(r["product"], r["product_module"]) for r in members} != {(card["product"], card["product_module"])}:
            raise ValueError("Top Issue 跨组")
        for other_id in decision["competitor_ids"]:
            if other_id not in lookup or not eligible(lookup[other_id]):
                raise ValueError("竞品比较证据缺失")
        cards.append(card)
    return cards


def md_table(headers, records):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", "<br>")
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"] +
                     ["| " + " | ".join(cell(v) for v in row) + " |" for row in records])


def render_report(package, comparison, cards, ledger):
    rows, issues = package["feedback_mapping"], package["issues"]
    selected = [r for r in rows if eligible(r)]
    parts = ["# Product Insights & Requirement Prioritization", "## 1. Executive Summary",
             "在当前样本中，82 条原始反馈经既有 V3 预测筛出 63 条问题反馈（飞书 31、企业微信 32），E1 保留 54 个候选 Issue。4 个多成员候选经本轮证据快审可保留为任务/症状层 Issue；Top 5 补充 1 个核心登录障碍单例。不能据此推断市场普遍性或共同技术根因。",
             "在当前样本中，飞书问题集中于通知、账号与会议；企业微信集中于账号、稳定性与安装更新。建议优先验证通知送达、验证码获取和附加文件下载链路；语言切换与换机恢复分别进入定向验证。",
             "## 2. Dataset Scope",
             "只读取 `../e1_offline/` 的完整 JSON、Issue CSV、Feedback Mapping、Summary 与 review.json，并交叉校验。沿用历史 V3 Dev + Holdout 预测，不使用 Gold 修正归类，不混入 r3，不调用模型、不重新聚类。原始输入 82、纳入 63、排除 19（正向 6、情绪/无效 13）；24 个产品/模块组。",
             "计数单位是反馈，不是用户数、故障次数或市场发生率；模块占比的分母分别为飞书 31 与企业微信 32。product_comparison.csv 的 percentage 单位为 0–100。未观察到某模块主问题记 0，不表示该产品不存在此类问题。",
             f"快审范围：多成员、高严重程度、各产品 Top 3 高频模块及 Top 5 候选的并集，共 {sum(i['screened'] for i in ledger)} 个 Issue；仅 Top 5 做逐条原文核对，不宣称全部深度人工复核。4 个多成员保留，未增删或移动任何成员。54 个 E1 review 状态保持原样；本轮独立记录为 assistant 证据快审，不冒充产品负责人签核。",
             "代表场景仅从原文解释；E1 未保存用户场景字段，不补造结构化场景。证据链：Top Issue → evidence_ids → [E1 Feedback Mapping](../e1_offline/feedback_mapping.csv) → 原始评论。另见本目录 evidence.csv 和 review_audit.json（源文件 SHA-256）。"]
    for number, product in [(3, "飞书/Lark"), (4, "企业微信/WeCom")]:
        members = [r for r in selected if r["product"] == product]
        subset = comparison[(comparison["product"] == product) & (comparison["issue_feedback_count"] > 0)].sort_values(["issue_feedback_count", "product_module"], ascending=[False, True])
        parts += [f"## {number}. {product} Problem Profile",
                  f"在当前样本中，问题反馈 {len(members)} 条；类型分布 {dict(Counter(r['feedback_type'] for r in members))}；预测严重程度 {dict(Counter(r['severity'] for r in members))}。",
                  md_table(["模块", "反馈数", "模块占比", "高严重程度", "Bug", "使用困难", "功能建议"],
                           [[r.product_module, r.issue_feedback_count, f"{r.percentage:.2f}%", r.high_severity_count, r.bug_count, r.usage_difficulty_count, r.feature_request_count] for r in subset.itertuples()]),
                  "在当前样本中，高频 Issue（多成员）如下：",
                  md_table(["Issue ID", "原 E1 标题", "证据数", "IDs"], [[i["issue_id"], i["issue_title"], i["evidence_count"], ", ".join(i["evidence_ids"])] for i in issues if i["product"] == product and i["evidence_count"] >= 2]),
                  "在当前样本中，包含预测高严重程度反馈的候选如下（单例不等于高发生率）：",
                  md_table(["Issue ID", "标题", "高/总数", "IDs"], [[i["issue_id"], i["issue_title"], f"{i['severity_distribution'].get('高', 0)}/{i['evidence_count']}", ", ".join(i["evidence_ids"])] for i in issues if i["product"] == product and i["severity_distribution"].get("高", 0)])]
    parts += ["## 5. Competitor Comparison",
              "- 在当前样本中，飞书通知为 6/31（19.35%），企业微信为 2/32（6.25%）；两边均有消息收不到，证据为 L002/L003/L010/L016/L038 与 W008/W021，不能据此计算真实送达率。",
              "- 在当前样本中，企业微信性能/稳定性为 7/32（21.88%）、安装/更新为 6/32（18.75%）；飞书分别为 2/31（6.45%）、0/31。企业微信更集中于无法进入、下载/更新障碍，飞书两条稳定性主问题为网络错误和卡顿。W009 的下载痛点仍保留原预测性能模块，不跨模块修正。",
              "- 在当前样本中，双方账号进入均存在障碍：飞书 5/31（16.13%）、企业微信 7/32（21.88%）；飞书包含验证码未到，企业微信包含旧设备验证循环、验证选项与注册入口问题。飞书会议有 4 条，企业微信该主模块为 0；四条会议诉求不同，不拼成一个 Issue。",
              "- 在当前样本中，企业微信国际化/翻译有 4 条，其中语言切换簇为 3 条；飞书该预测主模块为 0。但 L037 原文涉及翻译，因此这是主问题分布差异，不是语言能力优劣排名。",
              "在当前样本中，预测高严重程度为飞书 10/31（32.26%）、企业微信 19/32（59.38%）。飞书账号 5/5、通知 4/6，企业微信账号 4/7、安装 4/6、稳定性 4/7 含较多高严重程度反馈；风控 2/2 仅为两个单例。类型层面高严重程度在两边主要来自 Bug。以上为原模型标签，不是重新判级，不构成显著性或总体质量结论。",
              "## 6. Top 5 Issues",
              "选择规则：先保留全部 4 个已核对多成员 Issue，按重复证据、原预测严重程度、核心任务与竞品背景进行编辑排序，不计算总分。验证码因 2/2 高且直接阻断账号进入优先于其他 3 条簇。第 5 个为核心场景高严重程度单例补充。排序与优先级标签不等价于跨团队统一排期。"]
    for card in cards:
        parts += [f"### {card['rank']}. {card['issue_title']}",
                  f"Issue：`{card['issue_id']}`；产品：{card['product']}；模块：{card['product_module']}。",
                  f"Evidence：{card['evidence_count']} 条；IDs：{', '.join(card['evidence_ids'])}。反馈类型：{card['feedback_type_distribution']}；严重程度：{card['severity_distribution']}。",
                  "代表原始评论（完整引用，不翻译替换原文）："]
        for rep in card["representative_comments"]:
            parts += [f"- {rep['feedback_id']}：{rep['comment']}"]
        if card["evidence_count"] == 1:
            parts += ["该 Issue 仅 1 条证据，因此只展示 1 条，不能虚构 2–3 条代表评论。"]
        parts += [f"Issue Summary：{card['issue_summary']}", f"Problem：{card['problem']}",
                  f"Priority：**{card['priority']}**。{card['priority_reason']}",
                  f"Competitor Comparison：{card['competitor_summary']}",
                  "Proposed Solution（以下均为产品方案建议，不是用户反馈事实）：",
                  *[f"- {s}" for s in card["proposed_solution"]],
                  "Validation Metrics（未来上线/验证时监测，目前没有基线或目标数值）：",
                  *[f"- {m}" for m in card["validation_metrics"]], f"证据快审备注：{card['review_note']}"]
    parts += ["## 7. Product Recommendations",
              "先对高优先级的通知、验证码与附加文件链路做复现和原因定位，再按确认的故障点交付小范围修复；换机恢复是高影响单例，先验证安全约束与可复现性，不直接据此大改账号体系。语言切换按中优先级验证设置与注册路径。所有方案均是产品推断，尚未得到工程可行性、用户需求或收益验证。",
              "验证指标应先建立埋点口径与基线，再在上线后观察对应任务成功率、错误率和相关投诉；本报告没有当前送达率、登录率或改善幅度，也未估计收入、留存、DAU、成本或 RICE。",
              "可用于项目案例的事实表述：基于 82 条已有结构化反馈，完成 63 条问题反馈的双产品模块画像与证据链分析；从 54 个离线候选中核对 4 个多成员 Issue 并形成 5 张需求建议卡，定义后续验证指标。本阶段零模型调用；不宣称已上线或带来业务提升。",
              "## 8. Limitations",
              "- 便利样本小、缺少时间/设备/地域等均衡采样，评论数不是用户数；所有产品结论仅限当前样本。未观测到不等于不存在。",
              "- 模块、类型、有效性、严重程度继承 V3 预测，未用 Gold 纠正。严重程度仍可能判断不准，产品优先级是本轮编辑建议，不等于模型严重程度。",
              "- 4 个多成员簇只确认任务/症状一致，不确认技术根因；通知簇包括送达与展示子症状，附加文件簇包括不同失败表现。未重新聚类，仍有漏归并，例如 L017 与验证码簇相似、W009 与附加文件描述相似但模块不同。",
              "- 多问题评论仅保留原 E1 主 Issue；正向和无效评论未进入问题统计，不能推导满意度。部分竞品背景来自次要诉求（L037），不会额外增加模块计数。",
              "- Top 5 为 assistant 离线证据快审，并非外部人工签核；其余 Issue 仅筛查或未查，E1 全部 needs_review 状态未改变。多语言解读需母语/产品人员复核；用户报告（尤其隐私、封禁、数据恢复）不等于技术事实。",
              "- 缺少真实日志、影响用户数、发生率及业务数据，单例高影响项需先复现；解决方案与未来指标未经上线验证。Holdout 已用于既有评测并被查看，本轮产品解读不产生新的泛化成绩。",
              "- 为快速收尾仅交付 CSV、Markdown 与审计 JSON，跳过可选 XLSX；无新 UI、Evaluation、API 或下一 Phase。"]
    return "\n\n".join(parts) + "\n"


def build(source, output):
    package, hashes = load_e1(source)
    comparison = product_comparison(package["feedback_mapping"])
    cards = top_cards(package)
    frequent = {}
    for product, group in comparison.groupby("product"):
        threshold = group["issue_feedback_count"].nlargest(3).min()
        frequent[product] = set(group.loc[group["issue_feedback_count"] >= threshold, "product_module"])
    selected = {c["issue_id"]: c for c in cards}
    ledger = []
    for issue in package["issues"]:
        screened = issue["evidence_count"] >= 2 or issue["severity_distribution"].get("高", 0) > 0 or issue["product_module"] in frequent[issue["product"]] or issue["issue_id"] in selected
        ledger.append(dict(issue_id=issue["issue_id"], evidence_ids=issue["evidence_ids"], screened=screened,
                           source_review_status=issue["review_status"],
                           e2_review_status="evidence_checked_by_assistant" if issue["issue_id"] in selected else ("screened_only" if screened else "needs_review"),
                           review_note=selected[issue["issue_id"]]["review_note"] if issue["issue_id"] in selected else "未做完整逐条证据签核"))
    report = render_report(package, comparison, cards, ledger)
    export_insights(package, comparison, cards, report, output,
                    dict(source_directory=str(Path(source).resolve()), source_sha256=hashes, model_api_calls=0,
                         review_method="assistant_offline_evidence_check_not_human_signoff", issue_review=ledger, top_issue_cards=cards))
    return comparison, cards


def export_insights(package, comparison, cards, report, output, audit):
    """历史编辑报告和新批次共用导出格式，拒绝覆盖已有目录。"""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "product_insights.md").write_text(report, encoding="utf-8")
    columns = ["rank", "issue_id", "product", "product_module", "issue_title", "evidence_count", "evidence_ids", "priority", "priority_reason", "competitor_summary", "proposed_solution", "validation_metrics", "feedback_type_distribution", "severity_distribution"]
    def safe(value):
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
    pd.DataFrame(cards, columns=columns).map(safe).to_csv(output / "top_issues.csv", index=False, encoding="utf-8-sig")
    comparison.map(safe).to_csv(output / "product_comparison.csv", index=False, encoding="utf-8-sig", float_format="%.8f")
    evidence = [{"issue_id": c["issue_id"], **r} for c in cards for r in c["evidence"]]
    evidence_frame = pd.DataFrame(evidence) if evidence else pd.DataFrame(columns=["issue_id", "feedback_id", "product", "product_module", "comment"])
    evidence_frame.map(safe).to_csv(output / "evidence.csv", index=False, encoding="utf-8-sig")
    (output / "review_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def build_for_batch(package, output):
    """复用 E2 统计/导出；新批次不使用历史 DECISIONS，不宣称已审阅。"""
    from src.issue_clustering import apply_review, review_template
    rows = package["feedback_mapping"]
    apply_review(rows, review_template(package["issues"]))
    comparison = product_comparison(rows)
    lookup = {r["feedback_id"]: r for r in rows}
    core = {"账号/登录/验证", "聊天/消息", "通知", "文件/分享", "文档/编辑", "性能/稳定性"}
    ordered = sorted(package["issues"], key=lambda i: (
        -int(i["evidence_count"] >= 2), -i["evidence_count"],
        -i["severity_distribution"].get("高", 0), -int(i["product_module"] in core), i["issue_id"]))
    cards = []
    for rank, issue in enumerate(ordered[:5], 1):
        high = issue["severity_distribution"].get("高", 0)
        others = comparison[(comparison["product"] != issue["product"]) &
                            (comparison["product_module"] == issue["product_module"])]
        competitor = "当前批次不足以形成明确竞品差异判断。"
        if not others.empty:
            competitor = "在当前批次中，其他产品同模块的问题反馈数为：" + "、".join(
                f"{r.product} {r.issue_feedback_count}" for r in others.itertuples()) + "。模块相同不证明属于同一 Issue，也不代表总体质量差异。"
        priority = "高" if high and issue["product_module"] in core else ("中" if high or issue["evidence_count"] >= 2 else "低")
        cards.append({**issue, "rank": rank, "problem": issue["issue_summary"], "priority": priority,
                      "priority_reason": f"当前批次证据 {issue['evidence_count']} 条，预测高严重程度 {high} 条；"
                                         f"{'属于' if issue['product_module'] in core else '未列入本次'}核心场景。规则候选，尚需复核。",
                      "competitor_summary": competitor,
                      "proposed_solution": ["产品方案建议：先按原评论复现该任务/症状，定位原因后再确定修复或交互改进；不预设根因。"],
                      "validation_metrics": ["相关任务完成率（需先定义任务和采集基线）", "同类问题反馈数量及占比（保持来源可比）"],
                      "evidence": [lookup[i] for i in issue["evidence_ids"]]})
    parts = ["# Product Insights — User Batch", "仅基于当前批次；自动规则候选，未经过完整人工 Issue 复核。",
             f"输入 {len(rows)} 条，问题反馈 {sum(eligible(r) for r in rows)} 条，Issue {len(package['issues'])} 个。",
             "## Product Problem Profile", md_table(comparison.columns.tolist(), comparison.values.tolist()),
             "## Top Issues", "排序：多成员优先，然后证据数、预测高严重程度数、核心场景；不是商业价值评分。"]
    for card in cards:
        parts += [f"### {card['rank']}. {card['issue_title']}",
                  f"{card['product']} / {card['product_module']}；IDs：{', '.join(card['evidence_ids'])}",
                  card["problem"], card["priority_reason"], card["competitor_summary"],
                  *card["proposed_solution"], "未来验证指标（无当前值）：" + "；".join(card["validation_metrics"])]
    parts += ["## Limitations", "当前批次独立于项目历史82条；Mock 仅演示流程，不代表模型质量。建议为待验证产品推断，不是评论事实。"]
    export_insights(package, comparison, cards, "\n\n".join(parts) + "\n", output,
                    dict(model_api_calls=0, review_method="automatic_candidates_needs_review", top_issue_cards=cards))
    # 原值 JSON 用于页面；CSV 可转义公式字符，不能反向参与业务比较。
    (Path(output) / "product_comparison.json").write_text(comparison.to_json(orient="records", force_ascii=False), encoding="utf-8")
    return comparison, cards


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "analysis/results/e1_offline")
    parser.add_argument("--output", type=Path, default=ROOT / "analysis/results/e2_product_insights")
    args = parser.parse_args()
    result, cards = build(args.source, args.output)
    print(f"问题反馈 {result.issue_feedback_count.sum()} 条；Top {len(cards)}；模型请求 0；输出 {args.output}")
