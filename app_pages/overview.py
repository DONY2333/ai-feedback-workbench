"""30 秒了解数据、发现与质量证据。"""

import streamlit as st

from app import get_result, page_intro, show_dataframe, show_metrics
from src.workbench_data import E1, E2, HOLDOUT, FIELD_LABELS

page_intro("AI User Feedback & Product Insight Workbench", "将真实用户反馈转化为可验证的问题洞察和产品需求。")
st.write("本项目基于飞书/Lark与企业微信公开用户反馈，结合LLM结构化分析、模型评测、Issue Clustering和产品需求提炼。")

package = get_result(f"{E1}/issue_clusters.json", ["summary", "issues", "feedback_mapping"])
top = get_result(f"{E2}/top_issues.csv", ["rank", "product", "product_module", "issue_title", "evidence_count", "priority"])
if package is not None:
    summary = package["summary"]
    by_product = summary.get("eligible_by_product", {})
    show_metrics([
        ("原始反馈", summary.get("input_count", "—")), ("进入问题分析", summary.get("eligible_count", "—")),
        ("飞书/Lark问题反馈", by_product.get("飞书/Lark", "—")), ("企业微信问题反馈", by_product.get("企业微信/WeCom", "—")),
        ("E1候选 Issue", summary.get("issue_count", "—")), ("Top Issues", len(top) if top is not None else "—"),
    ])

st.subheader("Current Sample Key Findings")
comparison = get_result(f"{E2}/product_comparison.csv", ["product", "product_module", "issue_feedback_count", "percentage"])
if comparison is not None:
    totals = comparison.groupby("product")["issue_feedback_count"].sum()
    for product, module in [("飞书/Lark", "通知"), ("企业微信/WeCom", "账号/登录/验证"), ("企业微信/WeCom", "性能/稳定性"), ("企业微信/WeCom", "安装/更新")]:
        records = comparison[(comparison["product"] == product) & (comparison["product_module"] == module)]
        if not records.empty:
            row = records.iloc[0]
            st.markdown(f"- 在当前样本中，{product}的{module}问题为 **{int(row['issue_feedback_count'])}/{int(totals[product])}（{row['percentage']:.2f}%）**。")

st.subheader("Top 5 Issues")
if top is not None:
    show_dataframe(top[["rank", "product", "product_module", "issue_title", "evidence_count", "priority"]].rename(columns={
        "rank": "Rank", "product": "Product", "product_module": "Product Module", "issue_title": "Issue", "evidence_count": "Evidence Count", "priority": "Priority",
    }), hide_index=True, width="stretch", alt="E2 Top 5 产品问题")
    st.caption("在 Product Insights 查看方案与优先级依据，在 Feedback Analysis 查看全部原始证据。")

st.subheader("AI Quality Snapshot · V3 Holdout")
metrics = get_result(f"{HOLDOUT}/metrics_overall.csv", ["field", "accuracy", "n_samples", "correct"])
if metrics is not None:
    show_metrics([(FIELD_LABELS.get(r.field, "All-fields Correct") + " Accuracy" if r.field in FIELD_LABELS else "All-fields Correct", f"{float(r.accuracy):.2%}") for r in metrics.itertuples()])
st.caption("Gold Set：82 · Dev：20 · Holdout：62")
st.info("该模型经过人工Gold Set、Dev/Holdout切分和Prompt V1→V3迭代评测，而非直接将LLM输出视为事实。")
