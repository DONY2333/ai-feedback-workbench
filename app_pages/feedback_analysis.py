"""已有 V3 预测与原文证据的双向浏览，不触发在线分析。"""

import pandas as pd
import streamlit as st

from app import get_result, page_intro, show_dataframe, show_batch_analysis
from src.workbench_data import E1, E2, OfflineResultError, issue_evidence

page_intro("Feedback Analysis", "这一页解决：AI 结论来自哪条评论，Issue 又由哪些原始反馈支持？")
from src.user_run_ui import show_import_workflow

show_import_workflow()
st.divider()
st.subheader("Project Demo / Original Feedback")
package = get_result(f"{E1}/issue_clusters.json", ["issues", "feedback_mapping"])
audit = get_result(f"{E2}/review_audit.json", ["top_issue_cards"])

if package is not None:
    issues = {i["issue_id"]: dict(i) for i in package["issues"]}
    if audit is not None:
        for card in audit["top_issue_cards"]:
            original = issues.get(card["issue_id"])
            if original and original["evidence_ids"] == card["evidence_ids"]:
                original["issue_title"] = card["issue_title"]  # 仅显示 E2 已有标题，不改成员。
    frame = pd.DataFrame(package["feedback_mapping"])
    if frame.empty:
        st.info("没有已保存的历史反馈。")
    else:
        st.subheader("Feedback Table")
        filtered = frame.copy()
        filters = [("product", "产品"), ("product_module", "产品模块"), ("feedback_type", "反馈类型"), ("severity", "严重程度"), ("issue_id", "Issue")]
        for col, (field, label) in zip(st.columns(5), filters):
            options = sorted(frame[field].dropna().unique().tolist())
            options = [x for x in options if x]
            choice = col.selectbox(label, ["全部"] + options, key=f"feedback_filter_{field}")
            if choice != "全部":
                filtered = filtered[filtered[field] == choice]
        visible = ["feedback_id", "product", "comment", "valid", "feedback_type", "product_module", "severity", "core_user_pain", "issue_id"]
        labels = dict(zip(visible, ["ID", "产品", "原始评论", "是否有效", "反馈类型", "产品模块", "严重程度", "核心痛点", "所属Issue"]))
        st.caption(f"筛选后 {len(filtered)} / {len(frame)} 条 · 历史 V3 预测；未用 Gold 修正")
        show_dataframe(filtered[visible].rename(columns=labels), hide_index=True, width="stretch", alt="历史 V3 反馈与 Issue 映射")
        st.caption("未保存用户场景，不补造。表格不支持点击时，可使用下方选择器查看完整原文。")
        if not filtered.empty:
            selected = st.selectbox("选择反馈 ID", filtered["feedback_id"].tolist(), key="feedback_detail_id")
            row = filtered.loc[filtered["feedback_id"] == selected].iloc[0]
            st.subheader("Feedback Detail")
            left, right = st.columns([3, 2])
            with left:
                st.markdown("#### Original Feedback")
                st.text(row["comment"])
                st.caption(f"来源运行：{row.get('source_run', '未保存')} · 原 Excel 行：{row.get('original_excel_row', '未保存')}")
            with right:
                st.markdown("#### AI Structured Analysis")
                for field in ["valid", "feedback_type", "product_module", "severity", "core_user_pain"]:
                    st.write(f"{labels[field]}：{row[field]}")
            st.markdown("#### Issue Mapping")
            if row["issue_id"] in issues:
                issue = issues[row["issue_id"]]
                st.write(f"{issue['issue_id']} · {issue['issue_title']} · Evidence Count：{issue['evidence_count']}")
            else:
                st.info("该反馈未进入负向问题Issue归并。")
        else:
            st.info("当前筛选无反馈，请调整条件。")

    st.divider()
    st.subheader("Issue → Evidence")
    if issues:
        issue_id = st.selectbox("选择 Issue，查看全部证据", list(issues), format_func=lambda key: f"{issues[key]['product']} · {issues[key]['issue_title']} ({key})", key="evidence_issue_id")
        issue = issues[issue_id]
        st.write(f"**{issue['issue_title']}**")
        st.caption(f"{issue['product']} · {issue['product_module']} · Evidence Count：{issue['evidence_count']}")
        st.write("Evidence IDs：" + ", ".join(issue["evidence_ids"]))
        try:
            for row in issue_evidence(package, issue_id):
                with st.container(border=True):
                    st.markdown(f"**{row['feedback_id']}** · {row['feedback_type']} · 严重程度 {row['severity']}")
                    st.text(row["comment"])
        except OfflineResultError as error:
            st.warning(str(error))
    else:
        st.info("没有已保存的 Issue。")

st.divider()
if st.toggle("开启可选本地批量分析 / 导出", value=False, key="batch_enabled"):
    show_batch_analysis()
