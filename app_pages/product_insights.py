"""完全展示 E2 已有成果，不生成新业务建议。"""

import streamlit as st

from app import get_result, page_intro, show_dataframe
from src.workbench_data import E1, E2, OfflineResultError, issue_evidence, markdown_section

page_intro("Product Insights", "这一页解决：哪些问题值得优先验证，以及用户证据如何进入产品需求？")
from src.user_runs import list_batches
from src.user_run_ui import show_user_insights

st.subheader("Analysis Run")
runs = {r["batch_id"]: r for r in list_batches() if r["issue_analysis_status"] == "completed"}
selected_run = st.selectbox("数据来源", ["Project Demo / Original E2"] + list(runs), key="insights_analysis_run")
if selected_run != "Project Demo / Original E2":
    show_user_insights(selected_run)
    st.stop()

st.subheader("Product Problem Profile")
comparison = get_result(f"{E2}/product_comparison.csv", ["product", "product_module", "issue_feedback_count", "percentage", "high_severity_count", "bug_count", "usage_difficulty_count", "feature_request_count"])
if comparison is not None:
    for col, product in zip(st.columns(2), ["飞书/Lark", "企业微信/WeCom"]):
        with col:
            st.markdown(f"#### {product}")
            subset = comparison[(comparison["product"] == product) & (comparison["issue_feedback_count"] > 0)].copy()
            subset = subset.sort_values("issue_feedback_count", ascending=False, kind="stable")
            subset["percentage"] = subset["percentage"].map(lambda x: f"{float(x):.2f}%")
            show_dataframe(subset.drop(columns="product").rename(columns={"product_module": "模块", "issue_feedback_count": "问题反馈数", "percentage": "占比", "high_severity_count": "高严重程度数", "bug_count": "Bug", "usage_difficulty_count": "使用困难", "feature_request_count": "功能建议"}), hide_index=True, width="stretch", alt=f"{product} 当前样本问题分布")
    st.caption("计数是问题反馈条数，不是用户数或故障率；分母分别为飞书 31、企业微信 32。")

st.subheader("Competitor Comparison")
report = get_result(f"{E2}/product_insights.md")
if report is not None:
    content = markdown_section(report, "5. Competitor Comparison")
    if content:
        st.markdown(content)
    else:
        st.info("已有报告未找到竞品对比段落，不补造结论。")

st.subheader("Top 5 Product Issues")
audit = get_result(f"{E2}/review_audit.json", ["top_issue_cards"])
package = get_result(f"{E1}/issue_clusters.json", ["issues", "feedback_mapping"])
if audit is not None and audit["top_issue_cards"]:
    cards = {card["issue_id"]: card for card in audit["top_issue_cards"]}
    chosen = st.selectbox("选择 Top Issue", list(cards), format_func=lambda key: f"{cards[key]['rank']}. {cards[key]['issue_title']}", key="top_issue_id")
    card = cards[chosen]
    st.markdown(f"### {card['issue_title']}")
    st.caption(f"{card['issue_id']} · {card['product']} · {card['product_module']} · E2 assistant 离线证据快审，非产品负责人签核")
    st.markdown("#### Problem")
    st.write(card["issue_summary"])
    st.write(card["problem"])
    st.markdown("#### Evidence")
    st.write(f"Evidence Count：{card['evidence_count']} · IDs：{', '.join(card['evidence_ids'])}")
    for rep in card["representative_comments"]:
        with st.container(border=True):
            st.markdown(f"**{rep['feedback_id']}**")
            st.text(rep["comment"])
    if card["evidence_count"] == 1:
        st.caption("只有 1 条原始证据，不补造第 2–3 条代表评论。")
    if package is not None:
        try:
            members = issue_evidence(package, chosen)
            if [r["feedback_id"] for r in members] != card["evidence_ids"]:
                raise OfflineResultError("E1/E2 证据列表不一致，请检查存档。")
            with st.expander(f"查看全部 {len(members)} 条原始证据"):
                for row in members:
                    st.markdown(f"**{row['feedback_id']}**")
                    st.text(row["comment"])
        except OfflineResultError as error:
            st.warning(str(error))
    st.markdown("#### Affected Scenario")
    st.write("场景依据 E2 已有 Problem 描述：" + card["problem"])
    st.caption("未保存可靠的独立场景字段，不新增用户画像。")
    st.markdown(f"#### Priority：{card['priority']}")
    st.write(card["priority_reason"])
    st.markdown("#### Competitor Comparison")
    st.write(card["competitor_summary"])
    st.warning("产品方案建议（非用户原始事实）")
    for solution in card["proposed_solution"]:
        st.markdown("- " + solution)
    st.markdown("#### Validation Metrics")
    st.caption("仅定义未来应监测的指标，尚无当前值、目标值或上线收益。")
    for metric in card["validation_metrics"]:
        st.markdown("- " + metric)
elif audit is not None:
    st.info("E2 暂无产品卡片。")
